"""Walk-forward validation: many folds, then the statistics that judge them.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.

Each fold runs the full section 9.4 protocol independently -- its own search, its own variant
selection, its own single test evaluation. Nothing is shared between folds except the price
history, so a fold's test window is never seen by its own search.

What comes out is a *distribution*, plus four statistics that say whether the distribution means
anything: how consistent it was across folds, whether the Sharpe survives deflation for the
number of trials, whether the selection procedure beats choosing at random, and whether the
optimum is a plateau rather than a spike.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import numpy as np

from cracktrade.backtest import buy_and_hold_portfolio, extract_metrics, run_variants
from cracktrade.config import dump_strategy
from cracktrade.domain import FoldResult, Metrics, ValidationReport
from cracktrade.log import get_logger
from cracktrade.optimize.discovery import discover_parameters
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, get_objective
from cracktrade.optimize.runner import SplitOutcome, optimize_split, worst_case_warmup
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from cracktrade.validate.costs import cost_sensitivity
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme, walk_forward_splits
from cracktrade.validate.stability import stability_surface
from cracktrade.validate.statistics import (
    block_bootstrap_interval,
    deflated_sharpe,
    probability_of_backtest_overfitting,
)

if TYPE_CHECKING:
    import numpy.typing as npt

    from cracktrade.config import Strategy
    from cracktrade.data import MarketData
    from cracktrade.optimize.windows import Split

logger = get_logger(__name__)


def walk_forward(
    strategy: Strategy,
    data: MarketData,
    *,
    folds: int = DEFAULT_FOLDS,
    scheme: FoldScheme = FoldScheme.ANCHORED,
    epochs: int = 10,
    seed: int = 0,
    workers: int = 1,
    objective_name: str = DEFAULT_OBJECTIVE,
    train_fraction: float = 0.5,
    min_test_bars: int = 30,
) -> ValidationReport:
    """Optimize and evaluate ``strategy`` across successive walk-forward folds.

    Raises:
        NoOptimizableParametersError: nothing in the strategy is tunable.
        OptimizationError: the history cannot support the requested division.
    """
    parameters = discover_parameters(strategy)
    warmup = worst_case_warmup(strategy, parameters)
    splits = walk_forward_splits(
        data,
        warmup=warmup,
        folds=folds,
        scheme=scheme,
        train_fraction=train_fraction,
        min_test_bars=min_test_bars,
    )

    logger.info("walk-forward: %d %s fold(s) over %d bars", len(splits), scheme.value, len(data))

    started = time.perf_counter()
    outcomes = [
        optimize_split(
            strategy,
            division,
            ticker=data.ticker,
            epochs=epochs,
            seed=seed,
            workers=workers,
            objective_name=objective_name,
        )
        for division in splits
    ]
    elapsed = time.perf_counter() - started

    fold_results = tuple(
        FoldResult(
            index=index,
            train_bars=len(division.train.data),
            test_bars=division.test.scored_bars,
            first_test_bar=division.test.data.index[division.test.offset].date(),
            last_test_bar=division.test.data.index[-1].date(),
            entry_name=outcome.result.entry_name,
            exit_name=outcome.result.exit_name,
            metrics=outcome.result.test_metrics,
            train_metrics=outcome.result.train_metrics,
            parameters={change.path: change.new_value for change in outcome.result.changes},
        )
        for index, (division, outcome) in enumerate(zip(splits, outcomes, strict=True))
    )

    out_of_sample = np.concatenate([outcome.test_returns for outcome in outcomes])
    trials = sum(outcome.result.trials for outcome in outcomes)
    trial_sharpes = _per_period(
        np.array(
            [sharpe for outcome in outcomes for sharpe in outcome.trial_sharpes],
            dtype=np.float64,
        )
    )

    # The most recent fold is the one whose regime the user is about to trade in, so the
    # stability and cost checks are run there rather than averaged across all of them.
    last_split, last_outcome = splits[-1], outcomes[-1]

    benchmark = _out_of_sample_benchmark(strategy, data, splits)

    report = ValidationReport(
        strategy_name=strategy.strategy.name,
        ticker=data.ticker,
        objective=objective_name,
        scheme=scheme.value,
        folds=fold_results,
        benchmark=benchmark,
        optimized_yaml=dump_strategy(last_outcome.pruned),
        deflated=deflated_sharpe(
            out_of_sample,
            trials=trials,
            trial_sharpes=trial_sharpes if trial_sharpes.size else None,
        ),
        overfitting=probability_of_backtest_overfitting(_fold_performance_matrix(outcomes, splits)),
        stability=stability_surface(
            strategy,
            last_outcome.parameters,
            last_outcome.values,
            last_split.train,
            get_objective(objective_name),
        ),
        costs=cost_sensitivity(last_outcome.pruned, last_split.test),
        mean_return_interval=block_bootstrap_interval(out_of_sample, statistic="mean", seed=seed),
        total_return_interval=block_bootstrap_interval(out_of_sample, statistic="total", seed=seed),
        trials=trials,
        seed=seed,
        elapsed_seconds=elapsed,
    )

    _log_verdict(report)
    return report


def _out_of_sample_benchmark(
    strategy: Strategy, data: MarketData, splits: tuple[Split, ...]
) -> Metrics:
    """Buy and hold across the whole out-of-sample span, under the same cost model.

    The folds together form one continuous stretch of unseen history, so the honest comparison
    is holding the ticker over exactly that stretch. Without it the report says how consistent
    the strategy was but not whether consistency was worth anything (audit finding B1).
    """
    first_test_bar = data.index.get_loc(splits[0].test.data.index[splits[0].test.offset])
    assert isinstance(first_test_bar, int)

    span = data.slice(first_test_bar, len(data))
    portfolio = buy_and_hold_portfolio(
        span, strategy.execution, strategy.position_sizing, start_bar=0
    )
    return extract_metrics(portfolio, risk_free_rate=strategy.execution.risk_free_rate)


def _per_period(annualised: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """De-annualise Sharpe ratios for the deflation formula.

    ``Metrics.sharpe_ratio`` is annualised on 252 trading days, but the deflated Sharpe mixes the
    ratio with the *observation count*, so both have to be on the per-bar footing. Feeding it
    annualised trial Sharpes inflates their variance by 252 and the luck threshold by about 16,
    which fails every strategy regardless of merit.
    """
    return annualised / math.sqrt(TRADING_DAYS_PER_YEAR)


def _fold_performance_matrix(
    outcomes: list[SplitOutcome], splits: tuple[Split, ...]
) -> npt.NDArray[np.float64]:
    """Every fold's winning configuration, scored on every fold's test window.

    CSCV needs a slices-by-configurations matrix (spec 12.4). The configurations are the winners
    each fold's own search produced, and the slices are the fold test windows. Evaluating each
    winner everywhere is what exposes a configuration that only worked where it was fitted.
    """
    size = len(outcomes)
    matrix = np.zeros((size, size), dtype=np.float64)

    for column, outcome in enumerate(outcomes):
        for row, division in enumerate(splits):
            simulation = run_variants(outcome.pruned, division.test.data)[0]
            metrics = extract_metrics(
                simulation.portfolio,
                risk_free_rate=outcome.pruned.execution.risk_free_rate,
                offset=division.test.offset,
            )
            matrix[row, column] = metrics.sharpe_ratio

    return matrix


def _log_verdict(report: ValidationReport) -> None:
    """State the conclusion plainly, including when it is negative."""
    logger.info(
        "%s on %s: %d/%d folds profitable, %.1f%% out of sample vs %.1f%% buy-and-hold, "
        "PBO %.2f, deflated Sharpe P=%.3f",
        report.strategy_name,
        report.ticker,
        report.profitable_folds,
        len(report.folds),
        report.combined_return_pct,
        report.benchmark.total_return_pct,
        report.overfitting.probability,
        report.deflated.probability,
    )
    if not report.is_credible:
        logger.warning(
            "%s did not clear the robustness checks: %s",
            report.strategy_name,
            "; ".join(report.failures),
        )
