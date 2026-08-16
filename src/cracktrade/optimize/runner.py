"""The optimization protocol: fit on train, select on train, report on test once.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.4.

The ordering here is the whole point, and it is the fix for defect D2. Every step that *chooses*
something -- the parameter search, and then the entry/exit variant -- happens on the train
window. The test window is touched exactly once, after both choices are final, and its numbers
are the only ones reported as out-of-sample.

Train metrics are reported too, labelled in-sample. The gap between them is the single most
useful overfitting diagnostic available for free, and hiding it would be a disservice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from cracktrade.backtest import extract_metrics, extract_trades, run_variants
from cracktrade.config import dump_strategy
from cracktrade.domain import Metrics, OptimizationResult, ParameterChange
from cracktrade.errors import CracktradeError
from cracktrade.log import get_logger
from cracktrade.optimize.discovery import Parameter, discover_parameters, inject
from cracktrade.optimize.objective import (
    DEFAULT_OBJECTIVE,
    INFEASIBLE,
    Objective,
    get_objective,
)
from cracktrade.optimize.search import (
    SearchDiagnostics,
    evaluation_budget,
    run_search,
)
from cracktrade.optimize.windows import Split, TestWindow, TrainWindow, split
from cracktrade.strategy import build_strategy, required_warmup

if TYPE_CHECKING:
    import numpy.typing as npt

    from cracktrade.config import Strategy
    from cracktrade.data import MarketData
    from cracktrade.domain import Trade

logger = get_logger(__name__)


def optimize(
    strategy: Strategy,
    data: MarketData,
    *,
    epochs: int = 10,
    seed: int = 0,
    workers: int = 1,
    train_fraction: float = 0.8,
    objective_name: str = DEFAULT_OBJECTIVE,
    min_test_bars: int = 30,
) -> OptimizationResult:
    """Search ``strategy``'s parameters on train data and report on unseen test data.

    Raises:
        NoOptimizableParametersError: nothing in the strategy is tunable.
        OptimizationError: the history cannot support the requested split.
    """
    parameters = discover_parameters(strategy)
    warmup = worst_case_warmup(strategy, parameters)
    division = split(
        data, train_fraction=train_fraction, warmup=warmup, min_test_bars=min_test_bars
    )
    return optimize_split(
        strategy,
        division,
        ticker=data.ticker,
        epochs=epochs,
        seed=seed,
        workers=workers,
        objective_name=objective_name,
    ).result


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    """One optimization over one train/test division, with the pieces validation needs.

    Attributes:
        result: the reported result.
        pruned: the optimized strategy, collapsed to its winning variant pair.
        parameters: the tunable parameters that were searched.
        values: the winning parameter vector.
        trial_sharpes: Sharpe of every candidate scored, empty for a parallel search.
        test_returns: per-bar out-of-sample returns, warm-up prefix excluded.
    """

    result: OptimizationResult
    pruned: Strategy
    parameters: tuple[Parameter, ...]
    values: tuple[float, ...]
    trial_sharpes: tuple[float, ...]
    test_returns: npt.NDArray[np.float64]


def optimize_split(
    strategy: Strategy,
    division: Split,
    *,
    ticker: str,
    epochs: int = 10,
    seed: int = 0,
    workers: int = 1,
    objective_name: str = DEFAULT_OBJECTIVE,
) -> SplitOutcome:
    """Run the full protocol over one already-computed train/test division.

    Separated from :func:`optimize` so that walk-forward validation can drive many divisions
    without re-deriving the split each time.
    """
    parameters = discover_parameters(strategy)
    objective = get_objective(objective_name)

    diagnostics = SearchDiagnostics(
        seed=seed,
        workers=workers,
        objective=objective_name,
    )

    logger.info(
        "optimizing %d parameter(s) over %d train bars, reporting on %d test bars",
        len(parameters),
        len(division.train.data),
        division.test.scored_bars,
    )

    fitness = _Fitness(
        strategy=strategy, parameters=parameters, train=division.train, objective=objective
    )

    started = time.perf_counter()
    outcome = run_search(
        fitness,
        parameters,
        epochs=epochs,
        seed=seed,
        workers=workers,
        diagnostics=diagnostics,
    )
    diagnostics.elapsed_seconds = time.perf_counter() - started
    _absorb_counts(diagnostics, fitness, workers=workers)

    optimized = build_strategy(inject(strategy, parameters, outcome.values))

    # Variant selection is a *choice*, so it happens on train like every other choice.
    winner = _best_variant_name(optimized, division.train)
    pruned = _prune_to(optimized, winner)

    _warn_if_unhealthy(diagnostics)

    test_metrics, test_returns = _evaluate(pruned, division.test)

    result = OptimizationResult(
        strategy_name=strategy.strategy.name,
        ticker=ticker,
        objective=objective_name,
        optimized_yaml=dump_strategy(pruned),
        entry_name=winner[0],
        exit_name=winner[1],
        test_metrics=test_metrics,
        train_metrics=_evaluate_train(pruned, division.train),
        baseline_test_metrics=_evaluate(_prune_to(strategy, winner), division.test)[0],
        changes=_changes(parameters, outcome.values),
        trades=_test_trades(pruned, division.test),
        train_bars=len(division.train.data),
        test_bars=division.test.scored_bars,
        evaluations=diagnostics.evaluations,
        failures=diagnostics.failures,
        infeasible=diagnostics.infeasible,
        counts_exact=diagnostics.counts_exact,
        trials=diagnostics.evaluations * len(strategy.entry_variants) * len(strategy.exit_variants),
        budget=evaluation_budget(parameters, epochs),
        seed=seed,
        elapsed_seconds=diagnostics.elapsed_seconds,
        convergence_message=diagnostics.message,
        most_common_failure=diagnostics.most_common_failure,
    )

    return SplitOutcome(
        result=result,
        pruned=pruned,
        parameters=parameters,
        values=outcome.values,
        trial_sharpes=tuple(fitness.trial_sharpes) if diagnostics.counts_exact else (),
        test_returns=test_returns,
    )


@dataclass(slots=True)
class _Fitness:
    """The function scipy minimises, as a picklable object rather than a closure.

    A closure cannot cross a process boundary -- ``workers=-1`` fails with "Can't get local
    object" -- and spec section 9.2 requires the result to be *identical* at ``workers=1`` and
    ``workers=-1``, which cannot be asserted if the parallel path does not run at all.

    ``train`` is typed :class:`TrainWindow` and nothing else. Handing it a :class:`TestWindow` is
    a type error caught by mypy, which is what makes "never fit on test" a property of the code
    rather than a convention someone has to remember (spec section 2.5).
    """

    strategy: Strategy
    parameters: tuple[Parameter, ...]
    train: TrainWindow
    objective: Objective
    evaluations: int = 0
    failures: int = 0
    infeasible: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    #: Sharpe of every candidate scored. Their spread is the right measure of how widely the
    #: search ranged, which is what the deflated Sharpe deflates by (spec 12.3). Only populated
    #: in-process, so a parallel search leaves this empty and the statistic falls back.
    trial_sharpes: list[float] = field(default_factory=list)

    def __call__(self, vector: npt.NDArray[np.float64]) -> float:
        self.evaluations += 1
        try:
            candidate = build_strategy(
                inject(self.strategy, self.parameters, [float(value) for value in vector])
            )
            best, sharpe = _best_train_score(candidate, self.train, self.objective)
            self.trial_sharpes.append(sharpe)
        except CracktradeError as error:
            # A candidate that will not parse or will not simulate is not a bad strategy, it is
            # not a strategy. It must rank below every real one, not at zero (defect D9).
            self.failures += 1
            name = type(error).__name__
            self.failure_reasons[name] = self.failure_reasons.get(name, 0) + 1
            return INFEASIBLE

        if best == INFEASIBLE:
            self.infeasible += 1
        return best


def worst_case_warmup(strategy: Strategy, parameters: tuple[Parameter, ...]) -> int:
    """The largest warm-up any candidate in the search space could need.

    The test window is extended backwards by this much. Sizing it from the *baseline* strategy
    would be wrong in one direction that matters: bounds are plus or minus 50%, so the search
    can grow a 200-bar window to 300, and a candidate whose warm-up exceeded the offset would
    have its signals suppressed into the scored region -- quietly losing test bars it should
    have been able to trade.

    Evaluated by pushing every parameter to its upper bound. That combination need not be a
    valid strategy, so a failure falls back to the baseline's warm-up.
    """
    baseline = required_warmup(strategy)
    try:
        widest = build_strategy(
            inject(strategy, parameters, [parameter.high for parameter in parameters])
        )
    except CracktradeError:
        return baseline
    return max(baseline, required_warmup(widest))


def _best_train_score(
    strategy: Strategy, train: TrainWindow, objective: Objective
) -> tuple[float, float]:
    """The best objective score any variant achieves on train, and that variant's Sharpe.

    The Sharpe is returned alongside because the deflated Sharpe needs the *distribution* of
    trial Sharpes, not just the winner's, and recomputing it later would mean re-running the
    entire search.
    """
    risk_free = strategy.execution.risk_free_rate
    measured = [
        (objective(metrics), metrics.sharpe_ratio)
        for metrics in (
            extract_metrics(simulation.portfolio, risk_free_rate=risk_free)
            for simulation in run_variants(strategy, train.data)
        )
    ]
    if not measured:
        return INFEASIBLE, 0.0
    return min(measured, key=lambda pair: pair[0])


def _best_variant_name(strategy: Strategy, train: TrainWindow) -> tuple[str, str]:
    """The entry/exit pair that performed best on the train window."""
    risk_free = strategy.execution.risk_free_rate
    simulations = run_variants(strategy, train.data)
    best = max(
        simulations,
        key=lambda simulation: (
            extract_metrics(simulation.portfolio, risk_free_rate=risk_free).total_pnl
        ),
    )
    return best.pair.entry.name, best.pair.exit.name


def _prune_to(strategy: Strategy, winner: tuple[str, str]) -> Strategy:
    """Collapse a strategy to the single winning entry/exit pair."""
    entry_name, exit_name = winner
    config = strategy.model_dump(mode="python")
    config["entry_variants"] = [
        entry for entry in config["entry_variants"] if entry["name"] == entry_name
    ]
    config["exit_variants"] = [
        exit_ for exit_ in config["exit_variants"] if exit_["name"] == exit_name
    ]
    return build_strategy(config)


def _evaluate(strategy: Strategy, test: TestWindow) -> tuple[Metrics, npt.NDArray[np.float64]]:
    """Score a pruned strategy on the test window, returning its metrics and its returns."""
    simulation = run_variants(strategy, test.data)[0]
    metrics = extract_metrics(
        simulation.portfolio,
        risk_free_rate=strategy.execution.risk_free_rate,
        offset=test.offset,
    )
    returns = simulation.portfolio.returns().iloc[test.offset :].to_numpy()
    return metrics, np.asarray(returns, dtype=np.float64)


def _evaluate_train(strategy: Strategy, train: TrainWindow) -> Metrics:
    """Score a pruned strategy on the train window, for the overfitting gap."""
    simulation = run_variants(strategy, train.data)[0]
    return extract_metrics(simulation.portfolio, risk_free_rate=strategy.execution.risk_free_rate)


def _test_trades(strategy: Strategy, test: TestWindow) -> tuple[Trade, ...]:
    """Trades taken in the test window, excluding any opened during the warm-up prefix."""
    simulation = run_variants(strategy, test.data)[0]
    index = test.data.index
    cutoff = index[test.offset].date()
    return tuple(
        trade for trade in extract_trades(simulation.portfolio, index) if trade.entry_date >= cutoff
    )


def _changes(
    parameters: tuple[Parameter, ...], values: tuple[float, ...]
) -> tuple[ParameterChange, ...]:
    """What the search actually moved, and by how much."""
    changes: list[ParameterChange] = []
    for parameter, raw in zip(parameters, values, strict=True):
        new = round(raw) if parameter.is_integer else round(float(raw), 2)
        old = round(parameter.value) if parameter.is_integer else round(parameter.value, 2)
        changes.append(
            ParameterChange(
                path=parameter.path,
                old_value=float(old),
                new_value=float(new),
                low=parameter.low,
                high=parameter.high,
            )
        )
    return tuple(changes)


def _absorb_counts(diagnostics: SearchDiagnostics, fitness: _Fitness, *, workers: int) -> None:
    """Copy the fitness object's tallies into the diagnostics.

    ``evaluations`` always comes from scipy's own ``nfev``, which is authoritative regardless of
    how the work was distributed. The failure tallies live on the fitness object, and a parallel
    run mutates *copies* of it in child processes, so those counts do not come back. Rather than
    report a confident zero for a run in which most candidates may have failed, the diagnostics
    record that the counts are not exact and the report says so.
    """
    diagnostics.counts_exact = workers == 1
    if not diagnostics.counts_exact:
        return
    diagnostics.failures = fitness.failures
    diagnostics.infeasible = fitness.infeasible
    diagnostics.failure_reasons = dict(fitness.failure_reasons)


def _warn_if_unhealthy(diagnostics: SearchDiagnostics) -> None:
    """Say so when the search spent most of its budget on invalid configurations."""
    if diagnostics.counts_exact and diagnostics.looks_unhealthy:
        logger.warning(
            "%.0f%% of candidates failed (most often %s). That usually means the search bounds "
            "produce invalid configurations rather than that the search explored widely; the "
            "result rests on far fewer real evaluations than it appears to",
            100 * diagnostics.failure_fraction,
            diagnostics.most_common_failure,
        )
