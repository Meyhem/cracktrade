"""Sweeping a prospecting session -- spec section 19.7.

The shape here is the opposite of :mod:`cracktrade.api.worker.execute`. A run is claimed,
executed once and lands terminal; a session is claimed, swept, and *released* still running, so
the next pass through the worker loop re-decides whether to give the time to a user's run
instead.

One tick is one ticker: evolve, transfer-test, store the candidate. The position it works is
reserved before the search rather than advanced after it, which is what lets
``prospect_parallelism`` ticks run at once -- see
:meth:`~cracktrade.api.repos.ProspectRepo.reserve_ordinals`. Then, if there is time and anything
is due, one forward score.

"Same worker, low priority" used to fall out of releasing the session after every tick. It no
longer does: a sweep holds its session across many ticks, so it asks :func:`should_yield` after
each one whether a run is queued or the session has been asked to stop. Yielding drains the
ticks in flight rather than cancelling them, which keeps the promise that a queued backtest
waits at most one search.

Everything a tick can fail at is caught and counted -- a single ticker whose history the
provider will not serve must not end a sweep that is working on the other nineteen.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from uuid import UUID

import psycopg
import yaml
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.errors import ConflictError
from cracktrade.api.repos import ProspectRepo, RunRepo
from cracktrade.api.repos.rows import ProspectCandidateRow, ProspectSessionRow, RunStatus
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker import worker_name
from cracktrade.api.worker.pool import TickJob, TickResult, run_tick
from cracktrade.control import RunControl
from cracktrade.data import MarketDataProvider, YFinanceProvider
from cracktrade.errors import CracktradeError, RunCancelled
from cracktrade.log import get_logger
from cracktrade.optimize import TradeFloor
from cracktrade.prospect import (
    Candidate,
    ForwardScore,
    PrefetchHorizon,
    Reservation,
    SweepHistory,
    SweepParams,
    family_of,
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


def tick(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    reservation: Reservation,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    control: RunControl | None = None,
    owner: str | None = None,
) -> TickOutcome:
    """Prospect the ticker in ``reservation`` and record what came of it.

    The position is already spent when this is called -- the caller reserved it, which is what
    lets several ticks run at once -- so a failed search costs the sweep that position either
    way. The two outcomes are counted separately. A ticker the provider cannot serve is a fact
    about that ticker, not about the sweep, and stopping on it would let one delisting end a
    session that is otherwise working.

    ``owner`` is this worker's name. When given, the write is refused if the session has been
    taken over mid-search, and nothing is stored -- see
    :meth:`~cracktrade.api.repos.prospect.ProspectRepo.count_tick`.

    Never raises for a failed search. It raises only if the *database* refuses the write --
    including that ownership refusal -- which is the caller's problem rather than the tick's.
    """
    resolved = engine_settings or load_settings()
    params = SweepParams.from_dict(session.params)
    ticker = reservation.ticker
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
            settings=params.ga_settings(),
            # The session's seed plus the tick's ordinal across the whole sweep -- not its
            # position in the universe, which repeats every lap and would make each pass a
            # bit-identical replay of the one before it.
            seed=session.seed + reservation.ordinal,
            workers=resolved.workers,
            objective_name=params.objective,
            trade_floor=TradeFloor(minimum=params.min_trades, per_year=params.min_trades_per_year),
            segments=params.segments,
            holdout_fraction=params.holdout_fraction,
            control=guard,
        )
    except RunCancelled:
        # Nothing is counted: nothing was measured for this ticker. The position is spent all
        # the same, because it was reserved before the search began -- so resuming skips this
        # ticker for the current pass rather than retrying it, and round-robin returns to it
        # (section 19.7, as amended 2026-08-21).
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
        repo.count_tick(session.id, failed=candidate is None, worker=owner)
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


def claim_and_sweep(
    connection: psycopg.Connection[TupleRow],
    settings: ApiSettings,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    stop: threading.Event | None = None,
) -> ProspectSessionRow | None:
    """Claim a session, keep P ticks in flight until something asks it to yield, then release.

    ``None`` when there is no session to work on. Returns the session it worked, whatever
    happened to the ticks.

    A session with ``stop_requested`` is landed here rather than swept, so it never stops in the
    middle of a search whose result would be paid for and discarded.
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

        def yielding() -> bool:
            if beat.cancelled or (stop is not None and stop.is_set()):
                return True
            return should_yield(connection, claimed.id)

        try:
            _sweep(
                connection,
                claimed,
                settings,
                provider=provider or YFinanceProvider(),
                engine_settings=engine_settings or load_settings(),
                should_yield=yielding,
            )
        except ConflictError:
            # The session was taken over while ticks were computing. Nothing was stored for
            # them; the new owner carries on from the cursor.
            logger.warning("session %s was taken over mid-sweep; discarding it", claimed.id)
            return claimed
        if not beat.cancelled and (stop is None or not stop.is_set()):
            score_due(connection, claimed, provider=provider, engine_settings=engine_settings)

    with unit_of_work_on(connection) as work:
        ProspectRepo(work.connection).release_session(claimed.id, worker_name())
    return claimed


