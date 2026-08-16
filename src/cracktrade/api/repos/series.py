"""Captured chart series.

Written once, by the worker, at the moment the run executes. Never recomputed: prices are
retroactively adjusted, so a series rebuilt later would describe different data than the run's
own numbers and its vintage block (spec section 14.1).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from cracktrade.api.errors import NotFoundError
from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.rows import SeriesRow

#: ``fold`` value meaning "the whole run" rather than one walk-forward fold.
WHOLE_RUN = 0


class SeriesRepo(Repository):
    """Reads and writes ``run_series``. Append-only, like everything a run produces."""

    def put(
        self, *, run_id: UUID, name: str, points: dict[str, Any], fold: int = WHOLE_RUN
    ) -> SeriesRow:
        """Store one series. Rejected by the append-only trigger if it already exists."""
        row = self._fetch_one(
            """
            INSERT INTO run_series (run_id, name, fold, points)
            VALUES (%s, %s, %s, %s)
            RETURNING run_id, name, fold, points
            """,
            (run_id, name, fold, Jsonb(points)),
        )
        assert row is not None
        return SeriesRow(run_id=row[0], name=row[1], fold=row[2], points=row[3])

    def get(self, run_id: UUID, name: str, fold: int = WHOLE_RUN) -> SeriesRow | None:
        row = self._fetch_one(
            "SELECT run_id, name, fold, points FROM run_series "
            "WHERE run_id = %s AND name = %s AND fold = %s",
            (run_id, name, fold),
        )
        if row is None:
            return None
        return SeriesRow(run_id=row[0], name=row[1], fold=row[2], points=row[3])

    def require(self, run_id: UUID, name: str, fold: int = WHOLE_RUN) -> SeriesRow:
        found = self.get(run_id, name, fold)
        if found is None:
            raise NotFoundError(f"run {run_id} has no series {name!r} for fold {fold}")
        return found

    def catalog(self, run_id: UUID) -> dict[str, list[int]]:
        """Which series a run captured, and for which folds -- the charts tab's index."""
        rows = self._fetch_all(
            "SELECT name, fold FROM run_series WHERE run_id = %s ORDER BY name, fold", (run_id,)
        )
        catalog: dict[str, list[int]] = {}
        for row in rows:
            catalog.setdefault(str(row[0]), []).append(int(row[1]))
        return catalog
