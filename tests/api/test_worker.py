"""Phase 6: the run pipeline.

Most of these use a fake engine. That is deliberate: what is being tested is the *lifecycle* --
claiming, leasing, landing, cancelling, failing -- and running a real differential-evolution
search inside each case would make the suite slow enough that nobody would run it, while
proving nothing extra about the transitions.

Two tests do run the real engine, against a stub data provider so nothing touches the network.
They exist to check the seam: that a real result serialises, lands, and comes back out with its
series intact.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.repos import RunRepo, SeriesRepo, StrategyRepo, VersionRepo
from cracktrade.api.repos.rows import (
    FailureCategory,
    RunKind,
    RunRow,
    RunStatus,
    StrategyOrigin,
    VersionOrigin,
)
from cracktrade.api.services.runs import headline, launch
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker.execute import categorise, execute
from cracktrade.api.worker.runner import claim_one, sweep_expired
from cracktrade.config import Strategy
from cracktrade.control import RunControl
from cracktrade.data import MarketData, StaticProvider
from cracktrade.domain import RunSeries
from cracktrade.errors import (
    BacktestError,
    CausalityViolationError,
    ConfigError,
    DataUnavailableError,
    OptimizationError,
    RunCancelled,
)
from cracktrade.indicators.catalogue import install
from tests.test_metrics import trending_market

pytestmark = pytest.mark.db

TICKER = "TEST"


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


def _config(ticker: str = TICKER) -> dict[str, Any]:
    return {
        "strategy": {"name": "momentum_v2"},
        "universe": {
            "ticker": ticker,
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.1,
            "commission_pct": 0.05,
        },
        "indicators": [{"name": "sma_fast", "type": "sma", "window": 20}],
        "entry": {"signal": "close > sma_fast"},
        "exit": {"signal": "close < sma_fast", "stop_loss_pct": 5.0},
    }


def _seed(db: psycopg.Connection[TupleRow], ticker: str = TICKER) -> UUID:
    strategy = StrategyRepo(db).create(name="momentum_v2", origin=StrategyOrigin.AUTHORED)
    VersionRepo(db).append(
        strategy_id=strategy.id,
        origin=VersionOrigin.CREATED,
        config=_config(ticker),
        config_yaml="yaml",
    )
    db.commit()
    return strategy.id


def _queue(db: psycopg.Connection[TupleRow], strategy_id: UUID, kind: RunKind) -> RunRow:
    with unit_of_work_on(db) as work:
        params: dict[str, Any] = {}
        if kind is RunKind.OPTIMIZE:
            params = {"epochs": 2}
        elif kind is RunKind.WALK_FORWARD:
            params = {"epochs": 2, "folds": 2}
        return launch(work, strategy_id=strategy_id, kind=kind, params=params)


def _provider() -> StaticProvider:
    """Bars from a deterministic generator, so no test reaches the network."""
    return StaticProvider({TICKER: trending_market(bars=700).frame})


class FakeEngine:
    """Stands in for the engine so lifecycle tests do not carry a parameter search."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        series: tuple[RunSeries, ...] = (),
        credible: bool | None = None,
        suppressed: bool | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.result = result if result is not None else {"metrics": {"total_trades": 41}}
        self.series = series
        self.credible = credible
        self.suppressed = suppressed
        self.raises = raises
        self.saw_control: RunControl | None = None

    def run(
        self, strategy: Strategy, data: MarketData, run: RunRow, control: RunControl
    ) -> tuple[dict[str, Any], tuple[RunSeries, ...], bool | None, bool | None]:
        self.saw_control = control
        control.progress("working", 50.0)
        # The real engine checks between generations and folds; this stands in for one such
        # checkpoint, which is what makes a stop request observable in a test.
        control.raise_if_cancelled()
        if self.raises is not None:
            raise self.raises
        return self.result, self.series, self.credible, self.suppressed


def _execute(db: psycopg.Connection[TupleRow], run: RunRow, engine: FakeEngine) -> RunRow:
    with unit_of_work_on(db) as work:
        claimed = RunRepo(work.connection).claim("test-worker")
    assert claimed is not None
    return execute(db, claimed, engine=engine, provider=_provider())


# --------------------------------------------------------------------------- launching


def test_a_launch_pins_the_head_version(db: psycopg.Connection[TupleRow]) -> None:
    """A client cannot ask to run an old config; the server decides what "now" means."""
    strategy_id = _seed(db)
    VersionRepo(db).append(
        strategy_id=strategy_id,
        origin=VersionOrigin.EDITED,
        config=_config(),
        config_yaml="yaml",
    )
    db.commit()

    run = _queue(db, strategy_id, RunKind.BACKTEST)
    assert run.version == 2
    assert run.status is RunStatus.QUEUED