def should_yield(connection: psycopg.Connection[TupleRow], session_id: UUID) -> bool:
    """Whether a sweep should stop reserving new positions.

    Two reasons, both of which used to be free and are not any more. When a session was claimed
    for exactly one tick, ``run_forever``'s ordering gave a queued backtest its turn and the next
    claim re-read ``stop_requested`` from the row. A sweep that holds its session across many
    ticks has to ask, and this is where it asks.

    Read once per completed tick rather than on a timer, which is exactly as often as the answer
    can change anything.
    """
    with unit_of_work_on(connection) as work:
        session = ProspectRepo(work.connection).get_session(session_id)
        if session is None or session.stop_requested:
            return True
        return RunRepo(work.connection).count(statuses=[RunStatus.QUEUED]) > 0


def _sweep(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    settings: ApiSettings,
    *,
    provider: MarketDataProvider,
    engine_settings: Settings,
    should_yield: Callable[[], bool],
) -> None:
    """Keep the pool full until ``should_yield``, then drain what is in flight.

    **Sliding window, not a batch.** P positions are reserved and dispatched, and as each child
    returns its candidate is written and one more position is reserved and dispatched. A
    reserve-P/await-all loop would let the slowest tick in each wave gate the rest -- ticks were
    measured at 18-31s, so up to about 40% of the pool would sit idle at every tail.

    **Yielding drains rather than cancels.** Ticks in flight are allowed to finish, which bounds
    a queued run's wait at one search exactly as section 19.7 promises. Cancelling would throw
    away compute that was about to produce a candidate, and the position is already spent either
    way.
    """
    params = SweepParams.from_dict(session.params)
    horizon = PrefetchHorizon(
        provider,
        interval=params.interval,
        ttl_seconds=settings.prospect_prefetch_ttl_seconds,
    )
    owner = worker_name()

    def dispatch(pool: ProcessPoolExecutor, count: int) -> set[Future[TickResult]]:
        with unit_of_work_on(connection) as work:
            reserved, _ = ProspectRepo(work.connection).reserve_ordinals(
                session.id, count=count, worker=owner
            )
        start, end = params.window()
        jobs: set[Future[TickResult]] = set()
        for reservation in reserved:
            family = family_of(reservation.ticker)
            tickers = (reservation.ticker, *family.members, *family.controls)
            jobs.add(
                pool.submit(
                    run_tick,
                    TickJob(
                        reservation=reservation,
                        params=params,
                        # The session's seed plus the position's ordinal across the whole
                        # sweep -- not its index in the universe, which repeats every lap and
                        # would make each pass a bit-identical replay of the one before it.
                        seed=session.seed + reservation.ordinal,
                        frames=horizon.frames_for(tickers, start, end),
                        max_filled_fraction=engine_settings.max_filled_fraction,
                    ),
                )
            )
        return jobs

    with ProcessPoolExecutor(max_workers=settings.prospect_parallelism) as pool:
        pending = dispatch(pool, settings.prospect_parallelism)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                _land(connection, session, future.result(), owner=owner)
            if not should_yield():
                pending |= dispatch(pool, len(done))


def _land(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    result: TickResult,
    *,
    owner: str,
) -> None:
    """Write one child's finding and count it, in one transaction.

    The candidate insert shares the transaction with the count and both are ownership-checked, so
    a session taken over mid-search leaves nothing behind -- half a tick, a candidate with no
    tick counted against it, would be worse than none.
    """
    with unit_of_work_on(connection) as work:
        repo = ProspectRepo(work.connection)
        candidate = result.candidate
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
        repo.count_tick(session.id, failed=candidate is None, worker=owner)
    logger.info(
        "session %s: %s %s",
        session.id,
        result.reservation.ticker,
        "failed" if result.failed else "prospected",
    )


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
    "claim_and_sweep",
    "score_due",
    "should_yield",
    "tick",
]
