"""Starting, reading and stopping prospecting sessions.

Nothing here computes anything: starting a session writes one row and commits, and the worker
picks it up. The substance of this module is the *refusal* -- a universe or a search size that
cannot work is rejected at the request, not at three in the morning when the sweep reaches the
offending ticker and records a failure that looks like an engine fault.

The read side is deliberately thin. Section 19.5 fixes what a leaderboard may rank on and the
repository's ``ORDER BY`` is where that lives; re-sorting here would be a second, quieter place
for the same rule to be decided differently.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import NotFoundError, ValidationFailedError
from cracktrade.api.repos import ProspectRepo
from cracktrade.api.repos.rows import (
    ProspectCandidateOverviewRow,
    ProspectCandidateRow,
    ProspectForwardScoreRow,
    ProspectSessionRow,
    ProspectStatus,
)
from cracktrade.api.services.runs import (
    MAX_GENERATIONS,
    MAX_HOLDOUT_FRACTION,
    MAX_MIN_TRADES,
    MAX_MIN_TRADES_PER_YEAR,
    MAX_POPULATION,
    MAX_SEGMENTS,
    MIN_HOLDOUT_FRACTION,
    MIN_SEGMENTS,
)
from cracktrade.config import Interval
from cracktrade.errors import ProspectError
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, OBJECTIVES
from cracktrade.prospect import (
    DEFAULT_UNIVERSE,
    PROSPECT_SETTINGS,
    SweepParams,
    family_of,
    is_inverse,
)

#: Most tickers one session may sweep.
#:
#: A bound on the typo, not on the method. Rotation is round-robin, so a universe of two hundred
#: means each ticker is revisited about once a day at production search sizes -- and forward
#: evidence, which is what the leaderboard actually ranks on, accrues per candidate rather than
#: per session. A very wide sweep is not more thorough; it is the same compute spread thinner.
MAX_UNIVERSE = 40

#: Largest search size a *sweep* may ask for, below the ceiling a one-off evolve run gets.
#:
#: Section 19.1 measured it: the same ticker and seed with the budget scaled forty-fold produced
#: no improvement in-sample or out, and in one of two tickers the largest search came back worse
#: than the smallest. Meanwhile section 9.3's deflation threshold rises with the trial count, so
#: a bigger search raises its own bar without raising its result. A sweep runs this search
#: thousands of times, so the waste compounds in a way a single run's does not.
MAX_SWEEP_TRIALS = 2000


@dataclass(frozen=True, slots=True)
class SessionView:
    """A session together with the two counts its own row cannot know.

    Composed here rather than in the route. A route that reached into the repositories to count
    would be reaching past this layer, which spec section 15.4 forbids and
    ``tests/api/test_layering.py`` enforces.

    Counted on demand rather than kept as columns on the session. A counter the worker
    maintained would be one more thing that can disagree with the rows it counts, and section
    19.9 puts these two numbers on the screen as the honest statement of progress: a sweep with
    no end has no percentage, so "eleven candidates, three surviving" is what there is to say.
    They must not be able to drift from the list underneath them.
    """

    session: ProspectSessionRow
    candidates: int
    survivors: int


@dataclass(frozen=True, slots=True)
class SessionListing:
    """A page of sessions and the total behind it."""

    rows: tuple[SessionView, ...]
    total: int


@dataclass(frozen=True, slots=True)
class Leaderboard:
    """A page of candidates, each beside its most recent forward score."""

    rows: tuple[ProspectCandidateOverviewRow, ...]
    total: int


@dataclass(frozen=True, slots=True)
class CandidateDetail:
    """One candidate and its whole forward series.

    The series rather than the latest value: "this has been degrading for six weeks" is only
    visible as a sequence, and it is most of what a forward-validated ranking is for.
    """

    candidate: ProspectCandidateRow
    forward: tuple[ProspectForwardScoreRow, ...]


def normalise_universe(tickers: list[str] | None) -> tuple[str, ...]:
    """Clean and check a universe, or supply the default one.

    Every ticker must belong to a family. Transfer is the cheapest rejection the ladder has and
    the only one that is both immediate and decisive (section 19.3); a ticker without relatives
    could not be transfer-tested, so its candidates would be ranked against candidates that
    were. Discovering that when the sweep reaches it -- hours in, as a failed tick -- is far
    worse than saying so now.
    """
    requested = tickers if tickers else list(DEFAULT_UNIVERSE)
    seen: list[str] = []
    for raw in requested:
        ticker = raw.strip().upper()
        if ticker and ticker not in seen:
            seen.append(ticker)

    if not seen:
        raise ValidationFailedError("a sweep needs at least one ticker")
    if len(seen) > MAX_UNIVERSE:
        raise ValidationFailedError(
            f"a sweep may cover at most {MAX_UNIVERSE} tickers; rotation is round-robin, so a "
            f"wider universe is the same compute spread thinner rather than more search"
        )

    inverse = [ticker for ticker in seen if is_inverse(ticker)]
    if inverse:
        raise ValidationFailedError(
            f"{', '.join(inverse)}: inverse and volatility products are kept out of the sweep "
            f"(section 19.6). They decay structurally, so a long-only strategy that merely "
            f"avoids holding them scores well for a reason that has nothing to do with the "
            f"signal, and the benchmark comparison stops meaning anything"
        )

    unmapped: list[str] = []
    for ticker in seen:
        try:
            family_of(ticker)
        except ProspectError:
            unmapped.append(ticker)
    if unmapped:
        raise ValidationFailedError(
            f"{', '.join(unmapped)}: no family is defined for these, so a candidate found on "
            f"them could not be transfer-tested and would be ranked against candidates that "
            f"were. Add them to a family in the library, or leave them out"
        )
    return tuple(seen)


def normalise_params(params: dict[str, Any] | None) -> SweepParams:
    """Validate a sweep's question, filling in the engine's own defaults for what is absent.

    Bounded here rather than in the worker for the reason a launch is: a session that cannot
    possibly tick should never reach a worker, where every failure it produced would be
    recorded as a fault of the sweep.
    """
    supplied = params or {}

    interval = str(supplied.get("interval", Interval.H1.value))
    if interval not in {member.value for member in Interval}:
        raise ValidationFailedError(
            f"unknown interval {interval!r}; choose one of "
            f"{', '.join(member.value for member in Interval)}"
        )

    objective = str(supplied.get("objective", DEFAULT_OBJECTIVE))
    if objective not in OBJECTIVES:
        raise ValidationFailedError(
            f"unknown objective {objective!r}; choose one of {', '.join(OBJECTIVES)}"
        )

    population = int(supplied.get("population", PROSPECT_SETTINGS.population))
    if not 2 <= population <= MAX_POPULATION:
        raise ValidationFailedError(f"population must be between 2 and {MAX_POPULATION}")

    generations = int(supplied.get("generations", PROSPECT_SETTINGS.generations))
    if not 1 <= generations <= MAX_GENERATIONS:
        raise ValidationFailedError(f"generations must be between 1 and {MAX_GENERATIONS}")

    if population * generations > MAX_SWEEP_TRIALS:
        raise ValidationFailedError(
            f"population x generations must be at most {MAX_SWEEP_TRIALS} for a sweep, and "
            f"{population} x {generations} is {population * generations}. Section 19.1 measured "
            f"a forty-fold budget increase buying no improvement in or out of sample, while the "
            f"deflation threshold rises with the trial count -- a bigger search raises its own "
            f"bar without raising its result, thousands of times over"
        )

    segments = int(supplied.get("segments", 4))
    if not MIN_SEGMENTS <= segments <= MAX_SEGMENTS:
        raise ValidationFailedError(
            f"segments must be between {MIN_SEGMENTS} and {MAX_SEGMENTS}: the overfitting check "
            f"needs at least {MIN_SEGMENTS} to have anything to partition"
        )

    holdout = float(supplied.get("holdout_fraction", 0.2))
    if not MIN_HOLDOUT_FRACTION <= holdout <= MAX_HOLDOUT_FRACTION:
        raise ValidationFailedError(
            f"holdout_fraction must be between {MIN_HOLDOUT_FRACTION:g} and "
            f"{MAX_HOLDOUT_FRACTION:g}"
        )

    min_trades = int(supplied.get("min_trades", 20))
    if not 0 <= min_trades <= MAX_MIN_TRADES:
        raise ValidationFailedError(f"min_trades must be between 0 and {MAX_MIN_TRADES}")

    per_year = float(supplied.get("min_trades_per_year", 4.0))
    if not 0.0 <= per_year <= MAX_MIN_TRADES_PER_YEAR:
        raise ValidationFailedError(
            f"min_trades_per_year must be between 0 and {MAX_MIN_TRADES_PER_YEAR:g}"
        )

    return SweepParams(
        interval=Interval(interval),
        population=population,
        generations=generations,
        objective=objective,
        segments=segments,
        holdout_fraction=holdout,
        min_trades=min_trades,
        min_trades_per_year=per_year,
        initial_capital=float(supplied.get("initial_capital", 100_000.0)),
        slippage_pct=float(supplied.get("slippage_pct", 0.1)),
        commission_pct=float(supplied.get("commission_pct", 0.1)),
        risk_free_rate=float(supplied.get("risk_free_rate", 0.04)),
    )


def start(
    work: UnitOfWork,
    *,
    name: str,
    universe: list[str] | None = None,
    params: dict[str, Any] | None = None,
    seed: int | None = None,
) -> SessionView:
    """Begin a sweep. It is running from this moment; there is no queued state.

    A seed is recorded whether or not one was asked for, for the reason a run's is (defect
    D12): a search whose starting point is not written down cannot be repeated, and a candidate
    nobody can reproduce is a story rather than a result.
    """
    if not name.strip():
        raise ValidationFailedError("a sweep needs a name")
    created = ProspectRepo(work.connection).create_session(
        name=name.strip(),
        universe=normalise_universe(universe),
        params=normalise_params(params).to_dict(),
        seed=seed if seed is not None else secrets.randbelow(2**31),
    )
    return view_of(work, created)


def view_of(work: UnitOfWork, row: ProspectSessionRow) -> SessionView:
    """One session with its candidate counts attached."""
    repo = ProspectRepo(work.connection)
    return SessionView(
        session=row,
        candidates=repo.count_candidates(session_id=row.id),
        survivors=repo.count_candidates(session_id=row.id, survivors_only=True),
    )


def require_session(work: UnitOfWork, session_id: UUID) -> SessionView:
    return view_of(work, ProspectRepo(work.connection).require_session(session_id))


def list_sessions(
    work: UnitOfWork,
    *,
    statuses: list[ProspectStatus] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SessionListing:
    repo = ProspectRepo(work.connection)
    rows = repo.list_sessions(statuses=statuses, limit=limit, offset=offset)
    return SessionListing(
        rows=tuple(view_of(work, row) for row in rows),
        total=repo.count_sessions(statuses=statuses),
    )


def stop(work: UnitOfWork, session_id: UUID, *, lease_seconds: float) -> SessionView:
    """Stop a sweep, now if nobody is working it and after the current tick if somebody is.

    A live worker lands the terminal row itself, between ticks, so a sweep never stops in the
    middle of a search whose result would be paid for and thrown away. But a session no worker
    holds has nobody to observe the request, and a user pressing stop while the worker is down
    would otherwise watch a "stopping" state that never resolved.
    """
    repo = ProspectRepo(work.connection)
    repo.require_session(session_id)
    stopped = repo.stop_idle_session(session_id, lease_seconds=lease_seconds)
    return view_of(work, stopped if stopped is not None else repo.request_stop(session_id))


def resume(work: UnitOfWork, session_id: UUID) -> SessionView:
    """Put a finished sweep back to work, continuing from where it stopped.

    Unlike :func:`stop` there is no worker to coordinate with: a session that is not running is
    held by nobody, so the row can be flipped directly and the next claim picks it up.
    """
    repo = ProspectRepo(work.connection)
    repo.require_session(session_id)
    return view_of(work, repo.resume_session(session_id))


def leaderboard(
    work: UnitOfWork,
    *,
    session_id: UUID | None = None,
    ticker: str | None = None,
    survivors_only: bool = True,
    limit: int = 10,
    offset: int = 0,
) -> Leaderboard:
    """The candidates, ranked as section 19.5 permits and no other way.

    The ordering lives in the repository's ``ORDER BY``. Re-sorting here would give the one
    rule this feature exists to enforce a second place to be decided differently.
    """
    repo = ProspectRepo(work.connection)
    return Leaderboard(
        rows=tuple(
            repo.leaderboard(
                session_id=session_id,
                ticker=ticker,
                survivors_only=survivors_only,
                limit=limit,
                offset=offset,
            )
        ),
        total=repo.count_candidates(
            session_id=session_id, ticker=ticker, survivors_only=survivors_only
        ),
    )


def candidate_detail(work: UnitOfWork, candidate_id: UUID) -> CandidateDetail:
    repo = ProspectRepo(work.connection)
    found = repo.get_candidate(candidate_id)
    if found is None:
        raise NotFoundError(f"no prospecting candidate {candidate_id}")
    return CandidateDetail(candidate=found, forward=tuple(repo.forward_scores(candidate_id)))


def status_named(value: str) -> ProspectStatus:
    """Parse a status filter, refusing an unknown one rather than matching nothing."""
    try:
        return ProspectStatus(value)
    except ValueError as error:
        known = ", ".join(member.value for member in ProspectStatus)
        raise ValidationFailedError(f"unknown status {value!r}; choose one of {known}") from error


def parse_statuses(value: str | None) -> list[ProspectStatus] | None:
    """A comma-separated status filter, as the list screens send it."""
    if not value:
        return None
    return [status_named(part.strip()) for part in value.split(",") if part.strip()]
