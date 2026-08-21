"""The prospecting worker: one tick, the lease it holds, and the forward pass behind it.

The searches here are deliberately tiny -- four genomes over two generations, with the trade
floor switched off. What is being tested is the *lifecycle*: that a tick stores what it found
and advances the cursor, that a failed ticker does not end the sweep, that a session is released
rather than held, and that forward scoring writes nothing when there is nothing honest to write.
Running a real search at its production size would prove none of that and would make the suite
slow enough that nobody ran it.

Nothing touches the network: every ticker is served from a generated frame.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.errors import ConflictError
from cracktrade.api.repos import ProspectRepo, RunRepo, StrategyRepo, VersionRepo
from cracktrade.api.repos.rows import (
    ProspectStatus,
    RunKind,
    StrategyOrigin,
    VersionOrigin,
)
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker.prospect import claim_and_sweep, score_due, should_yield, tick
from cracktrade.config import Interval
from cracktrade.data import StaticProvider
from cracktrade.indicators.catalogue import install
from cracktrade.prospect import SweepParams, family_of

pytestmark = pytest.mark.db

#: The family one tick exercises, plus its controls. AMD's family is the largest in the
#: library, which makes it the honest case to test: a tick that works here works everywhere.
HOME = "AMD"
FAMILY = family_of(HOME)
TICKERS = (*FAMILY.members, *FAMILY.controls)

#: Small enough to run in seconds, with the trade floor off. With the floor at its default
#: these tests would pass or fail on whether a four-genome search happened to find something
#: that trades enough -- a fact about luck and the market generator, not about the worker.
PARAMS = SweepParams(
    interval=Interval.D1,
    population=4,
    generations=2,
    min_trades=0,
    min_trades_per_year=0.0,
    lookback_days=1200,
)


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


def _frame(bars: int, seed: int) -> pd.DataFrame:
    """A random walk on business days ending today, so a rolling window always reaches it."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, bars)))
    index = pd.DatetimeIndex(
        pd.date_range(end=pd.Timestamp.now().normalize(), periods=bars, freq="B").to_numpy(),
        name="Date",
    )
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(bars, 1_000_000.0),
        },
        index=index,
    )


def _provider(*, missing: tuple[str, ...] = ()) -> StaticProvider:
    return StaticProvider(
        {ticker: _frame(700, seed) for seed, ticker in enumerate(TICKERS) if ticker not in missing}
    )


def _settings(*, parallelism: int = 1) -> ApiSettings:
    return ApiSettings(
        worker_lease_seconds=60.0,
        worker_heartbeat_seconds=30.0,
        prospect_parallelism=parallelism,
    )


def _queue_a_run(db: psycopg.Connection[TupleRow]) -> None:
    """A backtest waiting behind the sweep.

    Also the tests' way of bounding a sweep: a queued run is what makes it stop reserving, so a
    sweep launched with one already queued does exactly one window of ticks and drains.
    """
    strategy = StrategyRepo(db).create(name="waiting", origin=StrategyOrigin.AUTHORED)
    version = VersionRepo(db).append(
        strategy_id=strategy.id,
        origin=VersionOrigin.CREATED,
        config={"strategy": {"name": "waiting"}},
        config_yaml="strategy:\n  name: waiting\n",
        after=0,
    )
    RunRepo(db).create(
        strategy_id=strategy.id,
        version=version.version,
        kind=RunKind.BACKTEST,
        params={},
        seed=0,
    )
    db.commit()


def _session(
    db: psycopg.Connection[TupleRow],
    *,
    universe: tuple[str, ...] = (HOME, "NVDA"),
    name: str = "overnight",
) -> Any:
    row = ProspectRepo(db).create_session(
        name=name, universe=universe, params=PARAMS.to_dict(), seed=11
    )
    db.commit()
    return row


# --------------------------------------------------------------------------- one tick


def test_a_tick_stores_what_it_found_against_its_reservation(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)

    reserved, taken = repo.reserve_ordinals(session.id, count=1)
    assert taken.cursor_index == 1  # the position is spent when it is handed out
    outcome = tick(db, session, reserved[0], provider=_provider())

    assert outcome.ticker == HOME
    assert outcome.error is None
    assert outcome.candidate is not None
    assert outcome.candidate.ticker == HOME

    after = repo.require_session(session.id)
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (1, 1, 0)

    stored = repo.leaderboard(session_id=session.id, survivors_only=False)
    assert len(stored) == 1
    row = stored[0].candidate
    assert row.ticker == HOME
    assert row.strategy_yaml == outcome.candidate.strategy_yaml
    # The transfer report travels with the candidate: a leaderboard must never be able to show
    # the holdout figure without it (section 19.2).
    assert row.candidate["transfer"]["family"] == FAMILY.name
    assert row.survived_transfer == outcome.candidate.survives_transfer


