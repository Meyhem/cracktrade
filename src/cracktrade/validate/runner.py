"""Walk-forward validation: many folds, then the statistics that judge them.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.

Each fold runs the full section 9.4 protocol independently -- its own search, its own single
test evaluation. Nothing is shared between folds except the price history, so a fold's test
window is never seen by its own search.

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

from cracktrade.backtest import (
    Calendar,
    buy_and_hold_portfolio,
    extract_metrics,
    run_simulation,
)
from cracktrade.config import dump_strategy
from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.domain import FoldResult, Metrics, ValidationReport
from cracktrade.history import scope_of
from cracktrade.log import get_logger
from cracktrade.optimize.discovery import discover_parameters
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, TradeFloor
from cracktrade.optimize.runner import SplitOutcome, optimize_split, worst_case_warmup
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
    trade_floor: TradeFloor = DEFAULT_TRADE_FLOOR,
    train_fraction: float = 0.5,
    min_test_bars: int = 30,
    control: RunControl = NO_CONTROL,
    capture_series: bool = False,
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
    outcomes = []
    for index, division in enumerate(splits):
        # Between folds, not inside one: a fold stopped half-way has an unusable search behind
        # it, and cancelling here leaves nothing partially computed.
        control.raise_if_cancelled()
        control.progress(f"fold {index + 1} of {len(splits)}", 100.0 * index / len(splits))
        outcomes.append(
            optimize_split(
                strategy,
                division,
                ticker=data.ticker,
                epochs=epochs,
                seed=seed,
                workers=workers,
                objective_name=objective_name,
                trade_floor=trade_floor,
                # Each fold's search reports within its own share of the whole run, so the
                # percentage advances monotonically rather than restarting per fold.
                control=_fold_control(control, index, len(splits)),
                capture_series=capture_series,
            )
        )
    elapsed = time.perf_counter() - started
    control.progress("computing statistics", 100.0)

    fold_results = tuple(
        FoldResult(
            index=index,
            train_bars=len(division.train.data),
            test_bars=division.test.scored_bars,
            first_test_bar=division.test.data.index[division.test.offset].date(),
            last_test_bar=division.test.data.index[-1].date(),
            metrics=outcome.result.test_metrics,
            train_metrics=outcome.result.train_metrics,
            parameters={change.path: change.new_value for change in outcome.result.changes},
            # Already computed: each fold is evaluated as a full backtest before all but its
            # metrics were discarded. Keeping the list is what lets a fold be looked at rather
            # than only scored -- and it is the only honest per-trade view a walk-forward has,
            # since the folds ran different configurations.
            trades=outcome.result.trades,
        )
        for index, (division, outcome) in enumerate(zip(splits, outcomes, strict=True))
    )

    out_of_sample = np.concatenate([outcome.test_returns for outcome in outcomes])
    trials = sum(outcome.result.trials for outcome in outcomes)
    # De-annualised fold by fold, under the calendar of the train window each Sharpe was
    # scored on. The folds share a venue and an interval, so the calendars agree in practice;
    # measuring per window is what makes that a property of the data rather than a hope.
    trial_sharpes = np.concatenate(
        [
            _per_period(
                np.array(outcome.trial_sharpes, dtype=np.float64),
                Calendar.of(division.train.data),
            )
            for division, outcome in zip(splits, outcomes, strict=True)
        ]
    )

    # The most recent fold is the one whose regime the user is about to trade in, so the
    # stability and cost checks are run there rather than averaged across all of them.
    last_split, last_outcome = splits[-1], outcomes[-1]

    benchmark = _out_of_sample_benchmark(strategy, data, splits)

    report = ValidationReport(
        history=scope_of(data, multi_window=True),
        strategy_name=strategy.strategy.name,
        ticker=data.ticker,
        objective=objective_name,
        scheme=scheme.value,
        folds=fold_results,
        benchmark=benchmark,
        optimized_yaml=dump_strategy(last_outcome.optimized),
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
            # The last fold's own bound objective, floor included. Re-deriving it here from the
            # objective's name would silently drop the floor and score the surface against a
            # different function than the search used.
            last_outcome.objective,
        ),
        costs=cost_sensitivity(last_outcome.optimized, last_split.test),
        mean_return_interval=block_bootstrap_interval(out_of_sample, statistic="mean", seed=seed),
        total_return_interval=block_bootstrap_interval(out_of_sample, statistic="total", seed=seed),
        trials=trials,
        seed=seed,
        elapsed_seconds=elapsed,
        # Per fold, never spliced into one curve. Each fold re-optimizes, so the folds are
        # different strategies; joining their equity curves would draw one nobody traded.
        fold_series=tuple(
            outcome.result.series for outcome in outcomes if outcome.result.series is not None
        ),
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
    calendar = Calendar.of(span)
    portfolio = buy_and_hold_portfolio(
        span, strategy.execution, strategy.position_sizing, start_bar=0, calendar=calendar
    )
    return extract_metrics(
        portfolio, risk_free_rate=strategy.execution.risk_free_rate, calendar=calendar
    )


def _per_period(annualised: npt.NDArray[np.float64], calendar: Calendar) -> npt.NDArray[np.float64]:
    """De-annualise Sharpe ratios for the deflation formula.

    ``Metrics.sharpe_ratio`` is annualised on the run's own calendar, but the deflated
    Sharpe mixes the ratio with the *observation count*, so both have to be on the per-bar
    footing. Feeding it annualised trial Sharpes inflates their variance by the periods
    per year and the luck threshold by its square root, which fails every strategy
    regardless of merit -- and the factor grows from 16 to 65 on 30-minute bars.
    """
    return annualised / math.sqrt(calendar.periods_per_year)


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
            simulation = run_simulation(
                outcome.optimized, division.test.data, scored_from=division.test.offset
            )
            metrics = extract_metrics(
                simulation.portfolio,
                risk_free_rate=outcome.optimized.execution.risk_free_rate,
                calendar=simulation.calendar,
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


def _fold_control(control: RunControl, index: int, folds: int) -> RunControl:
    """Scale one fold's progress into its slice of the whole run.

    Each fold's search reports 0-100% of itself. Passed straight through, the bar would restart
    six times and mean nothing; mapped into ``[index/folds, (index+1)/folds]`` it advances once
    from end to end. Cancellation passes through untouched.
    """
    if control.on_progress is None and control.should_stop is None:
        return control

    def scaled(stage: str, percent: float) -> None:
        control.progress(
            f"fold {index + 1} of {folds} - {stage}", 100.0 * (index + percent / 100.0) / folds
        )

    return RunControl(
        on_progress=scaled if control.on_progress is not None else None,
        should_stop=control.should_stop,
    )
