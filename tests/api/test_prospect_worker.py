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
from uuid import UUID

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.errors import ConflictError
from cracktrade.api.repos import ProspectRepo
from cracktrade.api.repos.rows import ProspectStatus
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker.prospect import claim_and_tick, score_due, sweep_settings, tick
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


def _settings() -> ApiSettings:
    return ApiSettings(worker_lease_seconds=60.0, worker_heartbeat_seconds=30.0)


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


def test_a_tick_stores_what_it_found_and_advances_the_cursor(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)

    outcome = tick(db, session, provider=_provider())

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

    outcome = tick(db, session, provider=_provider(missing=(HOME,)))

    assert outcome.failed
    assert outcome.candidate is None
    after = repo.require_session(session.id)
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (1, 0, 1)
    assert repo.count_candidates(session_id=session.id) == 0


def test_the_cursor_wraps_and_counts_a_pass(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME,))

    tick(db, session, provider=_provider())
    after = repo.require_session(session.id)

    assert (after.cursor_index, after.passes_completed) == (0, 1)


def test_each_ticker_in_a_pass_gets_its_own_seed(db: psycopg.Connection[TupleRow]) -> None:
    """Reproducible, and not the same search twice.

    The seed is the session's plus the cursor, so a tick can be repeated exactly while two
    tickers in one pass are not handed identical starting points.
    """
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA"))
    provider = _provider()

    tick(db, session, provider=provider)
    tick(db, repo.require_session(session.id), provider=provider)

    seeds = [
        row.candidate.seed for row in repo.leaderboard(session_id=session.id, survivors_only=False)
    ]
    assert sorted(seeds) == [session.seed, session.seed + 1]


def test_the_search_size_comes_from_the_sessions_frozen_question() -> None:
    settings = sweep_settings(PARAMS)
    assert (settings.population, settings.generations) == (4, 2)


# --------------------------------------------------------------------------- the loop


def test_a_session_is_released_after_one_tick(db: psycopg.Connection[TupleRow]) -> None:
    """The priority mechanism: a sweep yields the worker rather than holding it for hours."""
    repo = ProspectRepo(db)
    session = _session(db)

    worked = claim_and_tick(db, _settings(), provider=_provider())
    assert worked is not None
    assert worked.id == session.id

    after = repo.require_session(session.id)
    assert after.status is ProspectStatus.RUNNING
    assert after.claimed_by is None
    assert after.ticks_completed == 1

    # And so the very next claim finds it, without waiting out a lease.
    assert repo.claim_session("someone-else", lease_seconds=3600.0) is not None


def test_an_idle_worker_finds_nothing_to_prospect(db: psycopg.Connection[TupleRow]) -> None:
    assert claim_and_tick(db, _settings(), provider=_provider()) is None


def test_a_session_asked_to_stop_is_landed_rather_than_ticked(
    db: psycopg.Connection[TupleRow],
) -> None:
    """It never stops mid-search: the compute would be paid for and the result discarded."""
    repo = ProspectRepo(db)
    session = _session(db)
    repo.request_stop(session.id)
    db.commit()

    claim_and_tick(db, _settings(), provider=_provider())

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

    claim_and_tick(db, _settings(), provider=_provider(), stop=halt)

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
    """The candidate and the cursor share one transaction, so a refused tick stores neither.

    Half a tick would be worse than none: a candidate with no tick counted against it, in a
    session whose cursor says that ticker was never reached.
    """
    repo = ProspectRepo(db)
    session = _session(db)
    repo.claim_session("somebody-else", lease_seconds=60.0)
    db.commit()

    with pytest.raises(ConflictError):
        tick(db, session, provider=_provider(), owner="this-worker")
    db.rollback()

    after = repo.require_session(session.id)
    assert (after.cursor_index, after.ticks_completed, after.ticks_failed) == (0, 0, 0)
    assert repo.count_candidates(session_id=session.id) == 0
