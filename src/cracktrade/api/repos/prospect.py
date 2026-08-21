"""Prospecting: the session's lease and cursor, its candidates, and their forward scores.

Three tables, one repository. They are read together far more often than separately -- the
leaderboard is a candidate beside its latest forward score, and a tick writes a candidate and
advances a cursor in the same transaction -- so splitting them would mostly produce two objects
that are always constructed as a pair.

Two things here differ from :class:`~cracktrade.api.repos.run.RunRepo`, and both follow from
spec section 19.7:

* **A session whose worker dies is reclaimed, not failed.** A run cannot be resumed, because
  re-running refetches prices that are retroactively adjusted and so measures something else. A
  sweep can: each tick is its own measurement, the completed ones are already durable, and the
  cursor says where to carry on from. There is therefore no sweep for expired sessions --
  :meth:`ProspectRepo.claim_session` simply takes any session whose lease has lapsed.

* **Nothing here writes a verdict.** Section 19.2's rule is that a sweep publishes candidates
  and credibility remains a property of a strategy that has had its own walk-forward. The
  schema has no column to express one, and this layer adds none.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from cracktrade.api.errors import ConflictError, NotFoundError
from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.rows import (
    ProspectCandidateOverviewRow,
    ProspectCandidateRow,
    ProspectForwardScoreRow,
    ProspectSessionRow,
    ProspectStatus,
)
from cracktrade.prospect import Reservation, Rotation

_SESSION_COLUMNS = """
  id, name, status, universe, params, seed, cursor_index, passes_completed, ticks_completed,
  ticks_failed, created_at, stopped_at, claimed_by, heartbeat_at, stop_requested, error
"""

#: The same columns qualified with the ``s`` alias, for statements that join another relation
#: and so cannot use a bare column list in ``RETURNING``.
_SESSION_COLUMNS_QUALIFIED = ", ".join(
    f"s.{column.strip()}" for column in _SESSION_COLUMNS.replace("\n", " ").split(",")
)

_CANDIDATE_COLUMNS = """
  id, session_id, ticker, discovered_at, last_bar_seen, seed, candidate, strategy_yaml,
  survived_transfer, transfer_median, transfer_control, created_at
"""

_FORWARD_COLUMNS = """
  id, candidate_id, scored_at, first_bar, last_bar, bars, return_pct, sharpe, trades
