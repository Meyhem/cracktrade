"""Strategies: identity, lineage, and the derived overview the list screen renders."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from cracktrade.api.errors import NotFoundError
from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.rows import (
    PurgeCounts,
    RunKind,
    RunStatus,
    StrategyOrigin,
    StrategyOverviewRow,
    StrategyRow,
    Verdict,
)

#: The transaction-local setting migration 0002 has the append-only triggers read. Setting it
#: is how a transaction declares which strategy it is purging; PostgreSQL reverts it at COMMIT
#: or ROLLBACK, so the permission cannot outlive the transaction or leak onto the next borrower
#: of a pooled connection.
PURGE_SETTING = "cracktrade.purge_strategy_id"

_COLUMNS = """
  id, name, origin, parent_strategy_id, parent_version, origin_run_id,
  origin_not_credible, created_at
"""

_OVERVIEW_COLUMNS = """
  id, name, origin, parent_strategy_id, parent_version, origin_run_id, origin_not_credible,
  created_at, head_version, edited_at, ticker, start_date, end_date,
  optimize_runs, backtest_runs, walk_forward_runs, versions,
  last_run_id, last_run_kind, last_run_status, last_run_at, verdict, verdict_run_id
"""


def _strategy(row: tuple[Any, ...]) -> StrategyRow:
    return StrategyRow(
        id=row[0],
        name=row[1],
        origin=StrategyOrigin(row[2]),
        parent_strategy_id=row[3],
        parent_version=row[4],
        origin_run_id=row[5],
        origin_not_credible=row[6],
        created_at=row[7],
    )


def _overview(row: tuple[Any, ...]) -> StrategyOverviewRow:
    return StrategyOverviewRow(
        id=row[0],
        name=row[1],
        origin=StrategyOrigin(row[2]),
        parent_strategy_id=row[3],
        parent_version=row[4],
        origin_run_id=row[5],
        origin_not_credible=row[6],
        created_at=row[7],
        head_version=row[8],
        edited_at=row[9],
        ticker=row[10],
        start_date=row[11],
        end_date=row[12],
        optimize_runs=row[13],
        backtest_runs=row[14],
        walk_forward_runs=row[15],
        versions=row[16],
        last_run_id=row[17],
        last_run_kind=RunKind(row[18]) if row[18] else None,
        last_run_status=RunStatus(row[19]) if row[19] else None,
        last_run_at=row[20],
        verdict=Verdict(row[21]),
        verdict_run_id=row[22],
    )


class StrategyRepo(Repository):
    """Reads and writes ``strategy`` and its overview view."""

    def create(
        self,
        *,
        name: str,
        origin: StrategyOrigin,
        parent_strategy_id: UUID | None = None,
        parent_version: int | None = None,
        origin_run_id: UUID | None = None,
        origin_not_credible: bool = False,
    ) -> StrategyRow:
        """Insert a strategy. The lineage CHECK enforces the shape for each origin."""
        row = self._fetch_one(
            f"""
            INSERT INTO strategy
              (name, origin, parent_strategy_id, parent_version, origin_run_id,
               origin_not_credible)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (
                name,
                origin.value,
                parent_strategy_id,
                parent_version,
                origin_run_id,
                origin_not_credible,
            ),
        )
        assert row is not None  # RETURNING on a successful INSERT always yields a row
        return _strategy(row)

    def get(self, strategy_id: UUID) -> StrategyRow | None:
        row = self._fetch_one(f"SELECT {_COLUMNS} FROM strategy WHERE id = %s", (strategy_id,))
        return _strategy(row) if row else None

    def require(self, strategy_id: UUID) -> StrategyRow:
        """:meth:`get`, but a missing strategy is the caller's answer rather than ``None``."""
        found = self.get(strategy_id)
        if found is None:
            raise NotFoundError(f"no strategy {strategy_id}")
        return found

    def get_by_name(self, name: str) -> StrategyRow | None:
        row = self._fetch_one(f"SELECT {_COLUMNS} FROM strategy WHERE name = %s", (name,))
        return _strategy(row) if row else None

    def overview(self, strategy_id: UUID) -> StrategyOverviewRow | None:
        row = self._fetch_one(
            f"SELECT {_OVERVIEW_COLUMNS} FROM strategy_overview WHERE id = %s", (strategy_id,)
        )
        return _overview(row) if row else None

    def list_overview(
        self, *, search: str | None = None, verdict: Verdict | None = None
    ) -> list[StrategyOverviewRow]:
        """The list screen, with its search box and verdict filter.

        ``search`` matches name or ticker, case-insensitively, which is what the single search
        field in the header is for.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if search:
            conditions.append("(name ILIKE %s OR ticker ILIKE %s)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        if verdict is not None:
            conditions.append("verdict = %s")
            params.append(verdict.value)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._fetch_all(
            f"SELECT {_OVERVIEW_COLUMNS} FROM strategy_overview {where} ORDER BY name", params
        )
        return [_overview(row) for row in rows]

    def descendants(self, strategy_id: UUID) -> list[StrategyRow]:
        """Strategies whose existence is recorded as coming from this one.

        Both lineage edges, because both are foreign keys into rows a purge would remove: a
        **fork** points at the strategy, and a **promotion** points at the strategy *and* at
        the run it was promoted out of. A promotion whose parent link were somehow written
        elsewhere would still be caught by the second arm, which is why this asks the database
        what points here rather than trusting the shape the service writes.

        ``DISTINCT`` because a promotion matches on both arms at once.
        """
        rows = self._fetch_all(
            f"""
            SELECT DISTINCT {_COLUMNS} FROM strategy
            WHERE parent_strategy_id = %s
               OR origin_run_id IN (SELECT id FROM run WHERE strategy_id = %s)
            ORDER BY name
            """,
            (strategy_id, strategy_id),
        )
        return [_strategy(row) for row in rows]

    def purge(self, strategy_id: UUID) -> PurgeCounts:
        """Delete a strategy and everything belonging to it. **Irreversible.**

        The only destructive operation in the application, and the only caller of the escape
        hatch migration 0002 opened. It does not decide *whether* to delete -- the refusals
        live in :func:`cracktrade.api.services.strategies.delete_strategy`, because a
        repository that decided would be a repository the service could forget to ask.

        Ordering is leaf-first because the foreign keys are real: series reference runs, runs
        reference versions through the composite key, versions reference the strategy. Counts
        are taken before the deletes rather than from ``rowcount`` on each statement, so the
        number reported is of the rows that existed rather than of the rows one statement
        happened to reach.
        """
        counts = self._counts(strategy_id)
        self._execute(f"SELECT set_config('{PURGE_SETTING}', %s, true)", (str(strategy_id),))
        self._execute(
            "DELETE FROM run_series WHERE run_id IN (SELECT id FROM run WHERE strategy_id = %s)",
            (strategy_id,),
        )
        self._execute("DELETE FROM run WHERE strategy_id = %s", (strategy_id,))
        self._execute("DELETE FROM strategy_version WHERE strategy_id = %s", (strategy_id,))
        self._execute("DELETE FROM strategy WHERE id = %s", (strategy_id,))
        # Withdrawn immediately rather than left for COMMIT to revert. The unit of work may go
        # on to do something else on this connection, and a permission that stays granted for
        # the rest of the transaction is wider than the statements that needed it.
        self._execute(f"SELECT set_config('{PURGE_SETTING}', '', true)")
        return counts

    def _counts(self, strategy_id: UUID) -> PurgeCounts:
        """What a purge of this strategy would destroy."""
        row = self._fetch_one(
            """
            SELECT
              (SELECT count(*) FROM strategy_version WHERE strategy_id = %s),
              (SELECT count(*) FROM run WHERE strategy_id = %s),
              (SELECT count(*) FROM run_series
                 WHERE run_id IN (SELECT id FROM run WHERE strategy_id = %s))
            """,
            (strategy_id, strategy_id, strategy_id),
        )
        assert row is not None  # scalar subqueries always yield one row
        return PurgeCounts(versions=int(row[0]), runs=int(row[1]), series=int(row[2]))

    def count_by_verdict(self) -> dict[Verdict, int]:
        """Totals behind the filter chips. Absent verdicts are reported as zero, not omitted."""
        rows = self._fetch_all("SELECT verdict, count(*) FROM strategy_overview GROUP BY verdict")
        counts = dict.fromkeys(Verdict, 0)
        for row in rows:
            counts[Verdict(row[0])] = int(row[1])
        return counts
