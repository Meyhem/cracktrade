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
from cracktrade.api.repos.rows import StrategyOverviewRow, VersionRow
from cracktrade.api.services.config import ConfigComparison, ConfigReview
from cracktrade.api.services.diff import Change, SectionDiff
from cracktrade.api.services.health import Health
from cracktrade.api.services.strategies import StrategyDetails
from cracktrade.api.services.verdict import PromotedWarning, VerdictBlock
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


class VerdictCheck(BaseModel):
    """One robustness check behind the verdict, as the engine reported it."""

    name: str
    label: str
    passed: bool
    plain: str
    stat: str
    detail: str


class VerdictBlockOut(BaseModel):
    """The verdict banner: the state, the run that produced it, and what failed.

    ``failures`` is the engine's own list and is present whenever the verdict is
    ``not_credible``. A client rendering the state without it would show a red badge with no
    reason, which is the fastest way to teach someone to ignore the badge.
    """

    state: str
    run_id: UUID | None = None
    run_number: int | None = None
    version: int | None = None
    finished_at: str | None = None
    summary: str | None = None
    failures: list[str] = []
    checks: list[VerdictCheck] = []
    meta: str | None = None

    @classmethod
    def of(cls, verdict: VerdictBlock) -> VerdictBlockOut:
        return cls(
            state=verdict.state.value,
            run_id=verdict.run_id,
            run_number=verdict.run_number,
            version=verdict.version,
            finished_at=verdict.finished_at,
            summary=verdict.summary,
            failures=list(verdict.failures),
            checks=[
                VerdictCheck(
                    name=check.name,
                    label=check.label,
                    passed=check.passed,
                    plain=check.plain,
                    stat=check.stat,
                    detail=check.detail,
                )
                for check in verdict.checks
            ],
            meta=verdict.meta,
        )


class PromotedWarningOut(BaseModel):
    """Shown until a promoted strategy's own walk-forward passes."""

    origin_run_id: UUID
    origin_run_number: int
    parent_name: str | None
    text: str

    @classmethod
    def of(cls, warning: PromotedWarning) -> PromotedWarningOut:
        return cls(
            origin_run_id=warning.origin_run_id,
            origin_run_number=warning.origin_run_number,
            parent_name=warning.parent_name,
            text=warning.text,
        )


class StrategyDetail(BaseModel):
    """The detail header plus the head configuration."""

    id: UUID
    name: str
    created_at: datetime
    lineage: Lineage
    origin_not_credible: bool
    head: VersionOut
    counts: dict[str, int]
    verdict: VerdictBlockOut
    verdict_run_id: UUID | None
    promoted_warning: PromotedWarningOut | None


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


class ConfigDiffResponse(BaseModel):
    """Two configurations compared. No version refs: one side may not be a version at all."""

    groups: list[SectionDiffOut]
    from_yaml: str
    to_yaml: str

    @classmethod
    def of(cls, comparison: ConfigComparison) -> ConfigDiffResponse:
        return cls(
            groups=[SectionDiffOut.of(section) for section in comparison.groups],
            from_yaml=comparison.from_yaml,
            to_yaml=comparison.to_yaml,
        )


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


class HealthResponse(BaseModel):
    """What the process can honestly say about itself."""

    ok: bool
    database: bool
    migrations_current: bool
    applied_migrations: int
    pending_migrations: list[str]
    migration_refusal: str | None
    version: str

    @classmethod
    def of(cls, report: Health) -> HealthResponse:
        return cls(
            ok=report.ok,
            database=report.database,
            migrations_current=report.migrations_current,
            applied_migrations=report.applied,
            pending_migrations=list(report.pending),
            migration_refusal=report.refusal,
            version=report.version,
        )


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


def strategy_detail(details: StrategyDetails) -> StrategyDetail:
    """Assemble the detail payload from the pieces each layer owns."""
    row, overview = details.strategy, details.overview
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
        head=VersionOut.of(details.head),
        counts={
            "optimize": overview.optimize_runs,
            "backtest": overview.backtest_runs,
            "walk_forward": overview.walk_forward_runs,
            "versions": overview.versions,
        },
        verdict=VerdictBlockOut.of(details.verdict),
        verdict_run_id=overview.verdict_run_id,
        promoted_warning=(
            PromotedWarningOut.of(details.warning) if details.warning is not None else None
        ),
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
