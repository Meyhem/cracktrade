"""Prospecting sessions, and the candidates they publish.

Five routes and no more. There is deliberately no endpoint that edits a running session's
universe or costs: a sweep whose question changed partway would make its own candidate list
incomparable with itself, and the ranking would then be reading the difference between the
questions rather than between the candidates.

There is also no endpoint that marks a candidate credible. Section 19.2 -- credibility is a
property of a strategy that has had its own walk-forward, and the path to one is the existing
promote flow, unchanged.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from cracktrade.api.dependencies import Settings, Work
from cracktrade.api.schemas.prospect import (
    CandidateDetailOut,
    CandidateList,
    CandidateOut,
    SessionList,
    SessionOut,
    StartSessionRequest,
)
from cracktrade.api.services.prospect import (
    Leaderboard as LeaderboardResult,
)
from cracktrade.api.services.prospect import (
    candidate_detail,
    leaderboard,
    list_sessions,
    parse_statuses,
    require_session,
    start,
    stop,
)

router = APIRouter(tags=["prospect"], prefix="/prospect")


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def post_session(body: StartSessionRequest, work: Work) -> SessionOut:
    """Begin a sweep. 201: the session exists and is running from this moment.

    Unlike a run this is not 202. There is nothing queued and nothing to wait for -- the record
    is complete as soon as it is written, and the worker joins a sweep that has already begun.
    """
    return SessionOut.of(
        start(work, name=body.name, universe=body.universe, params=body.params, seed=body.seed)
    )


@router.get("/sessions", response_model=SessionList)
def get_sessions(
    work: Work,
    session_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionList:
    """Every sweep, newest first."""
    listing = list_sessions(
        work, statuses=parse_statuses(session_status), limit=limit, offset=offset
    )
    return SessionList(sessions=[SessionOut.of(view) for view in listing.rows], total=listing.total)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: UUID, work: Work) -> SessionOut:
    """One sweep: where its cursor is, and how much it has found."""
    return SessionOut.of(require_session(work, session_id))


@router.post("/sessions/{session_id}/stop", response_model=SessionOut)
def post_stop(session_id: UUID, work: Work, settings: Settings) -> SessionOut:
    """Ask a sweep to stop.

    It ends now if no worker holds it, and after the current tick if one does -- a sweep never
    stops mid-search, because that search's compute would be paid for and its result thrown
    away. The response says which happened: ``stopped`` if it is over, ``running`` with
    ``stop_requested`` if the worker still has to land it.
    """
    return SessionOut.of(stop(work, session_id, lease_seconds=settings.worker_lease_seconds))


@router.get("/candidates", response_model=CandidateList)
def get_candidates(
    work: Work,
    session_id: Annotated[UUID | None, Query()] = None,
    ticker: Annotated[str | None, Query()] = None,
    survivors_only: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CandidateList:
    """The leaderboard.

    Ranked as section 19.5 permits: survivors first, then by forward return where any has
    accrued, then by transfer median. There is no ``sort`` parameter, and that is the point --
    the holdout return is the figure a reader most wants to sort by and the one the search
    selected on, so the ordering is not a client's to choose.

    ``survivors_only`` defaults true because that is the leaderboard. Passing false is for the
    session screen, where seeing what was rejected is the whole value.
    """
    found: LeaderboardResult = leaderboard(
        work,
        session_id=session_id,
        ticker=ticker.strip().upper() if ticker else None,
        survivors_only=survivors_only,
        limit=limit,
        offset=offset,
    )
    return CandidateList(candidates=[CandidateOut.of(row) for row in found.rows], total=found.total)


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailOut)
def get_candidate(candidate_id: UUID, work: Work) -> CandidateDetailOut:
    """One candidate, its strategy, and its whole forward series."""
    return CandidateDetailOut.of_detail(candidate_detail(work, candidate_id))