def test_a_ticker_the_provider_cannot_serve_is_counted_not_fatal(
    db: psycopg.Connection[TupleRow],
) -> None:
    """One delisting must not end a sweep that is working on the other nineteen."""
    repo = ProspectRepo(db)
    session = _session(db)

    reserved, _ = repo.reserve_ordinals(session.id, count=1)
    outcome = tick(db, session, reserved[0], provider=_provider(missing=(HOME,)))

    assert outcome.failed
    assert outcome.candidate is None
    after = repo.require_session(session.id)
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (1, 0, 1)
    assert repo.count_candidates(session_id=session.id) == 0


def test_the_cursor_wraps_and_counts_a_pass(db: psycopg.Connection[TupleRow]) -> None:
    """The wrap happens when the position is reserved, not when the tick lands."""
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME,))

    reserved, _ = repo.reserve_ordinals(session.id, count=1)
    tick(db, session, reserved[0], provider=_provider())
    after = repo.require_session(session.id)

    assert (after.cursor_index, after.passes_completed) == (0, 1)


def test_each_ticker_in_a_pass_gets_its_own_seed(db: psycopg.Connection[TupleRow]) -> None:
    """Reproducible, and not the same search twice.

    The seed is the session's plus the reservation's ordinal, so a tick can be repeated exactly
    while two tickers in one pass are not handed identical starting points -- including when
    both are reserved at once and run concurrently.
    """
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA"))
    provider = _provider()

    reserved, _ = repo.reserve_ordinals(session.id, count=2)
    for reservation in reserved:
        tick(db, session, reservation, provider=provider)

    seeds = [
        row.candidate.seed for row in repo.leaderboard(session_id=session.id, survivors_only=False)
    ]
    assert sorted(seeds) == [session.seed, session.seed + 1]


def test_the_search_size_comes_from_the_sessions_frozen_question() -> None:
    settings = PARAMS.ga_settings()
    assert (settings.population, settings.generations) == (4, 2)


# --------------------------------------------------------------------------- the loop


def test_a_session_is_released_after_its_window_of_ticks(db: psycopg.Connection[TupleRow]) -> None:
    """The priority mechanism: a sweep yields the worker rather than holding it for hours."""
    repo = ProspectRepo(db)
    session = _session(db)

    _queue_a_run(db)

    worked = claim_and_sweep(db, _settings(), provider=_provider())
    assert worked is not None
    assert worked.id == session.id

    after = repo.require_session(session.id)
    assert after.status is ProspectStatus.RUNNING
    assert after.claimed_by is None
    assert after.ticks_completed == 1

    # And so the very next claim finds it, without waiting out a lease.
    assert repo.claim_session("someone-else", lease_seconds=3600.0) is not None


def test_an_idle_worker_finds_nothing_to_prospect(db: psycopg.Connection[TupleRow]) -> None:
    assert claim_and_sweep(db, _settings(), provider=_provider()) is None


def test_a_session_asked_to_stop_is_landed_rather_than_swept(
    db: psycopg.Connection[TupleRow],
) -> None:
    """It never stops mid-search: the compute would be paid for and the result discarded."""
    repo = ProspectRepo(db)
    session = _session(db)
    repo.request_stop(session.id)
    db.commit()

    claim_and_sweep(db, _settings(), provider=_provider())

    after = repo.require_session(session.id)
    assert after.status is ProspectStatus.STOPPED
    assert after.stopped_at is not None
    assert after.ticks_completed == 0
    assert repo.count_candidates(session_id=session.id) == 0


def test_a_worker_shutting_down_releases_without_stopping_the_session(
    db: psycopg.Connection[TupleRow],
) -> None:
    """A sweep outlives the process running it. Shutdown is not the user's stop."""
    repo = ProspectRepo(db)
    session = _session(db)
    halt = threading.Event()
    halt.set()

    claim_and_sweep(db, _settings(), provider=_provider(), stop=halt)

    after = repo.require_session(session.id)
    assert after.status is ProspectStatus.RUNNING
    assert after.claimed_by is None
    assert after.ticks_completed == 0


