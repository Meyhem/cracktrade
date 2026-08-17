"""The optimization protocol: fit on train, report on test once.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.4.

The ordering here is the whole point, and it is the fix for defect D2. The one step that
*chooses* something -- the parameter search -- happens on the train window. The test window is
touched exactly once, after that choice is final, and its numbers are the only ones reported as
out-of-sample.

Train metrics are reported too, labelled in-sample. The gap between them is the single most
useful overfitting diagnostic available for free, and hiding it would be a disservice.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from cracktrade.backtest import (
    buy_and_hold_portfolio,
    extract_metrics,
    extract_trades,
    run_simulation,
)
from cracktrade.backtest.benchmark import compare
from cracktrade.backtest.series import capture
from cracktrade.config import dump_strategy
from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.domain import (
    BenchmarkComparison,
    Metrics,
    OptimizationResult,
    ParameterChange,
    RunSeries,
)
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
    import pandas as pd
    import vectorbt as vbt

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
    on_generation: Callable[[int, float], None] | None = None,
    control: RunControl = NO_CONTROL,
    capture_series: bool = False,
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
        on_generation=on_generation,
        control=control,
        capture_series=capture_series,
    ).result


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    """One optimization over one train/test division, with the pieces validation needs.

    Attributes:
        result: the reported result.
        optimized: the strategy with the winning parameter vector substituted in.
        parameters: the tunable parameters that were searched.
        values: the winning parameter vector.
        trial_sharpes: Sharpe of every candidate scored, empty for a parallel search.
        test_returns: per-bar out-of-sample returns, warm-up prefix excluded.
    """

    result: OptimizationResult
    optimized: Strategy
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
    on_generation: Callable[[int, float], None] | None = None,
    control: RunControl = NO_CONTROL,
    capture_series: bool = False,
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
        on_generation=on_generation,
        control=control,
    )
    diagnostics.elapsed_seconds = time.perf_counter() - started
    _absorb_counts(diagnostics, fitness, workers=workers)

    optimized = build_strategy(inject(strategy, parameters, outcome.values))

    _warn_if_unhealthy(diagnostics)

    test_metrics, test_returns = _evaluate(optimized, division.test)

    # One buy-and-hold portfolio, shared by the reported comparison and the chart series. Two
    # simulations of the same hold would agree today and are free to diverge later, and a
    # benchmark curve that disagrees with the benchmark number printed under it is worse than
    # either alone.
    hold = buy_and_hold_portfolio(
        division.test.data,
        optimized.execution,
        optimized.position_sizing,
        start_bar=division.test.offset,
    )

    result = OptimizationResult(
        strategy_name=strategy.strategy.name,
        ticker=ticker,
        objective=objective_name,
        optimized_yaml=dump_strategy(optimized),
        test_metrics=test_metrics,
        train_metrics=_evaluate_train(optimized, division.train),
        baseline_test_metrics=_evaluate(strategy, division.test)[0],
        changes=_changes(parameters, outcome.values),
        trades=_test_trades(optimized, division.test),
        train_bars=len(division.train.data),
        test_bars=division.test.scored_bars,
        evaluations=diagnostics.evaluations,
        failures=diagnostics.failures,
        infeasible=diagnostics.infeasible,
        counts_exact=diagnostics.counts_exact,
        trials=diagnostics.evaluations,
        budget=evaluation_budget(parameters, epochs),
        seed=seed,
        elapsed_seconds=diagnostics.elapsed_seconds,
        convergence_message=diagnostics.message,
        most_common_failure=diagnostics.most_common_failure,
        benchmark=_hold_comparison(optimized, division.test, test_metrics, test_returns, hold),
        series=_capture_test_series(optimized, division.test, hold) if capture_series else None,
    )

    return SplitOutcome(
        result=result,
        optimized=optimized,
        parameters=parameters,
        values=outcome.values,
        trial_sharpes=tuple(fitness.trial_sharpes) if diagnostics.counts_exact else (),
        test_returns=np.asarray(test_returns.to_numpy(), dtype=np.float64),
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
            best, sharpe = _train_score(candidate, self.train, self.objective)
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


def _train_score(
    strategy: Strategy, train: TrainWindow, objective: Objective
) -> tuple[float, float]:
    """A candidate's objective score on train, and its Sharpe.

    The Sharpe is returned alongside because the deflated Sharpe needs the *distribution* of
    trial Sharpes, not just the winner's, and recomputing it later would mean re-running the
    entire search.

    One score per candidate, because a strategy is one entry rule and one exit rule. While the
    engine crossed E entries with X exits this took the best of E*X on the same train window --
    and did so under the configured objective, while the *reported* winner was chosen by raw
    PnL, so the parameters could be fitted for one pair and the config promoted for another.
    """
    risk_free = strategy.execution.risk_free_rate
    metrics = extract_metrics(
        run_simulation(strategy, train.data).portfolio, risk_free_rate=risk_free
    )
    return objective(metrics), metrics.sharpe_ratio


def _evaluate(strategy: Strategy, test: TestWindow) -> tuple[Metrics, pd.Series]:
    """Score a strategy on the test window, returning its metrics and its returns.

    The returns keep their index. The information ratio subtracts one return series from
    another, and on bare arrays that subtraction is positional -- correct only while both
    happen to be the same length, and silently wrong the day one is not.
    """
    simulation = run_simulation(strategy, test.data)
    metrics = extract_metrics(
        simulation.portfolio,
        risk_free_rate=strategy.execution.risk_free_rate,
        offset=test.offset,
    )
    return metrics, simulation.portfolio.returns().iloc[test.offset :]


def _hold_comparison(
    strategy: Strategy,
    test: TestWindow,
    metrics: Metrics,
    returns: pd.Series,
    hold: vbt.Portfolio,
) -> BenchmarkComparison:
    """Measure the optimized strategy against buying and holding the same test window.

    Scored from ``test.offset`` on both sides, so the benchmark is credited with exactly the
    bars the strategy was able to trade and no warm-up prefix it could not have acted on.
    """
    return compare(
        metrics,
        extract_metrics(hold, risk_free_rate=strategy.execution.risk_free_rate, offset=test.offset),
        strategy_returns=returns,
        benchmark_returns=hold.returns().iloc[test.offset :],
    )


def _evaluate_train(strategy: Strategy, train: TrainWindow) -> Metrics:
    """Score a strategy on the train window, for the overfitting gap."""
    simulation = run_simulation(strategy, train.data)
    return extract_metrics(simulation.portfolio, risk_free_rate=strategy.execution.risk_free_rate)


def _test_trades(strategy: Strategy, test: TestWindow) -> tuple[Trade, ...]:
    """Trades taken in the test window, excluding any opened during the warm-up prefix."""
    simulation = run_simulation(strategy, test.data)
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


def _capture_test_series(strategy: Strategy, test: TestWindow, hold: vbt.Portfolio) -> RunSeries:
    """Chart series for the out-of-sample window, and only for it.

    An optimization run's charts must describe the window the search never saw. Capturing over
    the train window too would draw a curve whose early half was fitted, presented beside
    numbers that were not.
    """
    simulation = run_simulation(strategy, test.data)
    return capture(
        portfolio=simulation.portfolio,
        benchmark=hold,
        data=test.data,
        offset=test.offset,
    )