def test_a_seed_is_always_recorded(db: psycopg.Connection[TupleRow]) -> None:
    """Reproducibility is a spec requirement, and "the default that day" is not a record."""
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    assert run.seed >= 0


# --------------------------------------------------------------------------- the happy path


def test_a_succeeded_run_lands_result_and_series_together(
    db: psycopg.Connection[TupleRow],
) -> None:
    """One transaction: a run is never visible as succeeded with half its record written."""
    from cracktrade.backtest import run_backtest
    from cracktrade.strategy import build_strategy

    data = trending_market(bars=700)
    captured = run_backtest(build_strategy(_config()), data, capture_series=True)
    assert captured.series is not None

    run = _queue(db, _seed(db), RunKind.BACKTEST)
    engine = FakeEngine(series=(captured.series,), suppressed=False)
    finished = _execute(db, run, engine)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.result == {"metrics": {"total_trades": 41}}
    assert finished.suppressed is False
    assert finished.progress is None

    catalog = SeriesRepo(db).catalog(run.id)
    assert set(catalog) == {
        "equity",
        "benchmark_equity",
        "drawdown",
        "close",
        "monthly_returns",
        "rolling_12m_return",
    }
    assert catalog["equity"] == [0], "a backtest has one curve, stored as fold 0"


def test_the_engine_receives_progress_and_cancellation_hooks(
    db: psycopg.Connection[TupleRow], db_url: str
) -> None:
    """Wired by the claim loop, which owns the heartbeat the hooks report through.

    ``execute`` on its own takes whatever control it is given -- none, in a direct call -- so
    this has to go through ``claim_one`` to check the wiring that actually ships.
    """
    _queue(db, _seed(db), RunKind.OPTIMIZE)
    engine = FakeEngine()
    claim_one(
        db,
        ApiSettings(database_url=db_url, worker_heartbeat_seconds=0.05),
        engine=engine,
        provider=_provider(),
    )

    assert engine.saw_control is not None
    assert engine.saw_control.on_progress is not None
    assert engine.saw_control.should_stop is not None


def test_walk_forward_series_are_numbered_from_one(db: psycopg.Connection[TupleRow]) -> None:
    """Fold 0 means "the whole run", and a walk-forward has no such thing."""
    from cracktrade.backtest import run_backtest
    from cracktrade.strategy import build_strategy

    data = trending_market(bars=700)
    captured = run_backtest(build_strategy(_config()), data, capture_series=True)
    assert captured.series is not None

    run = _queue(db, _seed(db), RunKind.WALK_FORWARD)
    _execute(db, run, FakeEngine(series=(captured.series, captured.series), credible=False))

    assert SeriesRepo(db).catalog(run.id)["equity"] == [1, 2]


# --------------------------------------------------------------------------- failure


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ConfigError("bad"), FailureCategory.CONFIG_INVALID),
        (DataUnavailableError("no bars"), FailureCategory.MARKET_DATA),
        (BacktestError("boom"), FailureCategory.ENGINE_FAILURE),
        (OptimizationError("boom"), FailureCategory.ENGINE_FAILURE),
        (CausalityViolationError("future"), FailureCategory.CAUSALITY_VIOLATION),
    ],
)
def test_engine_errors_are_categorised(error: Exception, category: FailureCategory) -> None:
    """A causality violation must never be filed as an ordinary crash."""
    assert categorise(error) == category


def test_a_failed_run_keeps_the_engines_own_words(db: psycopg.Connection[TupleRow]) -> None:
    """The failure screen shows this text, so summarising it would lose the diagnosis."""
    message = "MarketDataError: TEST 2020-01-01 -> returned 0 bars\n  3 retries, 1.2s backoff"
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    finished = _execute(db, run, FakeEngine(raises=DataUnavailableError(message)))

    assert finished.status is RunStatus.FAILED
    assert finished.failure_category is FailureCategory.MARKET_DATA
    assert finished.error is not None
    assert finished.error["message"] == message
    assert finished.error["exit_code"] == 3


def test_an_unexpected_error_is_recorded_rather_than_lost(
    db: psycopg.Connection[TupleRow],
) -> None:
    """A bug is still an outcome.

    A worker that died on one would leave the row running until its lease expired, reporting a
    fault it already knew about as a timeout.
    """
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    finished = _execute(db, run, FakeEngine(raises=ZeroDivisionError("division by zero")))

    assert finished.status is RunStatus.FAILED
    assert finished.failure_category is FailureCategory.ENGINE_FAILURE
    assert finished.error is not None
    assert "ZeroDivisionError" in finished.error["message"]