# --------------------------------------------------------------------------- forward scoring


def _candidate_row(
    db: psycopg.Connection[TupleRow], session_id: UUID, *, last_bar_seen: date
) -> UUID:
    """A survivor with a short warm-up, so the forward window is what is being tested."""
    row = ProspectRepo(db).add_candidate(
        session_id=session_id,
        ticker=HOME,
        discovered_at=datetime.now(UTC),
        last_bar_seen=last_bar_seen,
        seed=11,
        candidate={"ticker": HOME},
        strategy_yaml=(
            "strategy:\n"
            "  name: found_amd\n"
            "universe:\n"
            f"  ticker: {HOME}\n"
            "  start_date: '2020-01-01'\n"
            "  end_date: '2030-01-01'\n"
            "  interval: 1d\n"
            "execution:\n"
            "  initial_capital: 100000.0\n"
            "  slippage_pct: 0.1\n"
            "  commission_pct: 0.1\n"
            "indicators:\n"
            "  - name: sma_fast\n"
            "    type: sma\n"
            "    window: 5\n"
            "entry:\n"
            "  signal: close > sma_fast\n"
            "exit:\n"
            "  signal: close < sma_fast\n"
        ),
        survived_transfer=True,
        transfer_median=0.4,
        transfer_control=-0.5,
    )
    db.commit()
    return row.id


def test_a_candidate_with_new_bars_gets_a_forward_score(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)
    stale = date.today() - timedelta(days=120)
    candidate_id = _candidate_row(db, session.id, last_bar_seen=stale)

    assert score_due(db, session, provider=_provider()) == 1

    scores = repo.forward_scores(candidate_id)
    assert len(scores) == 1
    assert scores[0].first_bar > stale
    assert scores[0].bars >= 30


def test_too_few_new_bars_records_nothing_rather_than_a_zero(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Section 19.3 prefers "not yet" to a Sharpe from nine bars, which would sort above a real
    one. The candidate stays due, and is picked up once the bars exist."""
    repo = ProspectRepo(db)
    session = _session(db)
    fresh = date.today() - timedelta(days=3)
    candidate_id = _candidate_row(db, session.id, last_bar_seen=fresh)

    assert score_due(db, session, provider=_provider()) == 0
    assert repo.forward_scores(candidate_id) == []
    assert [
        row.id for row in repo.due_for_forward_scoring(session_id=session.id, stale_after_hours=0.0)
    ] == [candidate_id]


def test_a_candidate_scored_moments_ago_is_not_scored_again(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)
    candidate_id = _candidate_row(db, session.id, last_bar_seen=date.today() - timedelta(days=120))

    assert score_due(db, session, provider=_provider()) == 1
    assert score_due(db, session, provider=_provider()) == 0
    assert len(repo.forward_scores(candidate_id)) == 1


def test_a_takeover_mid_tick_leaves_nothing_behind(db: psycopg.Connection[TupleRow]) -> None:
    """The candidate and the count share one transaction, so a refused tick stores neither.

    Half a tick would be worse than none: a candidate with no tick counted against it. The
    worker reserves while it still owns the session and loses the lease during the search, which
    is the race the ownership clause exists for.

    The reserved position is *not* given back, and that is section 19.7 as amended: the cursor
    has moved past this ticker, so the new owner prospects whatever comes next and round-robin
    returns to this one a pass later.
    """
    repo = ProspectRepo(db)
    session = _session(db)
    repo.claim_session("this-worker", lease_seconds=60.0)
    reserved, _ = repo.reserve_ordinals(session.id, count=1, worker="this-worker")
    db.commit()

    repo.claim_session("somebody-else", lease_seconds=0.0)
    db.commit()

    with pytest.raises(ConflictError):
        tick(db, session, reserved[0], provider=_provider(), owner="this-worker")
    db.rollback()

    after = repo.require_session(session.id)
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (1, 0, 0)
    assert repo.count_candidates(session_id=session.id) == 0


# --------------------------------------------------------------------------- the sliding window


def test_a_sweep_runs_a_window_of_ticks_and_counts_them_all(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))
    _queue_a_run(db)

    claim_and_sweep(db, _settings(parallelism=4), provider=_provider())

    after = repo.require_session(session.id)
    assert after.ticks_completed + after.ticks_failed == 4
    # Four positions over a four-ticker universe is exactly one pass.
    assert (after.cursor_index, after.passes_completed) == (0, 1)
    assert repo.count_candidates(session_id=session.id) == 4


def test_every_tick_in_a_sweep_gets_a_distinct_seed(db: psycopg.Connection[TupleRow]) -> None:
    """Reservations are the seed source. Two concurrent ticks sharing one would run the same
    search twice and store it twice."""
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))
    _queue_a_run(db)

    claim_and_sweep(db, _settings(parallelism=4), provider=_provider())

    stored = repo.leaderboard(session_id=session.id, survivors_only=False)
    seeds = sorted(row.candidate.seed for row in stored)
    assert seeds == [session.seed + ordinal for ordinal in range(4)]


def test_a_sweep_covers_every_ticker_in_the_universe_exactly_once(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Round-robin survives the pool: concurrency changes when tickers are searched, never
    which ones or how often (section 19.7)."""
    repo = ProspectRepo(db)
    universe = (HOME, "NVDA", "MU", "INTC")
    session = _session(db, universe=universe)
    _queue_a_run(db)

    claim_and_sweep(db, _settings(parallelism=4), provider=_provider())

    stored = repo.leaderboard(session_id=session.id, survivors_only=False)
    assert sorted(row.candidate.ticker for row in stored) == sorted(universe)


def test_a_queued_run_stops_a_sweep_reserving_more(db: psycopg.Connection[TupleRow]) -> None:
    """Section 19.7 promises a user's backtest waits at most one search. When a session was
    claimed for one tick that fell out of the loop's ordering; a sweep that holds its session
    has to ask, and it drains rather than cancelling what is already paid for."""
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))
    _queue_a_run(db)

    claim_and_sweep(db, _settings(parallelism=2), provider=_provider())

    after = repo.require_session(session.id)
    assert after.ticks_completed + after.ticks_failed == 2
    assert after.claimed_by is None


