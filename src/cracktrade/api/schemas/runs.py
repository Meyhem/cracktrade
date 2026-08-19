"""Run request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from cracktrade.api.repos.rows import RunOverviewRow
from cracktrade.api.schemas.requests import Body
from cracktrade.api.schemas.responses import VersionOut
from cracktrade.api.services.runs import ParameterMove, RunDetails, headline, is_promotable
from cracktrade.api.services.verdict import Check


class LaunchRunRequest(Body):
    """Launch a run against the strategy's head.

    No version field: a client cannot ask to run an old config. The head at launch time is
    pinned by the server, which is the only answer that stays true about what produced the
    numbers.
    """

    kind: str = Field(pattern="^(backtest|optimize|walk_forward|evolve)$")
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
    #: Bar width of the version this run pinned, not of the strategy's head. Every bar count in
    #: the result -- holding periods, warm-up, window lengths -- means a different span of
    #: calendar time depending on it, and the head may have moved on since.
    interval: str
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
            interval=row.interval,
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


class CheckOut(BaseModel):
    """One robustness check, exactly as the engine reported it."""

    name: str
    label: str
    passed: bool
    plain: str
    stat: str
    detail: str

    @classmethod
    def of(cls, check: Check) -> CheckOut:
        return cls(
            name=check.name,
            label=check.label,
            passed=check.passed,
            plain=check.plain,
            stat=check.stat,
            detail=check.detail,
        )


class ParameterMoveOut(BaseModel):
    """One parameter the winning config moved, against the run's own base version."""

    path: str
    old: Any = None
    new: Any = None
    low: float | None = None
    high: float | None = None
    at_bound: bool = False

    @classmethod
    def of(cls, move: ParameterMove) -> ParameterMoveOut:
        return cls(
            path=move.path,
            old=move.old,
            new=move.new,
            low=move.low,
            high=move.high,
            at_bound=move.at_bound,
        )


class RunDetail(BaseModel):
    """One run, with the engine's own serialised result passed through untouched.

    ``result`` is exactly what ``cracktrade.serialize.to_dict`` produced, contractual derived
    properties included (spec section 15.1). Nothing here reshapes it, and nothing recomputes a
    verdict from it: ``checks`` is the engine's own list, read out of that same result.
    """

    run: RunOut
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    checks: list[CheckOut]
    config_diff: list[ParameterMoveOut]
    #: The name the promote dialog pre-fills. ``None`` when the run cannot be promoted.
    default_promote_name: str | None

    @classmethod
    def of(cls, details: RunDetails) -> RunDetail:
        run = details.row.run
        return cls(
            run=RunOut.of(details.row),
            result=run.result,
            error=run.error,
            checks=[CheckOut.of(check) for check in details.checks],
            config_diff=[ParameterMoveOut.of(move) for move in details.moves],
            default_promote_name=details.default_promote_name,
        )


class PromoteRequest(Body):
    """The promote dialog. ``name`` defaults to the server's suggestion."""

    name: str | None = Field(default=None, min_length=1, max_length=200)


class PromotedStrategy(BaseModel):
    """What a promotion produced.

    ``carried_warning`` is not decoration: it says the new strategy starts flagged, and the
    client is expected to show that rather than present a fresh-looking strategy whose numbers
    came from an unvalidated search.
    """

    strategy_id: UUID
    version: VersionOut
    backtest_run_id: UUID
    carried_warning: bool


class SeriesCatalog(BaseModel):
    """Which chart series a run captured, and for which folds."""

    series: dict[str, list[int]]


class SeriesPoints(BaseModel):
    """One captured series, exactly as stored."""

    name: str
    fold: int
    points: dict[str, Any]
