"""Phase 0: pin what was measured about yfinance's intraday responses.

These tests assert nothing about the engine. They assert facts about the *data* -- the ones the
intraday design rests on -- so that a fixture regenerated against a changed provider fails here,
loudly and in one place, instead of quietly shifting a backtest's numbers.

Every claim below was established by probing the live API on the capture date, not read from
documentation. ``tests/fixtures/README.md`` records the probe's findings in prose.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.intraday import FIXTURE_TIMEZONES, load_fixture


@pytest.mark.parametrize("name", sorted(FIXTURE_TIMEZONES))
def test_fixtures_are_raw_provider_shape(name: str) -> None:
    """Timezone-aware exchange-local index, OHLCV columns, sorted and unique."""
    frame = load_fixture(name)
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    assert index.tz is not None
    assert str(index.tz) == FIXTURE_TIMEZONES[name]
    assert index.is_monotonic_increasing
    assert index.is_unique
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert not frame.isna().to_numpy().any()


def _session_times(frame: pd.DataFrame) -> pd.DataFrame:
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    times = pd.DataFrame({"date": index.date, "time": index.time})
    return times.groupby("date").agg(
        bars=("time", "size"), first=("time", "min"), last=("time", "max")
    )


def test_xetra_session_is_seventeen_thirty_minute_bars() -> None:
    """8.5 hours of Xetra, not 6.5 of NYSE.

    The single most load-bearing number in the EU intraday design: any code that assumes a
    US session length is wrong by four bars a day here.
    """
    sessions = _session_times(load_fixture("sap_de_30m"))
    complete = sessions[sessions["bars"] > 1]
    assert complete["bars"].mode().iloc[0] == 17
    assert complete["first"].mode().iloc[0].isoformat() == "09:00:00"
    assert complete["last"].mode().iloc[0].isoformat() == "17:00:00"


def test_us_session_is_thirteen_thirty_minute_bars() -> None:
    """The contrast case. A hardcoded bars-per-day constant cannot satisfy both this and Xetra."""
    sessions = _session_times(load_fixture("spy_30m"))
    assert sessions["bars"].mode().iloc[0] == 13
    assert sessions["first"].mode().iloc[0].isoformat() == "09:30:00"
    assert sessions["last"].mode().iloc[0].isoformat() == "15:30:00"


def test_local_session_times_survive_dst_transitions() -> None:
    """Xetra reads 09:00-17:00 local on both sides of the EU's March and October switches.

    This is the justification for keeping intraday timestamps exchange-local instead of
    converting them to UTC: in UTC these same sessions would straddle two different offsets,
    and the EU's switch dates do not coincide with the US's.
    """
    sessions = _session_times(load_fixture("sap_de_1h"))
    for switch in ("2025-03-30", "2025-10-26", "2026-03-29"):
        around = sessions.loc[
            (sessions.index >= pd.Timestamp(switch).date() - pd.Timedelta(days=4).to_pytimedelta())
            & (
                sessions.index
                <= pd.Timestamp(switch).date() + pd.Timedelta(days=4).to_pytimedelta()
            )
        ]
        assert not around.empty
        assert set(around["first"].map(lambda value: value.isoformat())) == {"09:00:00"}
        assert set(around["last"].map(lambda value: value.isoformat())) == {"17:00:00"}


def test_sessions_are_not_a_fixed_length() -> None:
    """Xetra closes Dec 24 and 31 outright, and runs a five-bar Dec 30.

    The forced session-close logic must therefore learn the close time from recent sessions
    rather than assume one, and must still have a safety net when the learned time is wrong.
    """
    sessions = _session_times(load_fixture("sap_de_1h"))
    dates = {value.isoformat() for value in sessions.index}
    assert "2024-12-24" not in dates
    assert "2024-12-31" not in dates
    assert "2025-12-24" not in dates
    assert "2025-12-31" not in dates

    short = sessions.loc[pd.Timestamp("2024-12-30").date()]
    assert short["bars"] == 5
    assert short["last"].isoformat() == "13:00:00"


def test_lse_runs_half_days_where_xetra_closes() -> None:
    """LSE trades a shortened Dec 24 and Dec 31 -- four 1h bars, closing 12:30 London.

    An EU trader running one strategy across both venues sees two different calendars, which
    is why no calendar is hardcoded anywhere in the engine.
    """
    sessions = _session_times(load_fixture("vod_l_1h_halfdays"))
    for half_day in ("2024-12-24", "2024-12-31"):
        row = sessions.loc[pd.Timestamp(half_day).date()]
        assert row["bars"] == 4
        assert row["first"].isoformat() == "08:00:00"
        assert row["last"].isoformat() == "11:00:00"


def test_zero_volume_clusters_on_the_european_opening_bar() -> None:
    """Yahoo reports no volume on most Xetra 09:00 bars, while quoting prices normally.

    Recorded rather than repaired: zero volume is a legal value under the frame contract, and
    inventing a number here would be exactly the kind of authoritative-looking fiction this
    engine exists to avoid. The consequence -- volume-based indicators are unreliable on the
    EU opening bar -- belongs in the documentation, not in a silent fix.
    """
    frame = load_fixture("sap_de_1h")
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    zero = frame["Volume"] == 0
    opening = (
        pd.Series(index.time, index=frame.index).map(lambda value: value.isoformat()) == "09:00:00"
    )

    assert zero.sum() > 0.05 * len(frame)
    # Nearly all of it is the opening bar, and nearly every session's opening bar has it.
    assert (zero & opening).sum() / zero.sum() > 0.95
    assert (zero & opening).sum() / opening.sum() > 0.8
    # Prices on those bars are still real.
    assert frame.loc[zero, ["Open", "High", "Low", "Close"]].gt(0).to_numpy().all()


def test_the_capture_day_session_is_partial() -> None:
    """The response includes the still-forming bar of the current session.

    The loader must drop it: a run at 09:15 and a run at 17:45 have to see the same history.
    """
    sessions = _session_times(load_fixture("sap_de_30m"))
    last = sessions.iloc[-1]
    assert last["bars"] == 1
    assert str(sessions.index[-1]) == "2026-08-19"
