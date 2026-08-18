"""Runs: the queue, the lease, and the terminal record.

This repository carries the run lifecycle of spec section 14.5. The schema refuses any
transition it does not permit, so the methods here are narrow on purpose -- each writes one
legal transition, and there is no general ``update``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from cracktrade.api.errors import ConflictError, NotFoundError
from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.rows import (
    FailureCategory,
    RunKind,
    RunOverviewRow,
    RunRow,
    RunStatus,
)

_COLUMNS = """
  id, strategy_id, version, number, kind, status, params, seed, queued_at, started_at,
  finished_at, claimed_by, heartbeat_at, cancel_requested, progress, result, error,
  failure_category, is_credible, suppressed
"""


def _run(row: tuple[Any, ...]) -> RunRow:
    return RunRow(
        id=row[0],
        strategy_id=row[1],
        version=row[2],
        number=row[3],
        kind=RunKind(row[4]),
        status=RunStatus(row[5]),
        params=row[6],
        seed=row[7],
        queued_at=row[8],
        started_at=row[9],
        finished_at=row[10],
        claimed_by=row[11],
        heartbeat_at=row[12],
        cancel_requested=row[13],
        progress=row[14],
        result=row[15],
        error=row[16],
        failure_category=FailureCategory(row[17]) if row[17] else None,
        is_credible=row[18],
        suppressed=row[19],
    )


class RunRepo(Repository):
    """Reads and writes ``run``, including the worker's claim and lease."""

    # ------------------------------------------------------------------ launching

    def create(
        self,
        *,
        strategy_id: UUID,
        version: int,
        kind: RunKind,
        params: dict[str, Any],
        seed: int,
    ) -> RunRow:
        """Queue a run against an exact version.

        The display number is assigned from the strategy's existing runs inside the caller's
        transaction, shared across kinds so ``#14`` is unambiguous within a strategy. As with
        version numbering, a concurrent launch loses to the unique constraint and is reported
        as a conflict to retry rather than being silently renumbered.
        """
        row = self._fetch_one(
            f"""
            INSERT INTO run (strategy_id, version, number, kind, params, seed)
            SELECT %s, %s, coalesce(max(number), 0) + 1, %s, %s, %s
            FROM run WHERE strategy_id = %s
            RETURNING {_COLUMNS}
            """,
            (strategy_id, version, kind.value, Jsonb(params), seed, strategy_id),
        )
        assert row is not None
        return _run(row)

    # ------------------------------------------------------------------ reading

    def get(self, run_id: UUID) -> RunRow | None:
        row = self._fetch_one(f"SELECT {_COLUMNS} FROM run WHERE id = %s", (run_id,))
        return _run(row) if row else None

    def require(self, run_id: UUID) -> RunRow:
        found = self.get(run_id)
        if found is None:
            raise NotFoundError(f"no run {run_id}")
        return found

    def list_overview(
        self,
        *,
        strategy_id: UUID | None = None,
        kinds: Sequence[RunKind] | None = None,
        statuses: Sequence[RunStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunOverviewRow]:
        """One query behind four screens: the run tabs, all-runs, the queue, the chart picker."""
        conditions: list[str] = []
        params: list[Any] = []
        if strategy_id is not None:
            conditions.append("strategy_id = %s")
            params.append(strategy_id)
        if kinds:
            conditions.append("kind = ANY(%s)")
            params.append([kind.value for kind in kinds])
        if statuses:
            conditions.append("status = ANY(%s)")
            params.append([status.value for status in statuses])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetch_all(
            f"""
            SELECT {_COLUMNS}, strategy_name, stale FROM run_overview
            {where} ORDER BY queued_at DESC LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        return [RunOverviewRow(run=_run(row), strategy_name=row[20], stale=row[21]) for row in rows]

    def count(
        self,
        *,
        strategy_id: UUID | None = None,
        kinds: Sequence[RunKind] | None = None,
        statuses: Sequence[RunStatus] | None = None,
    ) -> int:
        """Total matching runs, for pagination. Same filters as :meth:`list_overview`."""
        conditions: list[str] = []
        params: list[Any] = []
        if strategy_id is not None:
            conditions.append("strategy_id = %s")
            params.append(strategy_id)
        if kinds:
            conditions.append("kind = ANY(%s)")
            params.append([kind.value for kind in kinds])
        if statuses:
            conditions.append("status = ANY(%s)")
            params.append([status.value for status in statuses])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = self._fetch_one(f"SELECT count(*) FROM run {where}", params)
        assert row is not None
        return int(row[0])

    def overview(self, run_id: UUID) -> RunOverviewRow | None:
        row = self._fetch_one(
            f"SELECT {_COLUMNS}, strategy_name, stale FROM run_overview WHERE id = %s", (run_id,)
        )
        if row is None:
            return None
        return RunOverviewRow(run=_run(row), strategy_name=row[20], stale=row[21])

    # ------------------------------------------------------------------ the worker's side

    def claim(self, worker: str) -> RunRow | None:
        """Take the oldest queued run, or return ``None`` if there is nothing to do.

        ``FOR UPDATE SKIP LOCKED`` is what makes the database a queue: a second worker running
        this at the same moment steps over the locked row and takes the next one, so two
        workers never execute the same run and neither waits on the other.

        Every wall-clock column here uses ``clock_timestamp()``, not ``now()``. ``now()`` is
        the *transaction's* start time, frozen for its whole duration -- fine for a short,
        single-statement transaction, but a run's lease is held across a worker's setup and
        execution, so a connection left idle-in-transaction beforehand (a bug, but one that
        happened) would report an elapsed time measured from whenever that stale transaction
        began, not from now. ``clock_timestamp()`` reads the actual clock every time.
        """
        row = self._fetch_one(
            f"""
            UPDATE run SET
              status = 'running',
              started_at = clock_timestamp(),
              claimed_by = %s,
              heartbeat_at = clock_timestamp()
            WHERE id = (
              SELECT id FROM run
              WHERE status = 'queued'
              ORDER BY queued_at
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            RETURNING {_COLUMNS}
            """,
            (worker,),
        )
        return _run(row) if row else None

    def heartbeat(self, run_id: UUID, *, progress: dict[str, Any] | None = None) -> bool:
        """Refresh the lease, optionally with progress. False if the run is no longer running.

        A worker whose heartbeat is refused has lost its lease -- swept as abandoned, or
        cancelled -- and should stop rather than carry on writing into a run it no longer owns.
        """
        affected = self._execute(
            """
            UPDATE run SET heartbeat_at = clock_timestamp(), progress = coalesce(%s, progress)
            WHERE id = %s AND status = 'running'
            """,
            (Jsonb(progress) if progress is not None else None, run_id),
        )
        return affected == 1

    def succeed(
        self,
        run_id: UUID,
        *,
        result: dict[str, Any],
        is_credible: bool | None = None,
        suppressed: bool | None = None,
    ) -> RunRow:
        """Land a result. One statement, so the promoted columns cannot disagree with it."""
        row = self._fetch_one(
            f"""
            UPDATE run SET
              status = 'succeeded', finished_at = clock_timestamp(), progress = NULL,
              result = %s, is_credible = %s, suppressed = %s
            WHERE id = %s AND status = 'running'
            RETURNING {_COLUMNS}
            """,
            (Jsonb(result), is_credible, suppressed, run_id),
        )
        if row is None:
            raise ConflictError(f"run {run_id} is not running and cannot be completed")
        return _run(row)

    def fail(
        self, run_id: UUID, *, category: FailureCategory, exit_code: int, message: str
    ) -> RunRow:
        """Record a failure with the engine's own error text, preserved verbatim."""
        row = self._fetch_one(
            f"""
            UPDATE run SET
              status = 'failed', finished_at = clock_timestamp(), progress = NULL,
              error = %s, failure_category = %s
            WHERE id = %s AND status IN ('queued', 'running')
            RETURNING {_COLUMNS}
            """,
            (
                Jsonb({"category": category.value, "exit_code": exit_code, "message": message}),
                category.value,
                run_id,
            ),
        )
        if row is None:
            raise ConflictError(f"run {run_id} has already finished")
        return _run(row)

    def cancel(self, run_id: UUID) -> RunRow:
        """End a run as cancelled: no result, no error -- nothing was measured."""
        row = self._fetch_one(
            f"""
            UPDATE run SET status = 'cancelled', finished_at = clock_timestamp(), progress = NULL
            WHERE id = %s AND status IN ('queued', 'running')
            RETURNING {_COLUMNS}
            """,
            (run_id,),
        )
        if row is None:
            raise ConflictError(f"run {run_id} has already finished")
        return _run(row)

    def request_cancel(self, run_id: UUID) -> RunRow:
        """Ask a live run to stop. The worker observes this between generations and folds."""
        row = self._fetch_one(
            f"""
            UPDATE run SET cancel_requested = true
            WHERE id = %s AND status IN ('queued', 'running')
            RETURNING {_COLUMNS}
            """,
            (run_id,),
        )
        if row is None:
            raise ConflictError(f"run {run_id} has already finished")
        return _run(row)

    def expired_leases(self, lease_seconds: float) -> list[RunRow]:
        """Runs whose worker has gone quiet for longer than the lease.

        Reported rather than acted on: they are failed honestly by the caller, never re-queued,
        because re-running refetches data and would measure something else (spec section 14.5).
        """
        rows = self._fetch_all(
            f"""
            SELECT {_COLUMNS} FROM run
            WHERE status = 'running' AND heartbeat_at < clock_timestamp() - %s::interval
            """,
            (timedelta(seconds=lease_seconds),),
        )
        return [_run(row) for row in rows]
