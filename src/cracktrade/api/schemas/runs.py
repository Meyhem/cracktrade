"""Run request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from cracktrade.api.repos.rows import RunOverviewRow
from cracktrade.api.schemas.requests import Body
from cracktrade.api.services.runs import headline, is_promotable


class LaunchRunRequest(Body):
    """Launch a run against the strategy's head.

    No version field: a client cannot ask to run an old config. The head at launch time is
    pinned by the server, which is the only answer that stays true about what produced the
    numbers.
    """

    kind: str = Field(pattern="^(backtest|optimize|walk_forward)$")
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = Field(default=None, ge=0)


class RunOut(BaseModel):
    """A run as every list and header renders it."""

    id: UUID
    number: int
    kind: str
    status: str
    strategy: dict[str, Any]
    version: int
    stale: bool
    params: dict[str, Any]
    seed: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_seconds: float | None
    progress: dict[str, Any] | None
    failure_category: str | None
    headline: dict[str, Any] | None
    promotable: bool
    cancel_requested: bool

    @classmethod
    def of(cls, row: RunOverviewRow) -> RunOut:
        run = row.run
        elapsed = (
            (run.finished_at - run.started_at).total_seconds()
            if run.finished_at and run.started_at
            else None
        )
        return cls(
            id=run.id,
            number=run.number,
            kind=run.kind.value,
            status=run.status.value,
            strategy={"id": str(run.strategy_id), "name": row.strategy_name},
            version=run.version,
            stale=row.stale,
            params=run.params,
            seed=run.seed,
            queued_at=run.queued_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            elapsed_seconds=elapsed,
            progress=run.progress,
            failure_category=run.failure_category.value if run.failure_category else None,
            headline=headline(run),
            promotable=is_promotable(run),
            cancel_requested=run.cancel_requested,
        )


class RunList(BaseModel):
    """A page of runs."""

    runs: list[RunOut]
    total: int


class RunDetail(BaseModel):
    """One run, with the engine's own serialised result passed through untouched.

    ``result`` is exactly what ``cracktrade.serialize.to_dict`` produced, contractual derived
    properties included (spec section 15.1). Nothing here reshapes it, and nothing recomputes a
    verdict from it.
    """

    run: RunOut
    result: dict[str, Any] | None
    error: dict[str, Any] | None


class SeriesCatalog(BaseModel):
    """Which chart series a run captured, and for which folds."""

    series: dict[str, list[int]]


class SeriesPoints(BaseModel):
    """One captured series, exactly as stored."""

    name: str
    fold: int
    points: dict[str, Any]
