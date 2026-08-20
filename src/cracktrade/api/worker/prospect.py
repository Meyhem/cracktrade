"""Executing one tick of a prospecting session -- spec section 19.7.

The shape here is the opposite of :mod:`cracktrade.api.worker.execute`. A run is claimed,
executed once and lands terminal; a session is claimed, ticks, and is *released* still running,
so the next pass through the worker loop re-decides whether to give the time to a user's run
instead. That release is what makes "same worker, low priority" true rather than aspirational:
a sweep cannot hold the worker for hours while a queued backtest waits behind it.

One tick is one ticker: evolve, transfer-test, store the candidate, advance the cursor. Then, if
there is time and anything is due, one forward score. Everything a tick can fail at is caught
and counted -- a single ticker whose history the provider will not serve must not end a sweep
that is working on the other nineteen.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass

import psycopg
import yaml
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.errors import ConflictError
from cracktrade.api.repos import ProspectRepo
from cracktrade.api.repos.rows import ProspectCandidateRow, ProspectSessionRow
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker import worker_name
from cracktrade.control import RunControl
from cracktrade.data import MarketDataProvider, YFinanceProvider
from cracktrade.errors import CracktradeError, RunCancelled
from cracktrade.evolution import GaSettings
from cracktrade.log import get_logger
from cracktrade.optimize import TradeFloor
from cracktrade.prospect import (
    Candidate,
    ForwardScore,
    Rotation,
    SweepHistory,
    SweepParams,
    forward_score,
    prospect_once,
)
from cracktrade.serialize import to_dict
from cracktrade.settings import Settings, load_settings
from cracktrade.strategy import build_strategy

logger = get_logger(__name__)

#: How stale a candidate's most recent forward score may be before it is scored again.
#:
#: A day, because that is the granularity at which the answer can change: the candidates are
#: intraday but the question -- "has this held up since we found it?" -- moves by sessions, not
#: by bars. Scoring more often would spend the sweep's compute redrawing the same point.
FORWARD_STALE_HOURS = 24.0

#: Forward scores attempted between one tick and the next.
#:
#: One. Section 19.3 puts forward validation *behind* the sweep in priority, and the sweep is
#: what produces the candidates there are to validate. A batch here would be the tail deciding
#: how long the dog waits.
FORWARD_BATCH = 1


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """What one tick did, for the loop above to log and for tests to assert on."""

    ticker: str
    candidate: Candidate | None
    error: str | None
    scored: int = 0

    @property
    def failed(self) -> bool:
        return self.error is not None


def sweep_settings(params: SweepParams) -> GaSettings:
    """The search one tick runs, from the session's frozen question."""
    return GaSettings(population=params.population, generations=params.generations)


