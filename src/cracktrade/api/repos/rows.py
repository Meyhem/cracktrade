"""Row types returned by the repositories.

Frozen dataclasses, as in :mod:`cracktrade.domain`. Pydantic stays at the HTTP edge: a
repository that returned response models would tie the storage shape to the wire shape, and
changing either would then mean changing both.

Enums mirror the database's own types. They exist so that a typo in a status string is a
type error rather than a query that quietly matches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
    EVOLVE = "evolve"


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
    """A run plus the three facts its own table cannot know: the strategy's name, staleness,
    and the bar interval of the version it pinned."""

    run: RunRow
    strategy_name: str
    stale: bool
    #: Bar interval of the version this run pinned, never the head's -- see migration 0005.
    interval: str


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
    #: Never None: a config written before the interval existed ran on daily bars, and the view
    #: says so rather than making every consumer re-decide what a missing value meant.
    interval: str
    optimize_runs: int
    backtest_runs: int
    walk_forward_runs: int
    evolve_runs: int
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


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    """What a strategy delete destroyed, counted before the rows went.

    Returned so the interface can report the size of what it did rather than a bare success.
    A user who has just deleted seven runs should be told it was seven.
    """

    versions: int
    runs: int
    series: int


class ProspectStatus(StrEnum):
    """Where a prospecting session is in its lifecycle (spec section 19.7).

    Three states, not five. A session is never *queued* -- it starts running the moment it is
    created -- and never *succeeded*, because a search that has no end has nothing to succeed
    at. ``stopped`` is what a session that did its job looks like.
    """

    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether the session has finished and will claim no further ticks."""
        return self in {ProspectStatus.STOPPED, ProspectStatus.FAILED}


@dataclass(frozen=True, slots=True)
class ProspectSessionRow:
    """A long-running sweep: its question, its cursor, and its lease.

    ``cursor_index`` and ``passes_completed`` are the whole of what resume restores -- see
    :class:`~cracktrade.prospect.Rotation`, which is a value for exactly this reason.
    """

    id: UUID
    name: str
    status: ProspectStatus
    universe: tuple[str, ...]
    params: dict[str, Any]
    seed: int
    cursor_index: int
    passes_completed: int
    ticks_completed: int
    ticks_failed: int
    created_at: datetime
    stopped_at: datetime | None
    claimed_by: str | None
    heartbeat_at: datetime | None
    stop_requested: bool
    error: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ProspectCandidateRow:
    """One tick's finding, stored verbatim.

    ``candidate`` is :func:`cracktrade.serialize.to_dict` of a
    :class:`~cracktrade.prospect.Candidate` and is returned unaltered. The three promoted
    columns are the ones the leaderboard filters and sorts on; everything else -- the holdout
    figures above all -- stays inside the document, where a query cannot casually rank by it.
    """

    id: UUID
    session_id: UUID
    ticker: str
    discovered_at: datetime
    last_bar_seen: date
    seed: int
    candidate: dict[str, Any]
    strategy_yaml: str
    survived_transfer: bool
    transfer_median: float
    transfer_control: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProspectForwardScoreRow:
    """One measurement of a candidate on bars it was never shown.

    Append-only: a score is a record of a measurement taken against the prices as they stood
    that day, not a cache of the candidate's current worth.
    """

    id: UUID
    candidate_id: UUID
    scored_at: datetime
    first_bar: date
    last_bar: date
    bars: int
    return_pct: float
    #: ``None`` where the ratio is undefined -- a window without trades has no return variance
    #: to divide by. Not the same fact as a zero, nor as the absence of a score.
    sharpe: float | None
    trades: int


@dataclass(frozen=True, slots=True)
class ProspectCandidateOverviewRow:
    """A candidate beside its most recent forward score, which is what the leaderboard shows.

    ``forward`` is ``None`` until enough bars have arrived to measure anything, and the UI is
    required to say so rather than to render a blank as a zero (spec section 19.9).
    """

    candidate: ProspectCandidateRow
    forward: ProspectForwardScoreRow | None
    forward_scores: int
