"""Executing one claimed run.

The only place the API layer calls the engine to *compute* something. Everything it produces is
landed verbatim: the serialised result, the captured series, and the two columns list views
filter on -- in one transaction, so a run is never visible as succeeded with half its record
written.

Failures are categorised, never swallowed. Each category maps to the exit code the CLI would
have returned for the same fault (spec section 14.5), and the engine's own error text is stored
as it was raised, because the failure screen shows it and a summary would lose the detail that
makes it diagnosable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.repos import RunRepo, SeriesRepo, VersionRepo
from cracktrade.api.repos.rows import FailureCategory, RunKind, RunRow
from cracktrade.backtest import run_backtest
from cracktrade.config import Strategy
from cracktrade.control import RunControl
from cracktrade.data import MarketData, MarketDataProvider, YFinanceProvider, load_history
from cracktrade.domain import RunSeries
from cracktrade.errors import (
    BacktestError,
    CausalityViolationError,
    ConfigError,
    CracktradeError,
    DataError,
    OptimizationError,
    RunCancelled,
)
from cracktrade.evolution import Chassis, GaSettings, evolve, library_warmup
from cracktrade.log import get_logger
from cracktrade.optimize import DEFAULT_TRADE_FLOOR, TradeFloor, optimize
from cracktrade.serialize import to_dict
from cracktrade.settings import Settings, load_settings
from cracktrade.strategy import build_strategy, required_warmup
from cracktrade.validate import FoldScheme, walk_forward

logger = get_logger(__name__)

#: Bars of margin beyond the declared warm-up, so a strategy is not accepted with a history
#: that is entirely warm-up (defect D11).
WARMUP_MARGIN = 30

#: Engine exit codes, by category (spec section 13.3).
EXIT_CODES: dict[FailureCategory, int] = {
    FailureCategory.CONFIG_INVALID: 2,
    FailureCategory.MARKET_DATA: 3,
    FailureCategory.ENGINE_FAILURE: 4,
    FailureCategory.CAUSALITY_VIOLATION: 5,
}


def categorise(error: Exception) -> FailureCategory:
    """Which of the four failure categories an exception belongs to.

    Ordered by specificity: a causality violation is checked before the generic engine faults
    it would otherwise fall into, because it is the one failure that must never be reported as
    an ordinary crash.
    """
    if isinstance(error, CausalityViolationError):
        return FailureCategory.CAUSALITY_VIOLATION
    if isinstance(error, ConfigError):
        return FailureCategory.CONFIG_INVALID
    if isinstance(error, DataError):
        return FailureCategory.MARKET_DATA
    if isinstance(error, BacktestError | OptimizationError):
        return FailureCategory.ENGINE_FAILURE
    return FailureCategory.ENGINE_FAILURE


class Engine(Protocol):
    """What the worker needs from the engine.

    A protocol so tests can substitute a fast fake for the lifecycle cases -- claiming,
    heartbeating, cancelling, landing -- and use the real engine only where the numbers matter.
    Without it every lifecycle test would carry a differential-evolution search.
    """

    def run(
        self, strategy: Strategy, data: MarketData, run: RunRow, control: RunControl
    ) -> tuple[dict[str, Any], tuple[RunSeries, ...], bool | None, bool | None]:
        """Execute, returning (serialised result, series per fold, is_credible, suppressed)."""
        ...


@dataclass(frozen=True, slots=True)
class CracktradeEngine:
    """The real engine, behind the worker's protocol."""

    settings: Settings

    def run(
        self, strategy: Strategy, data: MarketData, run: RunRow, control: RunControl
    ) -> tuple[dict[str, Any], tuple[RunSeries, ...], bool | None, bool | None]:
        params = run.params

        if run.kind is RunKind.BACKTEST:
            result = run_backtest(strategy, data, seed=run.seed, capture_series=True)
            series = (result.series,) if result.series else ()
            return to_dict(result), series, None, not result.metrics.has_enough_trades_to_judge

        if run.kind is RunKind.OPTIMIZE:
            optimized = optimize(
                strategy,
                data,
                epochs=int(params.get("epochs", 10)),
                seed=run.seed,
                workers=self.settings.workers,
                objective_name=str(params.get("objective", "calmar")),
                trade_floor=_trade_floor(params),
                control=control,
                capture_series=True,
            )
            series = (optimized.series,) if optimized.series else ()
            suppressed = not optimized.test_metrics.has_enough_trades_to_judge
            return to_dict(optimized), series, None, suppressed

        if run.kind is RunKind.EVOLVE:
            evolved = evolve(
                chassis_for(strategy),
                data,
                settings=GaSettings(
                    population=int(params.get("population", 40)),
                    generations=int(params.get("generations", 25)),
                ),
                seed=run.seed,
                objective_name=str(params.get("objective", "calmar")),
                trade_floor=_trade_floor(params),
                segments=int(params.get("segments", 4)),
                holdout_fraction=float(params.get("holdout_fraction", 0.2)),
                control=control,
                capture_series=True,
            )
            series = (evolved.series,) if evolved.series else ()
            return (
                to_dict(evolved),
                series,
                evolved.is_credible,
                not evolved.holdout_metrics.has_enough_trades_to_judge,
            )

        report = walk_forward(
            strategy,
            data,
            folds=int(params.get("folds", 6)),
            scheme=FoldScheme(str(params.get("scheme", "anchored"))),
            epochs=int(params.get("epochs", 10)),
            seed=run.seed,
            workers=self.settings.workers,
            objective_name=str(params.get("objective", "calmar")),
            trade_floor=_trade_floor(params),
            control=control,
            capture_series=True,
        )
        return to_dict(report), report.fold_series, report.is_credible, None