def tick(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    control: RunControl | None = None,
    owner: str | None = None,
) -> TickOutcome:
    """Prospect one ticker and record what came of it.

    The cursor advances whether the search succeeded or not, and the two outcomes are counted
    separately. A ticker the provider cannot serve is a fact about that ticker, not about the
    sweep, and stopping on it would let one delisting end a session that is otherwise working.

    ``owner`` is this worker's name. When given, the write is refused if the session has been
    taken over mid-search, and nothing is stored -- see
    :meth:`~cracktrade.api.repos.prospect.ProspectRepo.record_tick`.

    Never raises for a failed search. It raises only if the *database* refuses the write --
    including that ownership refusal -- which is the caller's problem rather than the tick's.
    """
    resolved = engine_settings or load_settings()
    params = SweepParams.from_dict(session.params)
    rotation = Rotation(session.universe, session.cursor_index, session.passes_completed)
    ticker = rotation.current
    guard = control or RunControl()

    candidate: Candidate | None = None
    error: str | None = None
    try:
        candidate = prospect_once(
            params.chassis_for(ticker),
            SweepHistory(
                params=params,
                provider=provider or YFinanceProvider(),
                max_filled_fraction=resolved.max_filled_fraction,
            ),
            settings=sweep_settings(params),
            # Derived from the session's seed and the ticker, so a tick is reproducible and two
            # tickers in one pass are not handed the same search.
            seed=session.seed + rotation.index,
            workers=resolved.workers,
            objective_name=params.objective,
            trade_floor=TradeFloor(minimum=params.min_trades, per_year=params.min_trades_per_year),
            segments=params.segments,
            holdout_fraction=params.holdout_fraction,
            control=guard,
        )
    except RunCancelled:
        # The cursor is deliberately not advanced: nothing was measured for this ticker, and
        # resuming should retry it rather than skip it.
        logger.info("session %s: tick on %s cancelled", session.id, ticker)
        return TickOutcome(ticker=ticker, candidate=None, error=None)
    except CracktradeError as failure:
        error = str(failure)
        logger.warning("session %s: %s failed -- %s", session.id, ticker, error)
    except Exception as failure:
        # Deliberately broad and deliberately not a swallow: it is logged with its traceback
        # and counted as a failed tick. A worker that died here would leave the session
        # claimed until its lease lapsed, reporting a fault it already knew about as a timeout.
        logger.exception("session %s: %s raised an unexpected error", session.id, ticker)
        error = f"{type(failure).__name__}: {failure}"

    advanced = rotation.advance()
    with unit_of_work_on(connection) as work:
        repo = ProspectRepo(work.connection)
        if candidate is not None:
            repo.add_candidate(
                session_id=session.id,
                ticker=candidate.ticker,
                discovered_at=candidate.discovered_at,
                last_bar_seen=candidate.last_bar_seen,
                seed=candidate.seed,
                candidate=to_dict(candidate),
                strategy_yaml=candidate.strategy_yaml,
                survived_transfer=candidate.survives_transfer,
                transfer_median=candidate.transfer.median_sibling_sharpe,
                transfer_control=candidate.transfer.median_control_sharpe,
            )
        repo.record_tick(
            session.id,
            cursor_index=advanced.index,
            passes_completed=advanced.passes,
            failed=candidate is None,
            worker=owner,
        )
    return TickOutcome(ticker=ticker, candidate=candidate, error=error)


def score_due(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    limit: int = FORWARD_BATCH,
) -> int:
    """Forward-score the candidates whose evidence is missing or stale. Returns how many landed.

    A candidate with too few new bars is not scored and *nothing is written* -- section 19.3
    prefers "not yet" to a Sharpe computed from nine bars, which would sort above a real one.
    It stays due, and is picked up again once the bars exist.
    """
    params = SweepParams.from_dict(session.params)
    with unit_of_work_on(connection) as work:
        due = ProspectRepo(work.connection).due_for_forward_scoring(
            session_id=session.id, stale_after_hours=FORWARD_STALE_HOURS, limit=limit
        )
    if not due:
        return 0

    resolved = engine_settings or load_settings()
    history = SweepHistory(
        params=params,
        provider=provider or YFinanceProvider(),
        max_filled_fraction=resolved.max_filled_fraction,
    )
    landed = 0
    for row in due:
        score = _score_one(row, history)
        if score is None:
            continue
        with unit_of_work_on(connection) as work:
            ProspectRepo(work.connection).append_forward_score(
                candidate_id=row.id,
                first_bar=score.first_bar,
                last_bar=score.last_bar,
                bars=score.bars,
                return_pct=score.return_pct,
                sharpe=score.sharpe,
                trades=score.trades,
            )
        landed += 1
        logger.info(
            "session %s: %s forward %+.2f%% over %d bars since %s",
            session.id,
            row.ticker,
            score.return_pct,
            score.bars,
            row.last_bar_seen,
        )
    return landed


def _score_one(row: ProspectCandidateRow, history: SweepHistory) -> ForwardScore | None:
    """One candidate's forward score, or ``None`` if it cannot be measured yet.

    Failures here are logged and skipped rather than raised. Forward scoring is the low-priority
    half of a tick; a ticker whose history is briefly unavailable should cost the sweep one
    skipped score, not the session.
    """
    try:
        strategy = build_strategy(yaml.safe_load(row.strategy_yaml))
        return forward_score(strategy, history(row.ticker), since=row.last_bar_seen)
    except CracktradeError as failure:
        logger.warning("could not forward-score candidate %s: %s", row.id, failure)
        return None


