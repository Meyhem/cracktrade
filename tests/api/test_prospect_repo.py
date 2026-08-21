"""The prospecting repository, against a real database.

What is worth checking here is what the schema and the SQL share responsibility for: the claim
that lets a dead worker's sweep be picked up where it stopped, the cursor that *is* resume, the
leaderboard's ordering, and the trigger that refuses to let a forward score be revised.

The ordering test is the one that matters most. Spec section 19.5 forbids ranking on the
holdout figure the search selected on, and the enforcement is that the column does not exist --
so the test asserts what the leaderboard *does* rank on, and a separate test asserts the
holdout is not reachable as a column at all.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.errors import ConflictError, InvariantViolationError, NotFoundError
from cracktrade.api.repos import ProspectRepo
from cracktrade.api.repos.rows import ProspectStatus
from cracktrade.prospect import Rotation

pytestmark = pytest.mark.db

UNIVERSE = ("AMD", "NVDA", "SOXL")
PARAMS: dict[str, Any] = {"interval": "1h", "population": 30, "generations": 10}


def _session(
    db: psycopg.Connection[TupleRow], name: str = "overnight", universe: tuple[str, ...] = UNIVERSE
) -> UUID:
    row = ProspectRepo(db).create_session(name=name, universe=universe, params=PARAMS, seed=7)
    db.commit()
    return row.id


def _candidate(
    db: psycopg.Connection[TupleRow],
    session_id: UUID,
    *,
    ticker: str = "AMD",
    survived: bool = True,
    median: float = 0.4,
    control: float = -1.0,
    holdout: float = 12.0,
) -> UUID:
    row = ProspectRepo(db).add_candidate(
        session_id=session_id,
        ticker=ticker,
        discovered_at=datetime.now(UTC),
        last_bar_seen=date(2026, 8, 19),
        seed=7,
        candidate={"ticker": ticker, "holdout_return_pct": holdout, "transfer": {}},
        strategy_yaml=f"strategy:\n  name: found_{ticker}\n",
        survived_transfer=survived,
        transfer_median=median,
        transfer_control=control,
    )
    db.commit()
    return row.id


# --------------------------------------------------------------------------- sessions


def test_a_session_starts_running_with_its_question_frozen(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    created = repo.create_session(name="overnight", universe=UNIVERSE, params=PARAMS, seed=7)
    db.commit()

    assert created.status is ProspectStatus.RUNNING
    assert created.universe == UNIVERSE
    assert created.params == PARAMS
    assert (created.cursor_index, created.passes_completed) == (0, 0)
    assert created.stopped_at is None
    assert repo.get_session(created.id) == created
    assert repo.get_session(uuid4()) is None


def test_require_session_names_what_is_missing(db: psycopg.Connection[TupleRow]) -> None:
    with pytest.raises(NotFoundError):
        ProspectRepo(db).require_session(uuid4())


def test_an_empty_universe_is_refused_by_the_database(db: psycopg.Connection[TupleRow]) -> None:
    """A sweep with nothing to sweep is not a degenerate case to handle later."""
    with pytest.raises(InvariantViolationError):
        ProspectRepo(db).create_session(name="empty", universe=(), params=PARAMS, seed=0)


def test_listing_filters_by_status(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    live = _session(db, "live")
    done = _session(db, "done")
    repo.stop_session(done)
    db.commit()

    assert [row.id for row in repo.list_sessions(statuses=[ProspectStatus.RUNNING])] == [live]
    assert [row.id for row in repo.list_sessions(statuses=[ProspectStatus.STOPPED])] == [done]
    assert repo.count_sessions() == 2
    assert repo.count_sessions(statuses=[ProspectStatus.RUNNING]) == 1


# --------------------------------------------------------------------------- the lease


def test_one_worker_claims_and_the_next_finds_nothing(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    first = repo.claim_session("worker-a", lease_seconds=60.0)
    db.commit()
    assert first is not None
    assert first.id == session_id
    assert first.claimed_by == "worker-a"

    assert repo.claim_session("worker-b", lease_seconds=60.0) is None


def test_a_lapsed_lease_makes_a_session_claimable_again(db: psycopg.Connection[TupleRow]) -> None:
    """The difference from a run: a sweep interrupted mid-flight is resumed, not failed.

    Each tick is its own measurement and the completed ones are already durable, so picking the
    session back up continues one sweep rather than repeating a different one.
    """
    repo = ProspectRepo(db)
    _session(db)

    assert repo.claim_session("worker-a", lease_seconds=60.0) is not None
    db.commit()
    time.sleep(0.05)

    taken_over = repo.claim_session("worker-b", lease_seconds=0.01)
    db.commit()
    assert taken_over is not None
    assert taken_over.claimed_by == "worker-b"


def test_a_heartbeat_from_a_worker_that_lost_the_session_is_refused(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.claim_session("worker-a", lease_seconds=60.0)
    db.commit()

    assert repo.heartbeat_session(session_id, "worker-a") is True
    assert repo.heartbeat_session(session_id, "worker-b") is False


def test_releasing_hands_the_session_straight_to_the_next_worker(
    db: psycopg.Connection[TupleRow],
) -> None:
    """A restarted worker must not wait out a lease nobody is holding."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.claim_session("worker-a", lease_seconds=60.0)
    db.commit()

    assert repo.release_session(session_id, "worker-a") is True
    db.commit()

    picked_up = repo.claim_session("worker-b", lease_seconds=3600.0)
    assert picked_up is not None
    assert picked_up.claimed_by == "worker-b"