def chassis_for(strategy: Strategy) -> Chassis:
    """The parts of a strategy an evolution run is allowed to inherit.

    Exactly the ticker, the dates, the costs and the sizing rule -- never the indicators, the
    entry or the exit, which are what the search composes. That asymmetry is the reason an
    evolution run is launched *against* a strategy rather than derived from one: the chassis
    says what market and what frictions to compose for, and nothing about what to compose.

    The name is the chassis's own, so a promoted strategy suggests something recognisable
    rather than inheriting a name that describes signals it does not contain.
    """
    return Chassis(
        name=f"{strategy.strategy.name}_evolved",
        ticker=strategy.universe.ticker,
        start_date=strategy.universe.start_date,
        end_date=strategy.universe.end_date,
        initial_capital=strategy.execution.initial_capital,
        slippage_pct=strategy.execution.slippage_pct,
        commission_pct=strategy.execution.commission_pct,
        risk_free_rate=strategy.execution.risk_free_rate,
        position_sizing=strategy.position_sizing,
    )


def _trade_floor(params: dict[str, Any]) -> TradeFloor:
    """The trade floor a launch asked for.

    Defaults are applied here as well as in the launch service, because a run queued before the
    floor was configurable has neither key in its stored params and must still execute.
    """
    return TradeFloor(
        minimum=int(params.get("min_trades", DEFAULT_TRADE_FLOOR.minimum)),
        per_year=float(params.get("min_trades_per_year", DEFAULT_TRADE_FLOOR.per_year)),
    )


def load_market_data(
    strategy: Strategy, provider: MarketDataProvider, settings: Settings, *, kind: RunKind
) -> MarketData:
    """Fetch the history the run needs, refusing one that cannot support it.

    ``min_bars`` carries the warm-up plus a margin so a 200-day average over a 60-day range is
    refused at the data layer rather than producing a backtest that is entirely warm-up.

    For an evolution run the warm-up is the *library's*, not the strategy's. A chassis declares
    a ticker and some costs; its own indicators are irrelevant, because the search can reach
    for any block in the library and the longest of those looks back 200 bars. Sizing this from
    ``required_warmup`` would accept a chassis with no indicators at all against a six-month
    window, and the run would then fail somewhere inside the search -- recorded as an engine
    fault, for what is really a history too short to have been accepted.
    """
    warmup = library_warmup() if kind is RunKind.EVOLVE else required_warmup(strategy)
    return load_history(
        strategy,
        provider,
        min_bars=warmup + WARMUP_MARGIN,
        max_filled_fraction=settings.max_filled_fraction,
    )


