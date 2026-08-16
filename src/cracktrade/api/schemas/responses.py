"""Response bodies, and the conversion from the layers below.

Rows and service results are frozen dataclasses; these are their wire form. The mapping is
explicit rather than automatic so that renaming a database column does not silently rename a
JSON field a client depends on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from cracktrade.api.errors import FieldIssue
from cracktrade.api.repos.rows import (
    StrategyOverviewRow,
    StrategyRow,
    VersionRow,
)
from cracktrade.api.services.config import ConfigReview
from cracktrade.api.services.diff import Change, SectionDiff
from cracktrade.serialize import to_dict


class Issue(BaseModel):
    """One validation problem, addressed to the field that caused it."""

    path: str
    message: str
    line: int | None = None
    suggestion: str | None = None

    @classmethod
    def of(cls, issue: FieldIssue) -> Issue:
        return cls(
            path=issue.path,
            message=issue.message,
            line=issue.line,
            suggestion=issue.suggestion,
        )


class SearchableParameterOut(BaseModel):
    """A parameter an optimization would move, and its bounds."""

    path: str
    value: float
    low: float
    high: float


class ValidateResponse(BaseModel):
    """The editor's answer. ``valid: false`` arrives with 200 -- invalidity is the answer."""

    valid: bool
    errors: list[Issue]
    warnings: list[Issue]
    canonical_yaml: str | None
    config: dict[str, Any] | None
    namespace: list[str]
    searchable_parameters: list[SearchableParameterOut]

    @classmethod
    def of(cls, review: ConfigReview) -> ValidateResponse:
        return cls(
            valid=review.valid,
            errors=[Issue.of(issue) for issue in review.errors],
            warnings=[Issue.of(issue) for issue in review.warnings],
            canonical_yaml=review.canonical_yaml,
            config=review.config,
            namespace=list(review.namespace),
            searchable_parameters=[
                SearchableParameterOut(
                    path=parameter.path,
                    value=parameter.value,
                    low=parameter.low,
                    high=parameter.high,
                )
                for parameter in review.searchable
            ],
        )


class Lineage(BaseModel):
    """Where a strategy came from."""

    origin: str
    parent_strategy_id: UUID | None = None
    parent_version: int | None = None
    origin_run_id: UUID | None = None


class VersionOut(BaseModel):
    """One immutable version."""

    version: int
    origin: str
    restored_from: int | None
    note: str | None
    created_at: datetime
    config: dict[str, Any]
    yaml: str

    @classmethod
    def of(cls, row: VersionRow) -> VersionOut:
        return cls(
            version=row.version,
            origin=row.origin.value,
            restored_from=row.restored_from,
            note=row.note,
            created_at=row.created_at,
            config=row.config,
            yaml=row.config_yaml,
        )


class ChangeOut(BaseModel):
    """One leaf that differs between two configurations."""

    path: str
    old: Any = None
    new: Any = None

    @classmethod
    def of(cls, change: Change) -> ChangeOut:
        return cls(path=change.path, old=change.old, new=change.new)


class VersionSummary(BaseModel):
    """A history-timeline entry: what changed, and what it means for earlier runs."""

    version: int
    head: bool
    origin: str
    restored_from: int | None
    note: str | None
    created_at: datetime
    change_summary: list[ChangeOut]
    flags: list[str]
    runs_against: int


class StrategySummary(BaseModel):
    """A row of the strategy list, verdict included."""

    id: UUID
    name: str
    ticker: str | None
    start_date: str | None
    end_date: str | None
    head_version: int
    edited_at: datetime
    created_at: datetime
    lineage: Lineage
    origin_not_credible: bool
    verdict: str
    verdict_run_id: UUID | None
    optimize_runs: int
    backtest_runs: int
    walk_forward_runs: int
    versions: int
    last_run_id: UUID | None
    last_run_kind: str | None
    last_run_status: str | None
    last_run_at: datetime | None

    @classmethod
    def of(cls, row: StrategyOverviewRow) -> StrategySummary:
        return cls(
            id=row.id,
            name=row.name,
            ticker=row.ticker,
            start_date=row.start_date,
            end_date=row.end_date,
            head_version=row.head_version,
            edited_at=row.edited_at,
            created_at=row.created_at,
            lineage=Lineage(
                origin=row.origin.value,
                parent_strategy_id=row.parent_strategy_id,
                parent_version=row.parent_version,
                origin_run_id=row.origin_run_id,
            ),
            origin_not_credible=row.origin_not_credible,
            verdict=row.verdict.value,
            verdict_run_id=row.verdict_run_id,
            optimize_runs=row.optimize_runs,
            backtest_runs=row.backtest_runs,
            walk_forward_runs=row.walk_forward_runs,
            versions=row.versions,
            last_run_id=row.last_run_id,
            last_run_kind=row.last_run_kind.value if row.last_run_kind else None,
            last_run_status=row.last_run_status.value if row.last_run_status else None,
            last_run_at=row.last_run_at,
        )