def test_a_stopped_session_is_never_claimed(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    repo.stop_session(_session(db))
    db.commit()

    assert repo.claim_session("worker-a", lease_seconds=60.0) is None


def test_a_session_asked_to_stop_is_still_claimable(db: psycopg.Connection[TupleRow]) -> None:
    """Somebody has to write the terminal row, and the ordinary claim is who."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.request_stop(session_id)
    db.commit()

    claimed = repo.claim_session("worker-a", lease_seconds=60.0)
    assert claimed is not None
    assert claimed.stop_requested is True


# --------------------------------------------------------------------------- the cursor


def test_a_tick_reserves_a_position_then_counts_itself(db: psycopg.Connection[TupleRow]) -> None:
    """The two halves a tick now writes, in the order it writes them. The cursor moves when the
    position is taken; the tally moves when the work comes back."""
    repo = ProspectRepo(db)
    session_id = _session(db)

    _, reserved = repo.reserve_ordinals(session_id, count=1)
    db.commit()
    assert (reserved.cursor_index, reserved.ticks_completed) == (1, 0)

    after = repo.count_tick(session_id)
    db.commit()
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (1, 1, 0)

    repo.reserve_ordinals(session_id, count=1)
    failed = repo.count_tick(session_id, failed=True)
    db.commit()
    assert (failed.cursor_index, failed.ticks_completed, failed.ticks_failed) == (2, 1, 1)

    wrapped, _ = repo.reserve_ordinals(session_id, count=1)
    db.commit()
    assert wrapped[0].ticker == "SOXL"
    assert repo.require_session(session_id).passes_completed == 1


def test_a_cursor_outside_the_universe_is_refused(db: psycopg.Connection[TupleRow]) -> None:
    """The check that makes resume safe: a restored cursor always names a real ticker.

    Asserted against the database directly rather than through a repository method. Since
    positions are handed out by ``reserve_ordinals``, which takes the ordinal modulo the universe
    size, no method here can produce an out-of-range cursor any more -- so what is worth pinning
    is that the constraint still refuses one whatever writes it.
    """
    session_id = _session(db)

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "UPDATE prospect_session SET cursor_index = %s WHERE id = %s",
            (len(UNIVERSE), session_id),
        )
    db.rollback()


def test_a_finished_session_accepts_no_further_ticks(db: psycopg.Connection[TupleRow]) -> None:
    """Both halves refuse, so a stopped session can neither hand out new work nor accept the
    result of work already in flight."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.stop_session(session_id)
    db.commit()

    with pytest.raises(ConflictError):
        repo.reserve_ordinals(session_id, count=1)
    db.rollback()
    with pytest.raises(ConflictError):
        repo.count_tick(session_id)


def test_stopping_twice_is_a_conflict_not_a_second_stop(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    stopped = repo.stop_session(session_id)
    db.commit()

    assert stopped.status is ProspectStatus.STOPPED
    assert stopped.stopped_at is not None
    assert stopped.claimed_by is None
    with pytest.raises(ConflictError):
        repo.stop_session(session_id)


def test_a_failed_session_records_why(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    failed = repo.fail_session(_session(db), message="no ticker in the universe has a family")
    db.commit()

    assert failed.status is ProspectStatus.FAILED
    assert failed.error == {"message": "no ticker in the universe has a family"}
    assert failed.stopped_at is not None


# --------------------------------------------------------------------------- candidates


def test_a_candidate_is_stored_verbatim_beside_its_promoted_columns(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    candidate_id = _candidate(db, session_id, median=0.62, control=-0.4)

    stored = repo.require_candidate(candidate_id)
    assert stored.candidate["holdout_return_pct"] == 12.0
    assert (stored.survived_transfer, stored.transfer_median, stored.transfer_control) == (
        True,
        0.62,
        -0.4,
    )


def test_the_holdout_return_is_not_a_column_anywhere(db: psycopg.Connection[TupleRow]) -> None:
    """Section 19.5's enforcement, checked rather than trusted.

    The holdout return is the number the search selected on and the number a reader most wants
    to sort by. It lives inside the jsonb precisely so that no query can casually ``ORDER BY``
    it, and this asserts nobody has since promoted it out for convenience.
    """
    row = db.execute(
        """
        SELECT count(*) FROM information_schema.columns
        WHERE table_name LIKE 'prospect_%' AND column_name LIKE '%holdout%'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_the_leaderboard_ranks_survivors_by_forward_then_transfer(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    rejected = _candidate(db, session_id, ticker="NVDA", survived=False, median=-0.2, holdout=99.0)
    unscored_best = _candidate(db, session_id, ticker="SOXL", median=0.9)
    scored_low = _candidate(db, session_id, ticker="AMD", median=0.5)

    repo.append_forward_score(
        candidate_id=scored_low,
        first_bar=date(2026, 8, 20),
        last_bar=date(2026, 8, 29),
        bars=70,
        return_pct=3.5,
        sharpe=0.9,
        trades=6,
    )
    db.commit()

    # Measured evidence outranks a better transfer figure with none.
    board = repo.leaderboard(session_id=session_id)
    assert [row.candidate.id for row in board] == [scored_low, unscored_best]
    assert board[0].forward is not None
    assert board[0].forward.return_pct == 3.5
    assert board[0].forward_scores == 1
    assert board[1].forward is None

    # The rejected candidate is visible where rejection is the point, and last.
    everything = repo.leaderboard(session_id=session_id, survivors_only=False)
    assert [row.candidate.id for row in everything][-1] == rejected
    assert repo.count_candidates(session_id=session_id) == 3
    assert repo.count_candidates(session_id=session_id, survivors_only=True) == 2


def test_the_leaderboard_filters_by_ticker(db: psycopg.Connection[TupleRow]) -> None:
    """Section 19.9: per-ticker, never pooled."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    _candidate(db, session_id, ticker="AMD")
    _candidate(db, session_id, ticker="NVDA")

    board = repo.leaderboard(session_id=session_id, ticker="AMD")
    assert [row.candidate.ticker for row in board] == ["AMD"]


# --------------------------------------------------------------------------- forward scores


def test_a_forward_score_can_record_an_undefined_sharpe(db: psycopg.Connection[TupleRow]) -> None:
    """Section 19.3: a window the candidate never traded has no Sharpe, and the column takes a
    NULL rather than the ``+inf`` the metric would otherwise carry.

    The return and the trade count are still recorded: the measurement happened, and "it stopped
    trading" is forward evidence worth showing. Only the ratio is missing.
    """
    repo = ProspectRepo(db)
    session_id = _session(db)
    candidate_id = _candidate(db, session_id)

    stored = repo.append_forward_score(
        candidate_id=candidate_id,
        first_bar=date(2026, 8, 20),
        last_bar=date(2026, 8, 29),
        bars=70,
        return_pct=0.0,
        sharpe=None,
        trades=0,
    )
    db.commit()

    assert stored.sharpe is None
    assert stored.return_pct == 0.0
    assert stored.trades == 0

    # And it reaches the leaderboard as a scored candidate, not an unscored one.
    board = repo.leaderboard(session_id=session_id)
    assert board[0].forward is not None
    assert board[0].forward.sharpe is None


def test_forward_scores_accumulate_as_a_series(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    candidate_id = _candidate(db, session_id)

    for last, bars, value in ((date(2026, 8, 27), 35, 3.0), (date(2026, 9, 3), 70, 1.2)):
        repo.append_forward_score(
            candidate_id=candidate_id,
            first_bar=date(2026, 8, 20),
            last_bar=last,
            bars=bars,
            return_pct=value,
            sharpe=value / 4,
            trades=bars // 10,
        )
    db.commit()

    assert [score.return_pct for score in repo.forward_scores(candidate_id)] == [3.0, 1.2]

    # The latest is what the leaderboard shows; the series is what makes the decay visible.
    board = repo.leaderboard(session_id=session_id)
    assert board[0].forward is not None
    assert board[0].forward.return_pct == 1.2
    assert board[0].forward_scores == 2


def test_a_forward_score_cannot_be_revised(db: psycopg.Connection[TupleRow]) -> None:
    """A score records a measurement against the prices as they stood that day.

    Enforced by the database rather than by this layer declining to offer an update: the
    repository has no such method, and if one were added the trigger would still refuse it.
    """
    repo = ProspectRepo(db)
    score = repo.append_forward_score(
        candidate_id=_candidate(db, _session(db)),
        first_bar=date(2026, 8, 20),
        last_bar=date(2026, 8, 29),
        bars=70,
        return_pct=3.5,
        sharpe=0.9,
        trades=6,
    )
    db.commit()

    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE prospect_forward_score SET return_pct = 99 WHERE id = %s", (score.id,))
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("DELETE FROM prospect_forward_score WHERE id = %s", (score.id,))
    db.rollback()

    assert [row.return_pct for row in repo.forward_scores(score.candidate_id)] == [3.5]


def test_scoring_takes_the_never_scored_first_then_the_stalest(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    scored = _candidate(db, session_id, ticker="AMD")
    never_scored = _candidate(db, session_id, ticker="SOXL")
    _candidate(db, session_id, ticker="NVDA", survived=False)

    repo.append_forward_score(
        candidate_id=scored,
        first_bar=date(2026, 8, 20),
        last_bar=date(2026, 8, 29),
        bars=70,
        return_pct=3.5,
        sharpe=0.9,
        trades=6,
    )
    db.commit()

    due = repo.due_for_forward_scoring(session_id=session_id, stale_after_hours=0.0)
    assert [row.id for row in due] == [never_scored, scored]

    # A candidate scored moments ago is not due again, and one that failed transfer never is.
    fresh = repo.due_for_forward_scoring(session_id=session_id, stale_after_hours=24.0)
    assert [row.id for row in fresh] == [never_scored]


# --------------------------------------------------------------------------- reservations


def test_a_reservation_advances_the_cursor_past_what_it_handed_out(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    reserved, after = repo.reserve_ordinals(session_id, count=2)

    assert [(r.ticker, r.ordinal) for r in reserved] == [("AMD", 0), ("NVDA", 1)]
    assert (after.cursor_index, after.passes_completed) == (2, 0)


def test_the_sql_advance_agrees_with_the_rotation_over_a_wraparound(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The advance exists twice -- in SQL because it must be atomic across workers, and in
    Rotation because that is where wrap-around is defined. This is what stops them drifting."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.reserve_ordinals(session_id, count=2)

    reserved, after = repo.reserve_ordinals(session_id, count=4)

    expected, rotation = Rotation(UNIVERSE, index=2, passes=0).reserve(4)
    assert reserved == expected
    assert (after.cursor_index, after.passes_completed) == (rotation.index, rotation.passes)


def test_two_concurrent_reservations_never_share_an_ordinal(db_url: str) -> None:
    """The whole reason the advance is one statement rather than a read then a write."""
    with psycopg.connect(db_url) as one, psycopg.connect(db_url) as two:
        session_id = (
            ProspectRepo(one)
            .create_session(name="race", universe=UNIVERSE, params=PARAMS, seed=7)
            .id
        )
        one.commit()

        first, _ = ProspectRepo(one).reserve_ordinals(session_id, count=3)
        one.commit()
        second, _ = ProspectRepo(two).reserve_ordinals(session_id, count=3)
        two.commit()

    assert sorted(r.ordinal for r in (*first, *second)) == [0, 1, 2, 3, 4, 5]


def test_an_abandoned_reservation_skips_its_tickers_and_the_next_pass_recovers_them(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Section 19.7 as amended: a dead worker's reserved positions are not retried. What keeps
    that from being data loss is round-robin -- the tickers are skipped for one pass, not
    dropped from the sweep."""
    repo = ProspectRepo(db)
    session_id = _session(db)

    abandoned, _ = repo.reserve_ordinals(session_id, count=2)  # nothing ever lands for these
    following, _ = repo.reserve_ordinals(session_id, count=3)

    assert [r.ticker for r in abandoned] == ["AMD", "NVDA"]
    assert [r.ordinal for r in following] == [2, 3, 4]
    assert {"AMD", "NVDA"} <= {r.ticker for r in following}


def test_a_reservation_is_refused_once_the_session_has_moved_on(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.claim_session("worker-b", lease_seconds=60.0)
    db.commit()

    with pytest.raises(ConflictError, match="no longer held by worker-a"):
        repo.reserve_ordinals(session_id, count=1, worker="worker-a")
    db.rollback()

    assert repo.require_session(session_id).cursor_index == 0


def test_a_reservation_of_nothing_is_refused(db: psycopg.Connection[TupleRow]) -> None:
    with pytest.raises(ValueError, match="positive count"):
        ProspectRepo(db).reserve_ordinals(_session(db), count=0)


def test_counting_a_tick_does_not_move_the_cursor(db: psycopg.Connection[TupleRow]) -> None:
    """Other ticks are in flight against positions already reserved. Writing a cursor here
    would rewind past them and hand those tickers out a second time."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.reserve_ordinals(session_id, count=3)

    after = repo.count_tick(session_id)

    assert (after.cursor_index, after.passes_completed) == (0, 1)
    assert (after.ticks_completed, after.ticks_failed) == (1, 0)


def test_a_failed_tick_is_counted_separately(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    after = repo.count_tick(session_id, failed=True)

    assert (after.ticks_completed, after.ticks_failed) == (0, 1)


def test_counting_is_refused_once_the_session_has_moved_on(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The takeover race, unchanged by the split: a candidate insert shares this transaction,
    so a refused count leaves nothing behind."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.claim_session("worker-b", lease_seconds=60.0)
    db.commit()

    with pytest.raises(ConflictError, match="no longer held by worker-a"):
        repo.count_tick(session_id, worker="worker-a")
    db.rollback()

    assert repo.require_session(session_id).ticks_completed == 0


# --------------------------------------------------------------------------- resume


def test_a_resumed_session_keeps_its_cursor_and_its_ledger(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The whole point. A session's compute can be rebought in minutes; its forward scores
    accumulate at 24h granularity and cannot be. Resume exists to keep the ledger."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.reserve_ordinals(session_id, count=5)
    repo.count_tick(session_id)
    _candidate(db, session_id, ticker="AMD")
    before = repo.require_session(session_id)
    repo.stop_session(session_id)
    db.commit()

    resumed = repo.resume_session(session_id)

    assert resumed.status is ProspectStatus.RUNNING
    assert resumed.stopped_at is None
    assert (resumed.cursor_index, resumed.passes_completed) == (
        before.cursor_index,
        before.passes_completed,
    )
    assert (resumed.universe, resumed.seed, resumed.params) == (
        before.universe,
        before.seed,
        before.params,
    )
    assert resumed.ticks_completed == before.ticks_completed
    assert repo.count_candidates(session_id=session_id) == 1


def test_resuming_clears_the_stop_request(db: psycopg.Connection[TupleRow]) -> None:
    """Without this the worker sees stop_requested on its first check and stops the session
    again immediately -- resume would look like it silently did nothing."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.request_stop(session_id)
    repo.stop_session(session_id)
    db.commit()
    assert repo.require_session(session_id).stop_requested

    resumed = repo.resume_session(session_id)

    assert not resumed.stop_requested


def test_a_resumed_session_is_claimable_again(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.stop_session(session_id)
    db.commit()
    assert repo.claim_session("worker", lease_seconds=60.0) is None

    repo.resume_session(session_id)
    db.commit()

    claimed = repo.claim_session("worker", lease_seconds=60.0)
    assert claimed is not None
    assert claimed.id == session_id


def test_a_failed_session_can_be_resumed_and_loses_its_error(
    db: psycopg.Connection[TupleRow],
) -> None:
    """``prospect_session_error_iff_failed`` requires error to be NULL once it is running, so
    resuming a failure has to clear it or the constraint refuses the write."""
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.fail_session(session_id, message="the provider refused every ticker")
    db.commit()

    resumed = repo.resume_session(session_id)

    assert resumed.status is ProspectStatus.RUNNING
    assert resumed.error is None
    assert resumed.stopped_at is None


def test_resuming_a_running_session_is_a_conflict(db: psycopg.Connection[TupleRow]) -> None:
    with pytest.raises(ConflictError, match="already running"):
        ProspectRepo(db).resume_session(_session(db))


def test_resuming_something_that_does_not_exist_is_a_conflict(
    db: psycopg.Connection[TupleRow],
) -> None:
    with pytest.raises(ConflictError):
        ProspectRepo(db).resume_session(uuid4())