"""


def _session(row: tuple[Any, ...]) -> ProspectSessionRow:
    return ProspectSessionRow(
        id=row[0],
        name=row[1],
        status=ProspectStatus(row[2]),
        universe=tuple(row[3]),
        params=row[4],
        seed=row[5],
        cursor_index=row[6],
        passes_completed=row[7],
        ticks_completed=row[8],
        ticks_failed=row[9],
        created_at=row[10],
        stopped_at=row[11],
        claimed_by=row[12],
        heartbeat_at=row[13],
        stop_requested=row[14],
        error=row[15],
    )


def _candidate(row: tuple[Any, ...]) -> ProspectCandidateRow:
    return ProspectCandidateRow(
        id=row[0],
        session_id=row[1],
        ticker=row[2],
        discovered_at=row[3],
        last_bar_seen=row[4],
        seed=row[5],
        candidate=row[6],
        strategy_yaml=row[7],
        survived_transfer=row[8],
        transfer_median=row[9],
        transfer_control=row[10],
        created_at=row[11],
    )


def _forward(row: tuple[Any, ...]) -> ProspectForwardScoreRow:
    return ProspectForwardScoreRow(
        id=row[0],
        candidate_id=row[1],
        scored_at=row[2],
        first_bar=row[3],
        last_bar=row[4],
        bars=row[5],
        return_pct=row[6],
        sharpe=row[7],
        trades=row[8],
    )


class ProspectRepo(Repository):
    """Reads and writes the three prospecting tables."""

    # ------------------------------------------------------------------ sessions

    def create_session(
        self,
        *,
        name: str,
        universe: Sequence[str],
        params: dict[str, Any],
        seed: int,
    ) -> ProspectSessionRow:
        """Start a sweep. It is running from this moment -- there is no queued state.

        The universe and params are frozen here and there is no method to edit them. A session
        whose question could change would make its own candidate list incomparable with itself,
        which is the one thing a leaderboard accumulated over days must not be.
        """
        row = self._fetch_one(
            f"""
            INSERT INTO prospect_session (name, universe, params, seed)
            VALUES (%s, %s, %s, %s)
            RETURNING {_SESSION_COLUMNS}
            """,
            (name, list(universe), Jsonb(params), seed),
        )
        assert row is not None
        return _session(row)

    def get_session(self, session_id: UUID) -> ProspectSessionRow | None:
        row = self._fetch_one(
            f"SELECT {_SESSION_COLUMNS} FROM prospect_session WHERE id = %s", (session_id,)
        )
        return _session(row) if row else None

    def require_session(self, session_id: UUID) -> ProspectSessionRow:
        found = self.get_session(session_id)
        if found is None:
            raise NotFoundError(f"no prospecting session {session_id}")
        return found

    def list_sessions(
        self,
        *,
        statuses: Sequence[ProspectStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProspectSessionRow]:
        """Sessions newest first, optionally filtered by status."""
        where = "WHERE status = ANY(%s)" if statuses else ""
        params: list[Any] = [[status.value for status in statuses]] if statuses else []
        rows = self._fetch_all(
            f"""
            SELECT {_SESSION_COLUMNS} FROM prospect_session
            {where} ORDER BY created_at DESC LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        return [_session(row) for row in rows]

    def count_sessions(self, *, statuses: Sequence[ProspectStatus] | None = None) -> int:
        where = "WHERE status = ANY(%s)" if statuses else ""
        params: list[Any] = [[status.value for status in statuses]] if statuses else []
        row = self._fetch_one(f"SELECT count(*) FROM prospect_session {where}", params)
        assert row is not None
        return int(row[0])

    # ------------------------------------------------------------------ the worker's side

    def claim_session(self, worker: str, *, lease_seconds: float) -> ProspectSessionRow | None:
        """Take a running session nobody is working on, or ``None`` if there is none.

        ``FOR UPDATE SKIP LOCKED`` for the same reason as on ``run``: a second worker stepping
        over the locked row takes the next session instead of waiting, so two workers never
        prospect the same session and the cursor cannot be advanced twice for one tick.

        A session already claimed becomes claimable again once its heartbeat is older than the
        lease. That is deliberate and is the difference from a run: picking a sweep back up is
        not a re-measurement, it is the next tick of one that was interrupted.

        A session with ``stop_requested`` is *not* excluded. Somebody has to write the terminal
        row, and letting the ordinary claim do it keeps one code path rather than adding a
        reaper that would need its own lease.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              claimed_by = %s,
              heartbeat_at = clock_timestamp()
            WHERE id = (
              SELECT id FROM prospect_session
              WHERE status = 'running'
                AND (claimed_by IS NULL OR heartbeat_at < clock_timestamp() - %s::interval)
              ORDER BY created_at
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            RETURNING {_SESSION_COLUMNS}
            """,
            (worker, timedelta(seconds=lease_seconds)),
        )
        return _session(row) if row else None

    def heartbeat_session(self, session_id: UUID, worker: str) -> bool:
        """Refresh the lease. ``False`` means this worker no longer owns the session.

        Checked against ``claimed_by`` as well as status: a worker whose session was taken over
        after its heartbeat lapsed must stop rather than carry on advancing a cursor another
        worker is also advancing.
        """
        affected = self._execute(
            """
            UPDATE prospect_session SET heartbeat_at = clock_timestamp()
            WHERE id = %s AND status = 'running' AND claimed_by = %s
            """,
            (session_id, worker),
        )
        return affected == 1

    def reserve_ordinals(
        self, session_id: UUID, *, count: int, worker: str | None = None
    ) -> tuple[tuple[Reservation, ...], ProspectSessionRow]:
        """Take the next ``count`` positions in the sweep, advancing the cursor past them.

        The cursor moves *before* the work rather than after it, and that inversion is what lets
        several ticks be in flight at once: reservation becomes the serialisation point, and it
        is one cheap statement, so a multi-minute search no longer holds a transaction open
        across itself.

        The consequence is section 19.7's amended invariant. A worker that dies abandons the
        positions it reserved, and those tickers are *skipped for the current pass* rather than
        retried -- round-robin brings them back on the next one. Nothing measured is lost,
        because a completed tick is already durable in its own transaction.

        The advance is arithmetic on the flattened ordinal and is done in SQL because it has to
        be atomic across workers; ``FOR UPDATE`` in the CTE is what serialises two workers
        reserving at once. :meth:`~cracktrade.prospect.Rotation.reserve` performs the same
        advance as a value, and a test asserts the two agree across a wrap-around.

        Raises:
            ValueError: ``count`` is not positive.
            ConflictError: the session is not running, or is no longer held by ``worker``.
        """
        if count < 1:
            msg = f"a reservation needs a positive count, got {count}"
            raise ValueError(msg)
        row = self._fetch_one(
            f"""
            WITH before AS (
              SELECT id,
                     passes_completed * cardinality(universe) + cursor_index AS start_ordinal,
                     cardinality(universe) AS size
              FROM prospect_session
              WHERE id = %s AND status = 'running'
                AND (%s::text IS NULL OR claimed_by = %s)
              FOR UPDATE
            )
            UPDATE prospect_session s SET
              cursor_index = (before.start_ordinal + %s) %% before.size,
              passes_completed = (before.start_ordinal + %s) / before.size,
              heartbeat_at = clock_timestamp()
            FROM before
            WHERE s.id = before.id
            RETURNING before.start_ordinal, {_SESSION_COLUMNS_QUALIFIED}
            """,
            (session_id, worker, worker, count, count),
        )
        if row is None:
            raise ConflictError(
                f"prospecting session {session_id} is not running, or is no longer held by "
                f"{worker or 'this worker'}"
            )
        start_ordinal = int(row[0])
        session = _session(row[1:])
        size = len(session.universe)
        rotation = Rotation(
            session.universe, index=start_ordinal % size, passes=start_ordinal // size
        )
        reserved, _ = rotation.reserve(count)
        return reserved, session

    def record_tick(
        self,
        session_id: UUID,
        *,
        cursor_index: int,
        passes_completed: int,
        failed: bool = False,
        worker: str | None = None,
    ) -> ProspectSessionRow:
        """Advance the cursor and count the tick.

        The cursor is written rather than incremented in SQL because the rotation, not the
        database, knows what follows the last ticker in the universe. Passing both integers
        from the value that computed them keeps the wrap-around in one place.

        A failed tick still advances: one ticker whose history the provider cannot supply must
        not stop the sweep from reaching the other nineteen.

        ``worker`` is the ownership check, and it closes a real race. A search holds the main
        thread for minutes; if the heartbeat connection is down long enough for the lease to
        lapse, another worker takes the session over while this one is still computing. Without
        this clause both would then advance the same cursor -- skipping a ticker and
        double-counting a tick. The caller passes its own name and the write is refused if the
        session has moved on. The candidate insert shares the transaction, so a refused tick
        leaves nothing behind and the new owner simply prospects that ticker itself.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              cursor_index = %s,
              passes_completed = %s,
              ticks_completed = ticks_completed + %s,
              ticks_failed = ticks_failed + %s,
              heartbeat_at = clock_timestamp()
            WHERE id = %s AND status = 'running'
              AND (%s::text IS NULL OR claimed_by = %s)
            RETURNING {_SESSION_COLUMNS}
            """,
            (
                cursor_index,
                passes_completed,
                0 if failed else 1,
                1 if failed else 0,
                session_id,
                worker,
                worker,
            ),
        )
        if row is None:
            raise ConflictError(
                f"prospecting session {session_id} is not running, or is no longer held by "
                f"{worker or 'this worker'}"
            )
        return _session(row)

    def release_session(self, session_id: UUID, worker: str) -> bool:
        """Drop the lease without ending the session, so another worker can pick it up at once.

        What a graceful shutdown does. Without it a restarted worker would wait out the whole
        lease before resuming a sweep that nothing is actually working on.
        """
        affected = self._execute(
            """
            UPDATE prospect_session SET claimed_by = NULL, heartbeat_at = NULL
            WHERE id = %s AND status = 'running' AND claimed_by = %s
            """,
            (session_id, worker),
        )
        return affected == 1

    # ------------------------------------------------------------------ ending a session

    def request_stop(self, session_id: UUID) -> ProspectSessionRow:
        """Ask a sweep to stop. One-way, like ``run.cancel_requested``.

        The worker observes this between ticks and lands the terminal row itself, so a session
        never stops in the middle of a search whose result would then be paid for and thrown
        away.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET stop_requested = true
            WHERE id = %s AND status = 'running'
            RETURNING {_SESSION_COLUMNS}
            """,
            (session_id,),
        )
        if row is None:
            raise ConflictError(f"prospecting session {session_id} has already finished")
        return _session(row)

    def stop_idle_session(
        self, session_id: UUID, *, lease_seconds: float
    ) -> ProspectSessionRow | None:
        """End a session immediately if no live worker holds it. ``None`` if one does.

        Stopping is otherwise the worker's job -- it observes ``stop_requested`` between ticks
        and lands the terminal row, so a sweep never stops mid-search. But a session nobody is
        working on has no worker to observe anything, and a user who presses stop while the
        worker is down would watch a "stopping..." state that never resolves. The lease is what
        distinguishes the two cases, and reading it inside the same statement is what stops a
        worker claiming the session between the check and the write.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              status = 'stopped', stopped_at = clock_timestamp(), stop_requested = true,
              claimed_by = NULL, heartbeat_at = NULL
            WHERE id = %s AND status = 'running'
              AND (claimed_by IS NULL OR heartbeat_at < clock_timestamp() - %s::interval)
            RETURNING {_SESSION_COLUMNS}
            """,
            (session_id, timedelta(seconds=lease_seconds)),
        )
        return _session(row) if row else None

    def stop_session(self, session_id: UUID) -> ProspectSessionRow:
        """End a sweep cleanly. Its candidates stay exactly as they are."""
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              status = 'stopped', stopped_at = clock_timestamp(),
              claimed_by = NULL, heartbeat_at = NULL
            WHERE id = %s AND status = 'running'
            RETURNING {_SESSION_COLUMNS}
            """,
            (session_id,),
        )
        if row is None:
            raise ConflictError(f"prospecting session {session_id} has already finished")
        return _session(row)

    def fail_session(self, session_id: UUID, *, message: str) -> ProspectSessionRow:
        """End a sweep because it could not continue at all.

        Reserved for what breaks the whole session -- an unreachable database, a universe whose
        every ticker is unmapped. A single tick that fails is counted in ``ticks_failed`` and
        the sweep carries on; treating that as fatal would let one bad ticker end a run that
        was working.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              status = 'failed', stopped_at = clock_timestamp(), error = %s,
              claimed_by = NULL, heartbeat_at = NULL
            WHERE id = %s AND status = 'running'
            RETURNING {_SESSION_COLUMNS}
            """,
            (Jsonb({"message": message}), session_id),
        )
        if row is None:
            raise ConflictError(f"prospecting session {session_id} has already finished")
        return _session(row)

    # ------------------------------------------------------------------ candidates

    def add_candidate(
        self,
        *,
        session_id: UUID,
        ticker: str,
        discovered_at: datetime,
        last_bar_seen: date,
        seed: int,
        candidate: dict[str, Any],
        strategy_yaml: str,
        survived_transfer: bool,
        transfer_median: float,
        transfer_control: float,
    ) -> ProspectCandidateRow:
        """Record a tick's finding.

        The promoted columns are written in the same statement as the document they came from,
        so they cannot drift from it -- the same discipline ``run.is_credible`` follows.
        """
        row = self._fetch_one(
            f"""
            INSERT INTO prospect_candidate (
              session_id, ticker, discovered_at, last_bar_seen, seed, candidate, strategy_yaml,
              survived_transfer, transfer_median, transfer_control
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_CANDIDATE_COLUMNS}
            """,
            (
                session_id,
                ticker,
                discovered_at,
                last_bar_seen,
                seed,
                Jsonb(candidate),
                strategy_yaml,
                survived_transfer,
                transfer_median,
                transfer_control,
            ),
        )
        assert row is not None
        return _candidate(row)

    def get_candidate(self, candidate_id: UUID) -> ProspectCandidateRow | None:
        row = self._fetch_one(
            f"SELECT {_CANDIDATE_COLUMNS} FROM prospect_candidate WHERE id = %s", (candidate_id,)
        )
        return _candidate(row) if row else None

    def require_candidate(self, candidate_id: UUID) -> ProspectCandidateRow:
        found = self.get_candidate(candidate_id)
        if found is None:
            raise NotFoundError(f"no prospecting candidate {candidate_id}")
        return found

    def leaderboard(
        self,
        *,
        session_id: UUID | None = None,
        ticker: str | None = None,
        survivors_only: bool = True,
        limit: int = 10,
        offset: int = 0,
    ) -> list[ProspectCandidateOverviewRow]:
        """Candidates beside their most recent forward score.

        The order is the one section 19.5 permits: survivors first, then by forward return
        where there is any, then by transfer median. The holdout return -- the figure the
        search selected on, and the one a reader most wants to sort by -- is not an option,
        which is why it was left inside the jsonb and never promoted to a column.

        ``survivors_only`` defaults to true because that is the leaderboard. Passing false is
        for the session detail screen, where seeing what was rejected is the point.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("c.session_id = %s")
            params.append(session_id)
        if ticker is not None:
            conditions.append("c.ticker = %s")
            params.append(ticker)
        if survivors_only:
            conditions.append("c.survived_transfer")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetch_all(
            f"""
            SELECT {_prefixed(_CANDIDATE_COLUMNS, "c")},
                   {_prefixed(_FORWARD_COLUMNS, "f")},
                   (SELECT count(*) FROM prospect_forward_score s WHERE s.candidate_id = c.id)
            FROM prospect_candidate c
            LEFT JOIN LATERAL (
              SELECT {_FORWARD_COLUMNS} FROM prospect_forward_score
              WHERE candidate_id = c.id ORDER BY scored_at DESC LIMIT 1
            ) f ON true
            {where}
            ORDER BY c.survived_transfer DESC, f.return_pct DESC NULLS LAST,
                     c.transfer_median DESC, c.discovered_at DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        return [
            ProspectCandidateOverviewRow(
                candidate=_candidate(row[:12]),
                forward=_forward(row[12:21]) if row[12] is not None else None,
                forward_scores=int(row[21]),
            )
            for row in rows
        ]

    def count_candidates(
        self,
        *,
        session_id: UUID | None = None,
        ticker: str | None = None,
        survivors_only: bool = False,
    ) -> int:
        conditions: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = %s")
            params.append(session_id)
        if ticker is not None:
            conditions.append("ticker = %s")
            params.append(ticker)
        if survivors_only:
            conditions.append("survived_transfer")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = self._fetch_one(f"SELECT count(*) FROM prospect_candidate {where}", params)
        assert row is not None
        return int(row[0])

    # ------------------------------------------------------------------ forward scores

    def append_forward_score(
        self,
        *,
        candidate_id: UUID,
        first_bar: date,
        last_bar: date,
        bars: int,
        return_pct: float,
        sharpe: float | None,
        trades: int,
    ) -> ProspectForwardScoreRow:
        """Append one measurement on bars the candidate was never shown.

        Append, never overwrite: prices are retroactively adjusted on every dividend and split,
        so the same candidate scored today and next month is scored against different prices
        for the same bars. The database enforces this rather than trusting the caller -- there
        is no ``update_forward_score`` because the trigger would refuse it.
        """
        row = self._fetch_one(
            f"""
            INSERT INTO prospect_forward_score (
              candidate_id, first_bar, last_bar, bars, return_pct, sharpe, trades
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING {_FORWARD_COLUMNS}
            """,
            (candidate_id, first_bar, last_bar, bars, return_pct, sharpe, trades),
        )
        assert row is not None
        return _forward(row)

    def forward_scores(self, candidate_id: UUID) -> list[ProspectForwardScoreRow]:
        """Every score for one candidate, oldest first.

        The series, not the latest value: "this has been degrading for six weeks" is a thing a
        reader needs to see, and it is only visible as a sequence.
        """
        rows = self._fetch_all(
            f"""
            SELECT {_FORWARD_COLUMNS} FROM prospect_forward_score
            WHERE candidate_id = %s ORDER BY scored_at
            """,
            (candidate_id,),
        )
        return [_forward(row) for row in rows]

    def due_for_forward_scoring(
        self, *, session_id: UUID, stale_after_hours: float, limit: int = 5
    ) -> list[ProspectCandidateRow]:
        """Survivors whose forward score is missing or older than ``stale_after_hours``.

        Never-scored candidates come first, then the ones scored longest ago. Only survivors:
        a candidate that failed transfer is not going to be ranked, and spending a worker's
        time forward-scoring it takes that time from the sweep itself.
        """
        rows = self._fetch_all(
            f"""
            SELECT {_prefixed(_CANDIDATE_COLUMNS, "c")}
            FROM prospect_candidate c
            LEFT JOIN LATERAL (
              SELECT scored_at FROM prospect_forward_score
              WHERE candidate_id = c.id ORDER BY scored_at DESC LIMIT 1
            ) f ON true
            WHERE c.session_id = %s AND c.survived_transfer
              AND (f.scored_at IS NULL OR f.scored_at < clock_timestamp() - %s::interval)
            ORDER BY f.scored_at ASC NULLS FIRST
            LIMIT %s
            """,
            (session_id, timedelta(hours=stale_after_hours), limit),
        )
        return [_candidate(row) for row in rows]


def _prefixed(columns: str, alias: str) -> str:
    """Qualify a column list with a table alias, so the two joined tables cannot collide."""
    return ", ".join(f"{alias}.{name.strip()}" for name in columns.replace("\n", " ").split(","))


__all__ = ["ProspectRepo"]