def test_a_missing_ticker_fails_as_market_data(db: psycopg.Connection[TupleRow]) -> None:
    """Exercises the real data path rather than a raised stand-in."""
    _queue(db, _seed(db, ticker="ABSENT"), RunKind.BACKTEST)
    with unit_of_work_on(db) as work:
        claimed = RunRepo(work.connection).claim("test-worker")
    assert claimed is not None
    finished = execute(db, claimed, engine=FakeEngine(), provider=_provider())

    assert finished.status is RunStatus.FAILED
    assert finished.failure_category is FailureCategory.MARKET_DATA


# --------------------------------------------------------------------------- cancellation


def test_a_cancelled_run_produces_no_result(db: psycopg.Connection[TupleRow]) -> None:
    """A search halted early is unfinished, not cheap. Reporting its numbers would be a lie."""
    run = _queue(db, _seed(db), RunKind.OPTIMIZE)
    finished = _execute(db, run, FakeEngine(raises=RunCancelled("stop")))

    assert finished.status is RunStatus.CANCELLED
    assert finished.result is None
    assert finished.error is None


def test_a_shutdown_signal_cancels_the_run_in_flight(
    db: psycopg.Connection[TupleRow], db_url: str
) -> None:
    """A worker asked to stop ends its run *cancelled*, not abandoned.

    The alternative is being killed mid-run and leaving the row to be swept and failed on lease
    expiry a minute later. Both records are honest, but this one is more accurate: an operator
    stopped it deliberately, and saying so distinguishes it from a crash.
    """
    _queue(db, _seed(db), RunKind.OPTIMIZE)
    halt = threading.Event()
    halt.set()

    finished = claim_one(
        db,
        ApiSettings(database_url=db_url, worker_heartbeat_seconds=0.05),
        engine=FakeEngine(),
        provider=_provider(),
        stop=halt,
    )

    assert finished is not None
    assert finished.status is RunStatus.CANCELLED
    assert finished.result is None


# --------------------------------------------------------------------------- leases


def test_an_abandoned_run_is_failed_not_requeued(db: psycopg.Connection[TupleRow]) -> None:
    """Re-running refetches retroactively adjusted prices, so a retry measures something else.

    Recording the interruption is the honest option, and the message says so rather than
    leaving the user to wonder why nothing restarted.
    """
    run = _queue(db, _seed(db), RunKind.OPTIMIZE)
    with unit_of_work_on(db) as work:
        RunRepo(work.connection).claim("worker-that-died")
    db.execute("UPDATE run SET heartbeat_at = now() - interval '10 minutes'")
    db.commit()

    swept = sweep_expired(db, lease_seconds=60)
    assert [row.id for row in swept] == [run.id]

    after = RunRepo(db).require(run.id)
    assert after.status is RunStatus.FAILED
    assert after.error is not None
    assert "not restarted" in after.error["message"]


def test_a_live_run_is_not_swept(db: psycopg.Connection[TupleRow]) -> None:
    _queue(db, _seed(db), RunKind.OPTIMIZE)
    with unit_of_work_on(db) as work:
        RunRepo(work.connection).claim("healthy-worker")
    db.commit()
    assert sweep_expired(db, lease_seconds=3600) == []


# --------------------------------------------------------------------------- the claim loop


def test_claiming_an_empty_queue_does_nothing(
    db: psycopg.Connection[TupleRow], db_url: str
) -> None:
    settings = ApiSettings(database_url=db_url)
    assert claim_one(db, settings, engine=FakeEngine(), provider=_provider()) is None


def test_the_loop_claims_and_finishes_a_run(db: psycopg.Connection[TupleRow], db_url: str) -> None:
    """The whole path in one call: claim, heartbeat, execute, land, notify."""
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    settings = ApiSettings(database_url=db_url, worker_heartbeat_seconds=0.05)

    finished = claim_one(db, settings, engine=FakeEngine(suppressed=False), provider=_provider())
    assert finished is not None
    assert finished.id == run.id
    assert finished.status is RunStatus.SUCCEEDED


def test_a_run_event_is_published(db: psycopg.Connection[TupleRow], db_url: str) -> None:
    """The server hears about a finished run over the channel they already share."""
    from cracktrade.api.events.notify import CHANNEL

    listener = psycopg.connect(db_url, autocommit=True)
    try:
        listener.execute(f"LISTEN {CHANNEL}")
        _queue(db, _seed(db), RunKind.BACKTEST)
        claim_one(
            db,
            ApiSettings(database_url=db_url, worker_heartbeat_seconds=0.05),
            engine=FakeEngine(suppressed=False),
            provider=_provider(),
        )

        listener.execute("SELECT 1")
        payloads = [json.loads(note.payload) for note in listener.notifies(timeout=5, stop_after=2)]
        assert [payload["status"] for payload in payloads] == ["running", "succeeded"]
    finally:
        listener.close()


