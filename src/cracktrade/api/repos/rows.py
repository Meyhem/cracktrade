"""Row types returned by the repositories.

Frozen dataclasses, as in :mod:`cracktrade.domain`. Pydantic stays at the HTTP edge: a
repository that returned response models would tie the storage shape to the wire shape, and
changing either would then mean changing both.

Enums mirror the database's own types. They exist so that a typo in a status string is a
type error rather than a query that quietly matches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class StrategyOrigin(StrEnum):
    """How a strategy came to exist."""

    AUTHORED = "authored"
    IMPORTED = "imported"
    FORKED = "forked"
    PROMOTED = "promoted"


class VersionOrigin(StrEnum):
    """How one version came to exist."""

    CREATED = "created"
    IMPORTED = "imported"
    FORKED = "forked"
    PROMOTED = "promoted"
    EDITED = "edited"
    RESTORED = "restored"


class RunKind(StrEnum):
    """What a run asked the engine to do."""

    BACKTEST = "backtest"
    OPTIMIZE = "optimize"
    WALK_FORWARD = "walk_forward"


class RunStatus(StrEnum):
    """Where a run is in its lifecycle (spec section 14.5)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Whether the run has finished and its row is now frozen."""
        return self in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


class FailureCategory(StrEnum):
    """Why a run failed. Maps 1:1 onto engine exit codes 2..5 (spec section 13.3)."""

    CONFIG_INVALID = "config_invalid"
    MARKET_DATA = "market_data"
    ENGINE_FAILURE = "engine_failure"
    CAUSALITY_VIOLATION = "causality_violation"


class Verdict(StrEnum):
    """A strategy's credibility, derived per spec section 14.4."""

    CREDIBLE = "credible"
    NOT_CREDIBLE = "not_credible"
    UNVALIDATED = "unvalidated"
    NEVER_RUN = "never_run"


@dataclass(frozen=True, slots=True)
class StrategyRow:
    """A row of ``strategy``: identity and lineage, without any config."""

    id: UUID
    name: str
    origin: StrategyOrigin
    parent_strategy_id: UUID | None
    parent_version: int | None
    origin_run_id: UUID | None
    origin_not_credible: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VersionRow:
    """One immutable version of a strategy's configuration."""

    id: UUID
    strategy_id: UUID
    version: int
    origin: VersionOrigin
    restored_from: int | None
    config: dict[str, Any]
    config_yaml: str
    note: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunRow:
    """One execution of the engine against one exact version.

    ``result`` and ``error`` are the engine's own serialisation, stored verbatim and returned
    unaltered (spec section 15.1). The repository does not look inside them.
    """

    id: UUID
    strategy_id: UUID
    version: int
    number: int
    kind: RunKind
    status: RunStatus
    params: dict[str, Any]
    seed: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    claimed_by: str | None
    heartbeat_at: datetime | None
    cancel_requested: bool
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    failure_category: FailureCategory | None
    is_credible: bool | None
    suppressed: bool | None


@dataclass(frozen=True, slots=True)
class RunOverviewRow:
    """A run plus the two facts its own table cannot know: the strategy's name, and staleness."""

    run: RunRow
    strategy_name: str
    stale: bool


@dataclass(frozen=True, slots=True)
class StrategyOverviewRow:
    """Everything the strategy list renders, with the derived verdict (spec section 14.4)."""

    id: UUID
    name: str
    origin: StrategyOrigin
    parent_strategy_id: UUID | None
    parent_version: int | None
    origin_run_id: UUID | None
    origin_not_credible: bool
    created_at: datetime
    head_version: int
    edited_at: datetime
    ticker: str | None
    start_date: str | None
    end_date: str | None
    optimize_runs: int
    backtest_runs: int
    walk_forward_runs: int
    versions: int
    last_run_id: UUID | None
    last_run_kind: RunKind | None
    last_run_status: RunStatus | None
    last_run_at: datetime | None
    verdict: Verdict
    verdict_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class SeriesRow:
    """One captured chart series. ``fold`` 0 is the whole run."""

    run_id: UUID
    name: str
    fold: int
    points: dict[str, Any]
