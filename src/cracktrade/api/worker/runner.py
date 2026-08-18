"""The claim loop.

One run at a time. The worker claims the oldest queued run, holds a lease on it while a
background thread refreshes the heartbeat, executes it, and goes back for the next.

A run whose worker dies is **failed, never re-queued** (spec section 14.5). Re-running would
refetch prices that are retroactively adjusted, so the second attempt is a different
measurement wearing the first one's identity. Recording the truth -- this run was interrupted
-- costs the user one click and keeps the record honest.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.events.notify import notify_run
from cracktrade.api.repos import RunRepo
from cracktrade.api.repos.rows import FailureCategory, RunRow
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker.execute import EXIT_CODES, Engine, execute
from cracktrade.control import RunControl
from cracktrade.data import MarketDataProvider
from cracktrade.log import get_logger
from cracktrade.settings import Settings

logger = get_logger(__name__)


def worker_name() -> str:
    """Identifies the process holding a lease, for a human reading the row later."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(slots=True)
class Heartbeat:
    """Keeps a claimed run's lease alive, and watches for a cancellation request.

    Runs on its own connection. The engine occupies the main thread for minutes at a time, so a
    heartbeat sharing that connection would never get to write -- and the run would be swept as
    abandoned while it was working perfectly.
    """

    database_url: str
    run_id: object
    interval: float
    _stop: threading.Event
    _cancelled: threading.Event
    _thread: threading.Thread | None = None
    _stage: str = "starting"
    _percent: float = 0.0

    @classmethod
    def for_run(cls, database_url: str, run: RunRow, interval: float) -> Heartbeat:
        return cls(
            database_url=database_url,
            run_id=run.id,
            interval=interval,
            _stop=threading.Event(),
            _cancelled=threading.Event(),
        )

    def report(self, stage: str, percent: float) -> None:
        """Record progress for the next beat rather than writing on every call.

        The engine reports once per generation, which can be several times a second on a small
        search; one write per beat is enough for a progress bar and spares the database the
        rest.
        """
        self._stage = stage
        self._percent = percent

    @property
    def cancelled(self) -> bool:
        """Whether a cancellation request has been seen, or the lease has been lost."""
        return self._cancelled.is_set()

    def _beat(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            while not self._stop.wait(self.interval):
                with contextlib.suppress(psycopg.Error), unit_of_work_on(connection) as work:
                    repo = RunRepo(work.connection)
                    alive = repo.heartbeat(
                        self.run_id,  # type: ignore[arg-type]
                        progress={"stage": self._stage, "percent": round(self._percent, 1)},
                    )
                    current = repo.get(self.run_id)  # type: ignore[arg-type]
                    if not alive or (current is not None and current.cancel_requested):
                        # Either something asked this run to stop, or the row is no longer
                        # running and this worker no longer owns it. Both mean: put the tools
                        # down.
                        self._cancelled.set()

    def __enter__(self) -> Heartbeat:
        self._thread = threading.Thread(target=self._beat, daemon=True, name="cracktrade-heartbeat")
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


def sweep_expired(connection: psycopg.Connection[TupleRow], lease_seconds: float) -> list[RunRow]:
    """Fail runs whose worker has gone quiet, and say why.

    Honest rather than convenient: the run is recorded as interrupted rather than retried,
    because a retry fetches data again and measures something else.
    """
    failed: list[RunRow] = []
    with unit_of_work_on(connection) as work:
        expired = RunRepo(work.connection).expired_leases(lease_seconds)
    for run in expired:
        logger.warning("run %s lost its worker (%s); failing it", run.id, run.claimed_by)
        with unit_of_work_on(connection) as work:
            failed.append(
                RunRepo(work.connection).fail(
                    run.id,
                    category=FailureCategory.ENGINE_FAILURE,
                    exit_code=EXIT_CODES[FailureCategory.ENGINE_FAILURE],
                    message=(
                        f"the worker executing this run ({run.claimed_by}) stopped reporting. "
                        f"The run was not restarted: re-running refetches prices that are "
                        f"retroactively adjusted, so it would measure something else."
                    ),
                )
            )
    return failed


def claim_one(
    connection: psycopg.Connection[TupleRow],
    settings: ApiSettings,
    *,
    engine: Engine | None = None,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    stop: threading.Event | None = None,
) -> RunRow | None:
    """Claim and execute one run. ``None`` when the queue is empty.

    ``stop`` is the process's shutdown signal. It is folded into the run's cancellation
    predicate, so a worker asked to shut down ends its in-flight run *cancelled* at the next
    checkpoint rather than being killed and leaving the run to be swept as abandoned minutes
    later. Cancelled is the more accurate record: an operator stopped it deliberately.
    """
    with unit_of_work_on(connection) as work:
        claimed = RunRepo(work.connection).claim(worker_name())
    if claimed is None:
        return None

    logger.info("claimed run %s (%s)", claimed.id, claimed.kind.value)
    notify_run(connection, claimed.id, claimed.strategy_id, "running")

    halt = stop
    heartbeat = Heartbeat.for_run(settings.database_url, claimed, settings.worker_heartbeat_seconds)
    with heartbeat as beat:
        finished = execute(
            connection,
            claimed,
            engine=engine,
            provider=provider,
            settings=engine_settings,
            control=RunControl(
                on_progress=beat.report,
                should_stop=lambda: beat.cancelled or (halt is not None and halt.is_set()),
            ),
        )

    notify_run(connection, finished.id, finished.strategy_id, finished.status.value)
    return finished


def run_forever(
    settings: ApiSettings,
    *,
    provider: MarketDataProvider | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Claim runs until asked to stop.

    Sweeping happens on every idle pass rather than on a timer: the moment the queue is empty
    is exactly when a stuck lease matters and nothing else is competing for the connection.
    """
    halt = stop or threading.Event()
    logger.info("worker %s ready", worker_name())

    with psycopg.connect(settings.database_url) as connection:
        while not halt.is_set():
            executed = claim_one(connection, settings, provider=provider, stop=halt)
            if executed is None:
                sweep_expired(connection, settings.worker_lease_seconds)
                halt.wait(settings.worker_poll_seconds)
    logger.info("worker %s stopped", worker_name())


@contextlib.contextmanager
def shutdown_on_signal(halt: threading.Event) -> Iterator[None]:
    """Turn SIGINT and SIGTERM into the worker's ordinary stop signal.

    The first signal asks: no new runs are claimed and the in-flight one stops at its next
    checkpoint, ending ``cancelled``. A second signal restores the default handler, so an
    impatient operator still gets an immediate exit -- and the run it interrupts is swept and
    failed honestly on lease expiry, which is what a killed worker should leave behind.

    Only installed by the ``worker`` command: signal handlers are process-global, and a library
    function that installed them would take them away from whatever embedded it.
    """
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def _ask_to_stop(number: int, _frame: object) -> None:
        logger.warning(
            "%s received; finishing the current run's checkpoint, then stopping. "
            "Signal again to exit immediately.",
            signal.Signals(number).name,
        )
        halt.set()
        signal.signal(number, previous[signal.Signals(number)])

    for number in previous:
        signal.signal(number, _ask_to_stop)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
