"""The parent-side fetch horizon -- spec section 19.7.

A tick fetches its home ticker plus every sibling and control in its family, about eleven for
semiconductors. Run twelve of them at once and each fetches the same eleven, so what matters
here is that concurrent ticks cost the provider one fetch per ticker rather than twelve -- and
that a sweep running for days never searches against bars frozen at its first tick.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cracktrade.config import Interval
from cracktrade.errors import DataUnavailableError
from cracktrade.prospect.prefetch import PrefetchHorizon

START = date(2024, 1, 1)
END = date(2024, 3, 1)


class CountingProvider:
    """Records every ticker it was asked for, so dedup is observable."""

    name = "counting"

    def __init__(self, *, refuses: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self._refuses = refuses

    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame:
        self.calls.append(ticker)
        if ticker in self._refuses:
            raise DataUnavailableError(f"nothing for {ticker}")
        index = pd.DatetimeIndex(pd.date_range(start, end, freq="B"), name="Date")
        return pd.DataFrame(
            {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=index
        )


def test_a_ticker_asked_for_twice_is_fetched_once() -> None:
    """The whole point: SPY is a control for several families, so consecutive ticks ask for it
    over and over."""
    provider = CountingProvider()
    horizon = PrefetchHorizon(provider, interval=Interval.D1)

    horizon.frames_for(["AMD", "SPY"], START, END)
    horizon.frames_for(["NVDA", "SPY"], START, END)

    assert provider.calls == ["AMD", "SPY", "NVDA"]


def test_a_ticker_repeated_within_one_request_is_fetched_once() -> None:
    provider = CountingProvider()

    frames = PrefetchHorizon(provider, interval=Interval.D1).frames_for(
        ["AMD", "SPY", "AMD"], START, END
    )

    assert provider.calls == ["AMD", "SPY"]
    assert set(frames) == {"AMD", "SPY"}


def test_every_requested_ticker_comes_back() -> None:
    horizon = PrefetchHorizon(CountingProvider(), interval=Interval.D1)

    frames = horizon.frames_for(["AMD", "NVDA", "SPY"], START, END)

    assert set(frames) == {"AMD", "NVDA", "SPY"}
    assert all(not frame.empty for frame in frames.values())


def test_an_entry_older_than_the_ttl_is_fetched_again() -> None:
    """A sweep runs for days. Without a bound the horizon would keep prospecting a history that
    stopped at the session's first tick, while the leaderboard claimed to be current -- the
    hazard FrameCache carries and the reason it is off by default."""
    provider = CountingProvider()
    horizon = PrefetchHorizon(provider, interval=Interval.D1, ttl_seconds=0.0)

    horizon.frames_for(["AMD"], START, END)
    horizon.frames_for(["AMD"], START, END)

    assert provider.calls == ["AMD", "AMD"]


def test_a_ticker_the_provider_refuses_is_omitted_rather_than_fatal() -> None:
    """One delisting must not stop the other eleven ticks in flight. The child that needed it
    fails its own tick and is counted as a failure, exactly as if it had fetched for itself."""
    horizon = PrefetchHorizon(CountingProvider(refuses=("GONE",)), interval=Interval.D1)

    frames = horizon.frames_for(["AMD", "GONE"], START, END)

    assert set(frames) == {"AMD"}


def test_a_refusal_is_not_cached_as_an_absence() -> None:
    """A ticker that failed once must be retried, not remembered as missing for the whole
    sweep: a provider hiccup would otherwise blacklist an instrument for days."""
    provider = CountingProvider(refuses=("GONE",))
    horizon = PrefetchHorizon(provider, interval=Interval.D1)

    horizon.frames_for(["GONE"], START, END)
    horizon.frames_for(["GONE"], START, END)

    assert provider.calls == ["GONE", "GONE"]


def test_the_interval_reaches_the_provider() -> None:
    """The session's frozen bar width, not a default: a sweep at 30m must not be handed daily
    bars because the horizon forgot to pass it on."""
    seen: list[Interval] = []

    class Recording(CountingProvider):
        def fetch(
            self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
        ) -> pd.DataFrame:
            seen.append(interval)
            return super().fetch(ticker, start, end, interval)

    PrefetchHorizon(Recording(), interval=Interval.M30).frames_for(["AMD"], START, END)

    assert seen == [Interval.M30]


def test_an_empty_request_asks_the_provider_nothing() -> None:
    provider = CountingProvider()

    assert PrefetchHorizon(provider, interval=Interval.D1).frames_for([], START, END) == {}
    assert provider.calls == []


def test_a_horizon_is_a_provider_the_engine_would_accept() -> None:
    """Guards the seam: the horizon takes anything satisfying the protocol, so a fixture, a
    database or yfinance all work without it knowing which."""
    from cracktrade.data import MarketDataProvider

    assert isinstance(CountingProvider(), MarketDataProvider)


@pytest.mark.parametrize("threads", [1, 4])
def test_the_thread_count_does_not_change_what_comes_back(threads: int) -> None:
    frames = PrefetchHorizon(
        CountingProvider(), interval=Interval.D1, max_threads=threads
    ).frames_for(["AMD", "NVDA", "SPY"], START, END)

    assert set(frames) == {"AMD", "NVDA", "SPY"}