def claim_and_tick(
    connection: psycopg.Connection[TupleRow],
    settings: ApiSettings,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    stop: threading.Event | None = None,
) -> ProspectSessionRow | None:
    """Claim a session, do one tick and one forward score, then release it.

    ``None`` when there is no session to work on. Returns the session it worked, whatever
    happened to the tick.

    Released rather than held, and that is the whole priority mechanism: the worker goes back
    to the top of its loop after every tick, where a queued run is checked first. A sweep
    therefore yields the process roughly once per search rather than once per session.

    A session with ``stop_requested`` is landed here rather than ticked, so it never stops in
    the middle of a search whose result would be paid for and discarded.
    """
    with unit_of_work_on(connection) as work:
        claimed = ProspectRepo(work.connection).claim_session(
            worker_name(), lease_seconds=settings.worker_lease_seconds
        )
    if claimed is None:
        return None

    if claimed.stop_requested or (stop is not None and stop.is_set()):
        with unit_of_work_on(connection) as work:
            repo = ProspectRepo(work.connection)
            if claimed.stop_requested:
                logger.info("session %s stopping as requested", claimed.id)
                repo.stop_session(claimed.id)
            else:
                repo.release_session(claimed.id, worker_name())
        return claimed

    heartbeat = SessionHeartbeat(
        database_url=settings.database_url,
        session_id=claimed.id,
        worker=worker_name(),
        interval=settings.worker_heartbeat_seconds,
        _stop=threading.Event(),
        _cancelled=threading.Event(),
    )
    with heartbeat as beat:
        control = RunControl(
            should_stop=lambda: beat.cancelled or (stop is not None and stop.is_set())
        )
        try:
            outcome = tick(
                connection,
                claimed,
                provider=provider,
                engine_settings=engine_settings,
                control=control,
                owner=worker_name(),
            )
        except ConflictError:
            # The session was taken over while this tick was computing. Nothing was stored;
            # the new owner will prospect this ticker itself.
            logger.warning("session %s was taken over mid-tick; discarding it", claimed.id)
            return claimed
        if not beat.cancelled and (stop is None or not stop.is_set()):
            score_due(connection, claimed, provider=provider, engine_settings=engine_settings)

    with unit_of_work_on(connection) as work:
        ProspectRepo(work.connection).release_session(claimed.id, worker_name())
    logger.info(
        "session %s: %s %s",
        claimed.id,
        outcome.ticker,
        "failed" if outcome.failed else "prospected",
    )
    return claimed


@dataclass(slots=True)
class SessionHeartbeat:
    """Keeps a claimed session's lease alive while a tick occupies the main thread.

    Its own connection, for the reason :class:`~cracktrade.api.worker.runner.Heartbeat` has one:
    a search holds the main thread for minutes, so a heartbeat sharing that connection would
    never get to write and the session would be taken over by another worker while this one was
    working perfectly.

    Unlike a run's heartbeat this does not poll for a cancellation flag. A session is stopped
    *between* ticks, and interrupting a search mid-flight would throw away the compute that was
    about to produce a candidate. What it does watch for is losing the lease, which means
    another worker has taken the session over and this one must stop before both advance the
    same cursor.
    """

    database_url: str
    session_id: object
    worker: str
    interval: float
    _stop: threading.Event
    _cancelled: threading.Event
    _thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        """Whether this worker has lost the session."""
        return self._cancelled.is_set()

    def _beat(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            while not self._stop.wait(self.interval):
                with contextlib.suppress(psycopg.Error), unit_of_work_on(connection) as work:
                    held = ProspectRepo(work.connection).heartbeat_session(
                        self.session_id,  # type: ignore[arg-type]
                        self.worker,
                    )
                    if not held:
                        self._cancelled.set()

    def __enter__(self) -> SessionHeartbeat:
        self._thread = threading.Thread(
            target=self._beat, daemon=True, name="cracktrade-prospect-heartbeat"
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


__all__ = [
    "FORWARD_BATCH",
    "FORWARD_STALE_HOURS",
    "SessionHeartbeat",
    "TickOutcome",
    "claim_and_tick",
    "score_due",
    "sweep_settings",
    "tick",
]