def _store_series(
    connection: psycopg.Connection[TupleRow], run: RunRow, series: tuple[RunSeries, ...]
) -> None:
    """Write the captured series.

    Fold 0 means "the run's one curve". A walk-forward has no fold 0 -- each fold re-optimizes,
    so there is no single curve -- and its folds are numbered from one.

    For an evolution run that curve covers the **holdout only**, not the whole history: the
    evolution region produced no reportable equity curve, because the strategy drawn on it is
    the one the region selected. Anything rendering these points has to say so, or it shows a
    partial history in a frame that means "the whole backtest" everywhere else.
    """
    repo = SeriesRepo(connection)
    walk_forward_run = run.kind is RunKind.WALK_FORWARD
    for index, bundle in enumerate(series):
        fold = index + 1 if walk_forward_run else 0
        for name, points in (
            ("equity", to_dict(bundle.equity)),
            ("benchmark_equity", to_dict(bundle.benchmark_equity)),
            ("drawdown", to_dict(bundle.drawdown)),
            ("close", to_dict(bundle.close)),
            ("monthly_returns", to_dict(bundle.monthly_returns)),
            ("rolling_12m_return", to_dict(bundle.rolling_12m_return)),
            # Dates only, and deliberately no values column: this series says *which* bars
            # were forward-filled so a chart can mark them, and inventing a value per date
            # would make it look like a measurement.
            ("filled", {"dates": to_dict(bundle.filled)}),
        ):
            repo.put(run_id=run.id, name=name, points=points, fold=fold)


def execute(
    connection: psycopg.Connection[TupleRow],
    run: RunRow,
    *,
    engine: Engine | None = None,
    provider: MarketDataProvider | None = None,
    settings: Settings | None = None,
    control: RunControl | None = None,
) -> RunRow:
    """Run one claimed run to completion and record what happened.

    Returns the terminal row. Never raises for an engine failure: a failed run is a *recorded
    outcome*, and a worker that crashed on one would leave the row running until its lease
    expired, reporting a fault it already knew about as a timeout.
    """
    resolved = settings or load_settings()
    engine = engine or CracktradeEngine(settings=resolved)

    try:
        version = VersionRepo(connection).require(run.strategy_id, run.version)
        strategy = build_strategy(version.config)
        data = load_market_data(strategy, provider or YFinanceProvider(), resolved, kind=run.kind)
        payload, series, credible, suppressed = engine.run(
            strategy, data, run, control or RunControl()
        )
    except RunCancelled:
        logger.info("run %s cancelled", run.id)
        with unit_of_work_on(connection) as work:
            return RunRepo(work.connection).cancel(run.id)
    except CracktradeError as error:
        category = categorise(error)
        logger.warning("run %s failed (%s): %s", run.id, category.value, error)
        with unit_of_work_on(connection) as work:
            return RunRepo(work.connection).fail(
                run.id,
                category=category,
                exit_code=EXIT_CODES[category],
                message=str(error),
            )
    except Exception as error:
        # Deliberately broad, and deliberately *not* a swallow: the traceback is logged, the run
        # is recorded as an engine failure, and nothing is converted into a plausible result.
        # The alternative is a worker that dies mid-run and leaves a row to time out with a
        # message that says nothing about what actually went wrong.
        logger.exception("run %s raised an unexpected error", run.id)
        with unit_of_work_on(connection) as work:
            return RunRepo(work.connection).fail(
                run.id,
                category=FailureCategory.ENGINE_FAILURE,
                exit_code=EXIT_CODES[FailureCategory.ENGINE_FAILURE],
                message=f"{type(error).__name__}: {error}",
            )

    # One transaction: the result, its series, and the promoted columns land together or not at
    # all. A succeeded run whose charts are missing would be a record of a measurement that is
    # partly absent, and nothing downstream could tell that from a run that captured nothing.
    with unit_of_work_on(connection) as work:
        landed = RunRepo(work.connection).succeed(
            run.id, result=payload, is_credible=credible, suppressed=suppressed
        )
        _store_series(work.connection, run, series)
    logger.info("run %s succeeded", run.id)
    return landed