def test_a_sweep_yields_for_a_queued_run(db: psycopg.Connection[TupleRow]) -> None:
    session = _session(db)

    assert not should_yield(db, session.id)
    _queue_a_run(db)
    assert should_yield(db, session.id)


def test_a_sweep_yields_when_the_session_is_asked_to_stop(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Stopping used to be seen at the next claim. A sweep holding its session across many ticks
    would otherwise ignore the request until it ran out of other reasons to stop."""
    repo = ProspectRepo(db)
    session = _session(db)

    assert not should_yield(db, session.id)
    repo.request_stop(session.id)
    db.commit()
    assert should_yield(db, session.id)


def test_a_sweep_yields_for_a_session_that_has_gone(db: psycopg.Connection[TupleRow]) -> None:
    assert should_yield(db, uuid4())


def test_a_sweep_produces_the_same_candidates_however_wide_it_runs(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The central claim of section 19.7 as amended: P is a throughput knob, not an input.

    A tick's seed is the session's plus its reservation's ordinal, so the same ordinal must
    produce the same candidate whether it ran alone or beside three others. If this ever fails,
    the engine is reporting numbers that depend on how busy the machine was -- which is the one
    kind of output this project exists to prevent.
    """
    repo = ProspectRepo(db)
    universe = (HOME, "NVDA", "MU", "INTC")
    _queue_a_run(db)  # keeps each sweep to exactly one window of ticks

    def sweep(parallelism: int) -> dict[int, str]:
        session = _session(db, universe=universe, name=f"p{parallelism}")
        while True:
            current = repo.require_session(session.id)
            if current.ticks_completed + current.ticks_failed >= len(universe):
                break
            claim_and_sweep(db, _settings(parallelism=parallelism), provider=_provider())
        repo.stop_session(session.id)
        db.commit()
        return {
            row.candidate.seed: row.candidate.strategy_yaml
            for row in repo.leaderboard(session_id=session.id, survivors_only=False)
        }

    serial = sweep(1)
    parallel = sweep(4)

    assert len(serial) == len(universe)
    assert set(serial) == set(parallel)
    for seed, strategy_yaml in serial.items():
        assert parallel[seed] == strategy_yaml, f"seed {seed} differs between P=1 and P=4"