class StrategyList(BaseModel):
    """The list screen, with the totals its filter chips show."""

    strategies: list[StrategySummary]
    totals: dict[str, int]


class StrategyDetail(BaseModel):
    """The detail header plus the head configuration."""

    id: UUID
    name: str
    created_at: datetime
    lineage: Lineage
    origin_not_credible: bool
    head: VersionOut
    counts: dict[str, int]
    verdict: str
    verdict_run_id: UUID | None


class CreatedStrategy(BaseModel):
    """What a create, import or fork produced."""

    strategy: StrategyDetail
    warnings: list[Issue] = []


class SectionDiffOut(BaseModel):
    """What changed in one config section, and what that means."""

    section: str
    changes: list[ChangeOut]
    consequence: str | None

    @classmethod
    def of(cls, section: SectionDiff) -> SectionDiffOut:
        return cls(
            section=section.section,
            changes=[ChangeOut.of(change) for change in section.changes],
            consequence=section.consequence,
        )


class DiffResponse(BaseModel):
    """Two versions compared, grouped for reading and with both YAML panes."""

    from_version: VersionSummaryRef
    to_version: VersionSummaryRef
    groups: list[SectionDiffOut]
    from_yaml: str
    to_yaml: str


class VersionSummaryRef(BaseModel):
    """Enough of a version to label a diff pane."""

    version: int
    origin: str
    restored_from: int | None
    created_at: datetime
    head: bool

    @classmethod
    def of(cls, row: VersionRow, *, head: bool) -> VersionSummaryRef:
        return cls(
            version=row.version,
            origin=row.origin.value,
            restored_from=row.restored_from,
            created_at=row.created_at,
            head=head,
        )


class SavedVersion(BaseModel):
    """A newly appended version, and how many runs it just made stale."""

    version: VersionOut
    stale_runs: int


class MetaResponse(BaseModel):
    """Static engine facts. Serialised from the engine's own constants, never restated."""

    engine_version: str
    trade_floor: int
    significance: float
    instability_threshold: float
    objectives: list[str]
    fold_schemes: list[str]
    defaults: dict[str, Any]
    indicators: list[dict[str, Any]]
    exit_fields: list[dict[str, Any]]
    limits: list[str]


def strategy_detail(
    row: StrategyRow, overview: StrategyOverviewRow, head: VersionRow
) -> StrategyDetail:
    """Assemble the detail payload from the pieces each layer owns."""
    return StrategyDetail(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        lineage=Lineage(
            origin=row.origin.value,
            parent_strategy_id=row.parent_strategy_id,
            parent_version=row.parent_version,
            origin_run_id=row.origin_run_id,
        ),
        origin_not_credible=row.origin_not_credible,
        head=VersionOut.of(head),
        counts={
            "optimize": overview.optimize_runs,
            "backtest": overview.backtest_runs,
            "walk_forward": overview.walk_forward_runs,
            "versions": overview.versions,
        },
        verdict=overview.verdict.value,
        verdict_run_id=overview.verdict_run_id,
    )


def meta_payload(meta: Any) -> MetaResponse:
    """The metadata service's result, as the wire shape."""
    return MetaResponse(
        engine_version=meta.engine_version,
        trade_floor=meta.trade_floor,
        significance=meta.significance,
        instability_threshold=meta.instability_threshold,
        objectives=list(meta.objectives),
        fold_schemes=list(meta.fold_schemes),
        defaults={
            "backtest": to_dict(meta.backtest_defaults),
            "optimize": to_dict(meta.optimize_defaults),
            "walk_forward": to_dict(meta.walk_forward_defaults),
        },
        indicators=[to_dict(indicator) for indicator in meta.indicators],
        exit_fields=[to_dict(field) for field in meta.exit_fields],
        limits=list(meta.limits),
    )


DiffResponse.model_rebuild()
