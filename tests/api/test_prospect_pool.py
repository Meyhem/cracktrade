"""One tick, run the way a pool child runs it.

No database here: this is the compute half. What matters is that it produces the candidate the
serial worker would have produced, and that it never raises across the pool boundary -- an
exception crossing it would take down every other tick in flight.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cracktrade.api.worker.pool import TickJob, TickResult, run_tick
from cracktrade.config import Interval
from cracktrade.indicators.catalogue import install
from cracktrade.prospect import Reservation, SweepParams, family_of

HOME = "AMD"
FAMILY = family_of(HOME)
TICKERS = (*FAMILY.members, *FAMILY.controls)

#: Small enough to run in seconds, with the trade floor off -- otherwise these tests pass or
#: fail on whether a four-genome search happened to find something that trades enough, which is
#: a fact about luck and the market generator rather than about the child.
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


def _frames() -> dict[str, pd.DataFrame]:
    return {ticker: _frame(700, seed) for seed, ticker in enumerate(TICKERS)}


def _job(*, ordinal: int = 0, frames: dict[str, pd.DataFrame] | None = None) -> TickJob:
    return TickJob(
        reservation=Reservation(ticker=HOME, ordinal=ordinal),
        params=PARAMS,
        seed=11 + ordinal,
        frames=_frames() if frames is None else frames,
    )


def test_a_child_produces_a_candidate_for_its_reservation() -> None:
    result = run_tick(_job())

    assert result.error is None
    assert result.candidate is not None
    assert result.candidate.ticker == HOME
    assert result.reservation.ordinal == 0
    assert not result.failed


def test_a_child_returns_a_failure_rather_than_raising() -> None:
    """An exception crossing the pool boundary would take down every other tick in flight."""
    result = run_tick(_job(frames={}))

    assert result.candidate is None
    assert result.error is not None
    assert result.failed
    assert result.reservation.ticker == HOME


def test_the_same_reservation_produces_the_same_candidate() -> None:
    """Parallelism must be observationally invisible; this is that claim at the unit level."""
    frames = _frames()

    first = run_tick(_job(ordinal=3, frames=frames))
    second = run_tick(_job(ordinal=3, frames=frames))

    assert first.candidate is not None
    assert second.candidate is not None
    assert first.candidate.strategy_yaml == second.candidate.strategy_yaml
    assert first.candidate.seed == second.candidate.seed


def test_different_ordinals_do_not_repeat_a_search() -> None:
    """The ordinal reaches the seed, so two positions in one pool are two different searches."""
    frames = _frames()

    first = run_tick(_job(ordinal=0, frames=frames))
    second = run_tick(_job(ordinal=1, frames=frames))

    assert first.candidate is not None
    assert second.candidate is not None
    assert first.candidate.seed != second.candidate.seed


def test_a_job_carries_no_way_to_reach_the_database_or_the_network() -> None:
    """The seam that keeps a child honest: it cannot write, cannot fetch, and cannot advance a
    cursor. Ownership and transactions stay in the parent."""
    fields = set(TickJob.__dataclass_fields__)

    assert fields == {"reservation", "params", "seed", "frames", "max_filled_fraction"}


def test_a_result_carries_its_reservation_home() -> None:
    """The parent lands results as they arrive, out of dispatch order, so each has to say which
    position it belongs to."""
    result = run_tick(_job(ordinal=7))

    assert isinstance(result, TickResult)
    assert result.reservation == Reservation(ticker=HOME, ordinal=7)