# --------------------------------------------------------------------------- headlines


def test_a_suppressed_backtest_headline_omits_the_figures(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The client cannot render a number it was never given (spec section 15.2)."""
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    result = {
        "metrics": {
            "total_trades": 16,
            "total_return_pct": 13.2,
            "has_enough_trades_to_judge": False,
        },
        "entry_defined_pct": 8.0,
        "benchmark": {"excess_return_pct": -135.8, "benchmark": {"total_return_pct": 149.0}},
    }
    finished = _execute(db, run, FakeEngine(result=result, suppressed=True))

    summary = headline(finished)
    assert summary is not None
    assert summary["suppressed"] is True
    assert summary["trades"] == 16
    assert "return_pct" not in summary
    assert "benchmark_return_pct" not in summary


def test_an_unsuppressed_backtest_headline_carries_the_figures(
    db: psycopg.Connection[TupleRow],
) -> None:
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    result = {
        "metrics": {
            "total_trades": 41,
            "total_return_pct": 13.2,
            "max_drawdown_pct": -22.4,
            "has_enough_trades_to_judge": True,
        },
        "entry_defined_pct": 94.0,
        "benchmark": {"excess_return_pct": -135.8, "benchmark": {"total_return_pct": 149.0}},
    }
    summary = headline(_execute(db, run, FakeEngine(result=result, suppressed=False)))
    assert summary is not None
    assert summary["return_pct"] == 13.2
    assert summary["excess_pp"] == -135.8


def test_a_walk_forward_headline_counts_failed_checks(
    db: psycopg.Connection[TupleRow],
) -> None:
    run = _queue(db, _seed(db), RunKind.WALK_FORWARD)
    result = {
        "folds": [{}, {}, {}, {}],
        "scheme": "anchored",
        "combined_return_pct": 6.6,
        "benchmark": {"total_return_pct": 149.0},
        "profitable_folds": 2,
        "total_trades": 16,
        "is_credible": False,
        "checks": [{"passed": True}, {"passed": False}, {"passed": False}],
    }
    summary = headline(_execute(db, run, FakeEngine(result=result, credible=False)))
    assert summary is not None
    assert summary["failed_checks"] == 2
    assert summary["is_credible"] is False
    assert summary["folds"] == 4


def test_an_unfinished_run_has_no_headline(db: psycopg.Connection[TupleRow]) -> None:
    assert headline(_queue(db, _seed(db), RunKind.BACKTEST)) is None


# --------------------------------------------------------------------------- the real engine


@pytest.mark.slow
def test_a_real_backtest_runs_end_to_end(db: psycopg.Connection[TupleRow]) -> None:
    """The seam itself: a real result serialises, lands, and keeps its series.

    Deliberately one of only two tests that run the engine here. The lifecycle is covered by
    the fake; this checks that what the engine actually produces survives the round trip.
    """
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    with unit_of_work_on(db) as work:
        claimed = RunRepo(work.connection).claim("test-worker")
    assert claimed is not None

    finished = execute(db, claimed, provider=_provider())
    assert finished.status is RunStatus.SUCCEEDED, finished.error

    assert finished.result is not None
    assert finished.result["metrics"]["total_trades"] >= 0
    assert finished.result["vintage"]["ticker"] == TICKER
    assert "benchmark" in finished.result

    equity = SeriesRepo(db).require(run.id, "equity")
    assert len(equity.points["dates"]) == len(equity.points["values"]) > 0

    summary = headline(finished)
    assert summary is not None
    assert summary["trade_floor"] == 20


@pytest.mark.slow
def test_a_real_optimization_records_its_diagnostics(
    db: psycopg.Connection[TupleRow],
) -> None:
    run = _queue(db, _seed(db), RunKind.OPTIMIZE)
    with unit_of_work_on(db) as work:
        claimed = RunRepo(work.connection).claim("test-worker")
    assert claimed is not None

    finished = execute(db, claimed, provider=_provider())
    assert finished.status is RunStatus.SUCCEEDED, finished.error
    assert finished.result is not None
    assert finished.result["seed"] == run.seed
    assert finished.result["trials"] > 0
    # Contractual derived properties survive the round trip (spec section 15.1).
    assert "improvement_pct" in finished.result
    assert "overfitting_gap_pct" in finished.result


def test_started_and_finished_timestamps_bracket_the_run(
    db: psycopg.Connection[TupleRow],
) -> None:
    run = _queue(db, _seed(db), RunKind.BACKTEST)
    finished = _execute(db, run, FakeEngine(suppressed=False))
    assert finished.started_at is not None
    assert finished.finished_at is not None
    assert finished.queued_at <= finished.started_at <= finished.finished_at
    assert finished.finished_at <= datetime.now(UTC)
