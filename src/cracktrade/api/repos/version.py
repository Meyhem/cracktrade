"""Strategy versions: the append-only configuration history."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from cracktrade.api.errors import ConflictError, NotFoundError
from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.rows import VersionOrigin, VersionRow

_COLUMNS = """
  id, strategy_id, version, origin, restored_from, config, config_yaml, note, created_at
"""


def _version(row: tuple[Any, ...]) -> VersionRow:
    return VersionRow(
        id=row[0],
        strategy_id=row[1],
        version=row[2],
        origin=VersionOrigin(row[3]),
        restored_from=row[4],
        config=row[5],
        config_yaml=row[6],
        note=row[7],
        created_at=row[8],
    )


class VersionRepo(Repository):
    """Reads and appends ``strategy_version``. Nothing here updates or deletes."""

    def append(
        self,
        *,
        strategy_id: UUID,
        origin: VersionOrigin,
        config: dict[str, Any],
        config_yaml: str,
        after: int,
        note: str | None = None,
        restored_from: int | None = None,
    ) -> VersionRow:
        """Write the version following ``after``, or refuse.

        The number is computed inside the statement, from the strategy's own rows, rather than
        passed in. ``after`` is the version the caller believes is currently the head -- ``0``
        when writing a strategy's first -- and it is checked **in this statement**, not by the
        caller beforehand.

        That distinction is the whole point. A check in the service followed by an insert leaves
        a window, and under ``READ COMMITTED`` a second saver whose insert begins after the
        first one commits recomputes ``max(version)`` against the *new* head, takes the next
        number and succeeds. Both requests are then answered 201, both having declared they
        edited v1, and the second silently discarded the first: the last-write-wins failure spec
        section 15.2 forbids, wearing a success code. It was not a narrow window either -- two
        saves a couple of milliseconds apart opened it reliably.

        Guarded here there is no window. Either the ``HAVING`` sees a moved head and writes
        nothing, or two inserts overlap closely enough to compute the same number and the unique
        constraint rejects one. Both are a conflict, and the caller has no use for the
        difference.
        """
        row = self._fetch_one(
            f"""
            INSERT INTO strategy_version
              (strategy_id, version, origin, restored_from, config, config_yaml, note)
            SELECT
              %s,
              coalesce(max(version), 0) + 1,
              %s, %s, %s, %s, %s
            FROM strategy_version WHERE strategy_id = %s
            HAVING coalesce(max(version), 0) = %s
            RETURNING {_COLUMNS}
            """,
            (
                strategy_id,
                origin.value,
                restored_from,
                Jsonb(config),
                config_yaml,
                note,
                strategy_id,
                after,
            ),
        )
        if row is None:
            # Worded exactly as the unique-constraint path is translated: the two are the same
            # answer to the same question, and a caller that could tell them apart would only
            # be learning how close the race was.
            raise ConflictError("the strategy was modified concurrently; reload and retry")
        return _version(row)

    def head(self, strategy_id: UUID) -> VersionRow | None:
        """The current version -- what a run launches against and an edit is based on."""
        row = self._fetch_one(
            f"""
            SELECT {_COLUMNS} FROM strategy_version
            WHERE strategy_id = %s ORDER BY version DESC LIMIT 1
            """,
            (strategy_id,),
        )
        return _version(row) if row else None

    def require_head(self, strategy_id: UUID) -> VersionRow:
        """The head, or an error. A strategy without one cannot exist: v1 is written with it."""
        found = self.head(strategy_id)
        if found is None:
            raise NotFoundError(f"strategy {strategy_id} has no versions")
        return found

    def get(self, strategy_id: UUID, version: int) -> VersionRow | None:
        row = self._fetch_one(
            f"SELECT {_COLUMNS} FROM strategy_version WHERE strategy_id = %s AND version = %s",
            (strategy_id, version),
        )
        return _version(row) if row else None

    def require(self, strategy_id: UUID, version: int) -> VersionRow:
        found = self.get(strategy_id, version)
        if found is None:
            raise NotFoundError(f"strategy {strategy_id} has no version {version}")
        return found

    def list(self, strategy_id: UUID) -> list[VersionRow]:
        """Every version, newest first -- the order the History tab renders."""
        rows = self._fetch_all(
            f"SELECT {_COLUMNS} FROM strategy_version WHERE strategy_id = %s ORDER BY version DESC",
            (strategy_id,),
        )
        return [_version(row) for row in rows]

    def count_runs_against(self, strategy_id: UUID) -> dict[int, int]:
        """Runs per version, for the History tab's "N runs against this version" line."""
        rows = self._fetch_all(
            "SELECT version, count(*) FROM run WHERE strategy_id = %s GROUP BY version",
            (strategy_id,),
        )
        return {int(row[0]): int(row[1]) for row in rows}
