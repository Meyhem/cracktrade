"""Prospecting request and response bodies.

The naming here is load-bearing. Section 19.9 requires the interface to keep three things
separate that a careless schema would flatten into one number: what the search *selected on*,
what transfer *rejected on*, and what forward evidence has *accrued*. So the holdout figures
are nested under ``selected_on`` rather than sitting beside the forward ones as peers, and
``forward`` is nullable rather than defaulting to zero -- "not measured yet" and "measured and
flat" must not render the same.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from cracktrade.api.repos.rows import (
    ProspectCandidateOverviewRow,
    ProspectCandidateRow,
    ProspectForwardScoreRow,
)
from cracktrade.api.schemas.requests import Body
from cracktrade.api.services.prospect import CandidateDetail, SessionView


class StartSessionRequest(Body):
    """Begin a sweep.

    Every field is optional except the name: the defaults are the twenty instruments section
    19.6 screened and the small search section 19.1 measured, and a user who has no opinion
    should get the configuration that was actually tested rather than an empty form.
    """

    name: str = Field(min_length=1, max_length=120)
    universe: list[str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = Field(default=None, ge=0)


class SessionOut(BaseModel):
    """A sweep as the list and the header render it.

    ``progress`` is deliberately absent. Section 19.9: a search with no end has no percentage,
    and the honest statement of where a sweep has got to is how many tickers it has covered and
    how many candidates survived -- both of which are here as counts.
    """

    id: UUID
    name: str
    status: str
    universe: list[str]
    params: dict[str, Any]
    seed: int
    #: The ticker being prospected next. Named for what it is rather than as a percentage.
    current_ticker: str
    cursor_index: int
    passes_completed: int
    ticks_completed: int
    ticks_failed: int
    candidates: int
    survivors: int
    created_at: datetime
    stopped_at: datetime | None
    claimed_by: str | None
    heartbeat_at: datetime | None
    stop_requested: bool
    error: dict[str, Any] | None

    @classmethod
    def of(cls, view: SessionView) -> SessionOut:
        row = view.session
        return cls(
            id=row.id,
            name=row.name,
            status=row.status.value,
            universe=list(row.universe),
            params=row.params,
            seed=row.seed,
            current_ticker=row.universe[row.cursor_index],
            cursor_index=row.cursor_index,
            passes_completed=row.passes_completed,
            ticks_completed=row.ticks_completed,
            ticks_failed=row.ticks_failed,
            candidates=view.candidates,
            survivors=view.survivors,
            created_at=row.created_at,
            stopped_at=row.stopped_at,
            claimed_by=row.claimed_by,
            heartbeat_at=row.heartbeat_at,
            stop_requested=row.stop_requested,
            error=row.error,
        )


class SessionList(BaseModel):
    sessions: list[SessionOut]
    total: int


class ForwardScoreOut(BaseModel):
    """One measurement on bars the candidate was never shown."""

    scored_at: datetime
    first_bar: date
    last_bar: date
    bars: int
    return_pct: float
    sharpe: float
    trades: int

    @classmethod
    def of(cls, row: ProspectForwardScoreRow) -> ForwardScoreOut:
        return cls(
            scored_at=row.scored_at,
            first_bar=row.first_bar,
            last_bar=row.last_bar,
            bars=row.bars,
            return_pct=row.return_pct,
            sharpe=row.sharpe,
            trades=row.trades,
        )


class SelectedOn(BaseModel):
    """The figures the search chose this candidate by. **Not evidence.**

    Nested under their own name rather than sitting beside the forward figures as peers,
    because a reader scanning a row will believe whatever the biggest number says and these are
    the numbers most likely to be large and least likely to mean anything. Section 19.5 forbids
    ranking on them and the storage layer does not promote them to a column; this is the same
    rule applied to the wire shape.
    """

    holdout_return_pct: float
    holdout_sharpe: float
    #: Closed trades behind the two figures above, so a result resting on four is visible.
    holdout_trades: int
    median_segment_return_pct: float
    #: How many strategies were considered to produce this one -- the trial count section 9.3's
    #: deflation divides by.
    distinct_configurations: int


class TransferOut(BaseModel):
    """How the candidate travelled across its family, and why that was or was not enough."""

    family: str
    home_sharpe: float
    median_sibling_sharpe: float
    median_control_sharpe: float
    members: int
    negative_members: int
    beats_controls: bool
    survives: bool
    failures: list[str]


class CandidateOut(BaseModel):
    """One candidate, with its three kinds of number kept apart.

    ``forward`` is ``None`` until enough bars have arrived to measure anything. A client must
    render that as "not yet", never as a zero: the difference between unmeasured and measured-
    and-flat is the whole point of the rung.
    """

    id: UUID
    session_id: UUID
    ticker: str
    discovered_at: datetime
    last_bar_seen: date
    seed: int
    composition: str
    blocks: list[str]
    strategy_yaml: str
    survived_transfer: bool
    selected_on: SelectedOn
    transfer: TransferOut
    forward: ForwardScoreOut | None
    forward_scores: int

    @classmethod
    def of(cls, row: ProspectCandidateOverviewRow) -> CandidateOut:
        return cls(
            **_candidate_fields(row.candidate),
            forward=ForwardScoreOut.of(row.forward) if row.forward else None,
            forward_scores=row.forward_scores,
        )


class CandidateDetailOut(CandidateOut):
    """A candidate and its whole forward series.

    The series rather than the latest point: a candidate that has decayed for six weeks and one
    found last Tuesday can show the same latest figure, and only the sequence tells them apart.
    """

    forward_history: list[ForwardScoreOut]

    @classmethod
    def of_detail(cls, detail: CandidateDetail) -> CandidateDetailOut:
        return cls(
            **_candidate_fields(detail.candidate),
            forward=ForwardScoreOut.of(detail.forward[-1]) if detail.forward else None,
            forward_scores=len(detail.forward),
            forward_history=[ForwardScoreOut.of(row) for row in detail.forward],
        )


class CandidateList(BaseModel):
    candidates: list[CandidateOut]
    total: int


def _candidate_fields(row: ProspectCandidateRow) -> dict[str, Any]:
    """Unpack the stored document into the wire shape.

    Read from the ``jsonb`` rather than from promoted columns, because most of it deliberately
    has none -- see migration 0006 on why the holdout return is not a column. Every lookup is
    defensive: a candidate written by an earlier version must still render, and a missing key
    is a gap in an old row rather than a reason to fail the request.
    """
    stored: dict[str, Any] = row.candidate
    transfer: dict[str, Any] = stored.get("transfer") or {}
    return {
        "id": row.id,
        "session_id": row.session_id,
        "ticker": row.ticker,
        "discovered_at": row.discovered_at,
        "last_bar_seen": row.last_bar_seen,
        "seed": row.seed,
        "composition": str(stored.get("composition", "")),
        "blocks": list(stored.get("blocks") or []),
        "strategy_yaml": row.strategy_yaml,
        "survived_transfer": row.survived_transfer,
        "selected_on": SelectedOn(
            holdout_return_pct=float(stored.get("holdout_return_pct", 0.0)),
            holdout_sharpe=float(stored.get("holdout_sharpe", 0.0)),
            holdout_trades=int(stored.get("holdout_trades", 0)),
            median_segment_return_pct=float(stored.get("median_segment_return_pct", 0.0)),
            distinct_configurations=int(stored.get("distinct_configurations", 0)),
        ),
        "transfer": TransferOut(
            family=str(transfer.get("family", "")),
            home_sharpe=float(transfer.get("home_sharpe", 0.0)),
            # Promoted columns rather than the document for these two: they are what the
            # leaderboard sorted by, and the row must show the figure it was ranked on.
            median_sibling_sharpe=row.transfer_median,
            median_control_sharpe=row.transfer_control,
            members=sum(
                1 for result in (transfer.get("results") or []) if not result.get("is_control")
            ),
            negative_members=int(transfer.get("negative_members", 0)),
            beats_controls=bool(transfer.get("beats_controls", False)),
            survives=row.survived_transfer,
            failures=list(transfer.get("failures") or []),
        ),
    }
