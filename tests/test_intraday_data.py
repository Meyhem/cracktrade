"""Phase 2: loading intraday bars.

Offline throughout, against the captured fixtures. The daily path is exercised by
``tests/test_data.py`` and must not change; what is asserted here is the intraday behaviour
that differs from it, and *why* it differs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from cracktrade.config import Interval, Strategy, parse_strategy
from cracktrade.data import (
    FrameCache,
    StaticProvider,
    load_history,
    median_bars_per_session,
    prepare_frame,
    prepare_history,
    session_count,
    session_ids,
    source_timezone,
)
from cracktrade.data.loader import _normalise_index
from cracktrade.errors import DataUnavailableError
from tests.intraday import load_fixture

BERLIN = ZoneInfo("Europe/Berlin")

#: Comfortably after the last fixture bar has closed, so nothing is dropped as unclosed unless
#: a test asks for it.
AFTER_CAPTURE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def sap_strategy(
    interval: str = "30m",
    start: str = "2026-07-01",
    end: str = "2026-08-18",
    **exit_rule: object,
) -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "sap_intraday"},
            "universe": {
                "ticker": "SAP.DE",
                "start_date": start,
                "end_date": end,
                "interval": interval,
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": dict({"max_holding_bars": 4}, **exit_rule),
        }
    )


def sap_provider() -> StaticProvider:
    return StaticProvider(
        by_interval={
            ("SAP.DE", Interval.M30): load_fixture("sap_de_30m"),
            ("SAP.DE", Interval.H1): load_fixture("sap_de_1h"),
        }
    )


# ------------------------------------------------------------------- the index


def test_intraday_timestamps_keep_their_wall_clock_time() -> None:
    """The trap: ``.normalize()`` would set all seventeen bars of a session to midnight.

    Seventeen duplicates of one instant, sixteen of which the duplicate filter then deletes --
    a whole trading day reduced to one bar, silently.
    """
    frame, timezone = _normalise_index(load_fixture("sap_de_30m"), "SAP.DE", Interval.M30)
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    assert index.tz is None
    assert timezone == "Europe/Berlin"

    first_session = index[index.normalize() == index[0].normalize()]
    assert len(first_session) == 17
    assert first_session[0].strftime("%H:%M") == "09:00"
    assert first_session[-1].strftime("%H:%M") == "17:00"


def test_intraday_timestamps_are_exchange_local_not_utc() -> None:
    """09:00 in Frankfurt stays 09:00, not 07:00.

    Converting to UTC would be silently wrong rather than obviously wrong: the EU changes to
    summer time on the last Sunday of March and the US on the second Sunday of March, so the
    same Xetra session would sit at 07:00 UTC for part of the year and 08:00 for the rest --
    moving the session boundary the forced-close rule keys off.
    """
    frame, _ = _normalise_index(load_fixture("sap_de_30m"), "SAP.DE", Interval.M30)
    raw = load_fixture("sap_de_30m")
    assert frame.index[0].strftime("%H:%M") == "09:00"
    assert raw.index[0].tz_convert("UTC").strftime("%H:%M") == "07:00"


def test_a_dst_switch_does_not_move_the_session() -> None:
    """The whole justification for exchange-local timestamps, asserted end to end."""
    frame, _ = _normalise_index(load_fixture("sap_de_1h"), "SAP.DE", Interval.H1)
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    for switch in ("2025-10-26", "2026-03-29"):
        window = index[
            (index >= pd.Timestamp(switch) - pd.Timedelta(days=4))
            & (index <= pd.Timestamp(switch) + pd.Timedelta(days=4))
        ]
        opens = {stamp.strftime("%H:%M") for stamp in window if stamp.hour == window.min().hour}
        assert opens == {"09:00"}


def test_daily_normalisation_is_untouched() -> None:
    """Daily still converts to UTC and truncates to midnight, and reports no timezone."""
    raw = load_fixture("sap_de_1h")
    frame, timezone = _normalise_index(raw, "SAP.DE", Interval.D1)
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    assert timezone is None
    assert (index == index.normalize()).all()


def test_source_timezone_reads_the_raw_response() -> None:
    assert source_timezone(load_fixture("vod_l_1h_halfdays")) == "Europe/London"
    assert source_timezone(load_fixture("spy_30m")) == "America/New_York"


# ------------------------------------------------------------- unclosed bars


def test_the_forming_bar_is_dropped() -> None:
    """The capture-day session is a single 09:00 bar that had not closed yet."""
    frame = prepare_frame(
        load_fixture("sap_de_30m"),
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 19, 9, 15, tzinfo=BERLIN),
    )
    assert frame.index[-1].date() == date(2026, 8, 18)


def test_a_bar_is_kept_exactly_once_its_close_has_passed() -> None:
    """A 09:00 bar on a 30-minute strategy is complete at 09:30 Frankfurt time, not before."""
    fixture = load_fixture("sap_de_30m")
    before = prepare_frame(
        fixture,
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 19, 9, 29, tzinfo=BERLIN),
    )
    after = prepare_frame(
        fixture,
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 19, 9, 30, tzinfo=BERLIN),
    )
    assert len(after) == len(before) + 1
    assert after.index[-1] == pd.Timestamp("2026-08-19 09:00")


def test_the_cutoff_is_read_in_the_exchanges_timezone() -> None:
    """07:29 UTC is 09:29 in Frankfurt, so the 09:00 bar is still forming.

    Reading the clock in UTC would have declared it closed two hours early -- and in the other
    direction, would keep a bar an EU trader cannot have seen yet.
    """
    frame = prepare_frame(
        load_fixture("sap_de_30m"),
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 19, 7, 29, tzinfo=UTC),
    )
    assert frame.index[-1] == pd.Timestamp("2026-08-18 17:00")


def test_two_runs_in_one_session_see_the_same_history() -> None:
    """Determinism shifts from "per UTC day" to "per closed bar"; both are determinism."""
    fixture = load_fixture("sap_de_30m")
    morning = prepare_frame(
        fixture,
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 18, 10, 5, tzinfo=BERLIN),
    )
    later = prepare_frame(
        fixture,
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 18, 10, 25, tzinfo=BERLIN),
    )
    pd.testing.assert_frame_equal(morning, later)


# ---------------------------------------------------------- session-aware fill


def gappy_intraday() -> pd.DataFrame:
    """The 30m fixture with two holes: one mid-session, one at a session's open."""
    frame = load_fixture("sap_de_30m").copy()
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    dates = index.normalize()
    second_session = dates == sorted(set(dates))[1]
    positions = np.flatnonzero(second_session)
    frame.iloc[positions[0]] = np.nan  # session open
    frame.iloc[positions[5]] = np.nan  # mid-session
    return frame


def test_a_mid_session_hole_is_filled_from_within_the_session() -> None:
    frame, filled = prepare_history(
        gappy_intraday(), ticker="SAP.DE", interval=Interval.M30, now=AFTER_CAPTURE
    )
    repaired = frame.index[filled.to_numpy()]
    assert len(repaired) == 1
    assert repaired[0].strftime("%H:%M") == "11:30"


def test_a_hole_at_the_open_is_dropped_not_filled_from_yesterday() -> None:
    """Filling it would pin the whole overnight gap onto a bar that never traded.

    The fabricated bar would report a zero return across the gap and hand a stop-loss a
    high/low range from the previous day to fire against -- on a morning when the market did
    not print those prices.
    """
    frame, _ = prepare_history(
        gappy_intraday(), ticker="SAP.DE", interval=Interval.M30, now=AFTER_CAPTURE
    )
    original = load_fixture("sap_de_30m")
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)

    dropped_session = sorted({stamp.date() for stamp in original.index})[1]
    kept = [stamp for stamp in index if stamp.date() == dropped_session]
    assert len(kept) == 16
    assert kept[0].strftime("%H:%M") == "09:30"


def test_the_filled_mask_stays_aligned_after_a_row_is_dropped() -> None:
    frame, filled = prepare_history(
        gappy_intraday(), ticker="SAP.DE", interval=Interval.M30, now=AFTER_CAPTURE
    )
    assert len(filled) == len(frame)
    assert (filled.index == frame.index).all()


def test_daily_forward_fill_is_still_global() -> None:
    """Carrying yesterday's close onto a non-trading day is the right model for daily bars."""
    from tests.factories import make_ohlcv

    raw = make_ohlcv(30)
    raw.iloc[10] = np.nan
    frame, filled = prepare_history(raw, ticker="TEST", now=date(2030, 1, 1))
    assert len(frame) == 30
    assert int(filled.sum()) == 1


# --------------------------------------------------------------- load_history


def test_load_history_returns_intraday_market_data() -> None:
    data = load_history(sap_strategy(), sap_provider(), now=AFTER_CAPTURE)
    assert data.interval is Interval.M30
    assert data.timezone == "Europe/Berlin"
    assert data.index.tz is None
    assert len(data) > 500


def test_a_xetra_session_measures_seventeen_thirty_minute_bars() -> None:
    """Measured, never assumed. New York would give 13 for the same interval."""
    data = load_history(sap_strategy(), sap_provider(), now=AFTER_CAPTURE)
    assert median_bars_per_session(data.index) == 17


def test_an_hourly_xetra_session_measures_nine_bars() -> None:
    """Nine, because the 17:00 bar covers only the half hour to 17:30. Bars, not hours."""
    data = load_history(
        sap_strategy("1h", start="2025-01-02", end="2026-08-18"),
        sap_provider(),
        now=AFTER_CAPTURE,
    )
    assert median_bars_per_session(data.index) == 9


def test_the_interval_and_timezone_survive_slicing() -> None:
    """The truncation harness and the walk-forward splitter both go through ``slice``.

    A window that forgot its bar width would be annualised as daily, in the code path meant to
    be checking for exactly that class of error.
    """
    data = load_history(sap_strategy(), sap_provider(), now=AFTER_CAPTURE)
    window = data.head(100)
    assert window.interval is Interval.M30
    assert window.timezone == "Europe/Berlin"
    assert data.slice(10, 50).interval is Interval.M30


def test_a_missing_interval_is_reported_with_what_is_available() -> None:
    provider = StaticProvider(by_interval={("SAP.DE", Interval.H1): load_fixture("sap_de_1h")})
    with pytest.raises(DataUnavailableError, match="1h"):
        load_history(sap_strategy("30m"), provider, now=AFTER_CAPTURE)


def test_an_empty_response_names_the_interval_limit() -> None:
    """yfinance returns an empty frame rather than raising, so the message is ours to write.

    Without it the user is left guessing between a wrong ticker, a holiday range, and a
    60-day provider limit they have never heard of.
    """
    from cracktrade.data.provider import _empty_response_message

    message = _empty_response_message("SAP.DE", date(2021, 1, 1), date(2021, 2, 1), Interval.M30)
    assert "58 days" in message
    assert "1h" in message and "1d" in message


# ------------------------------------------------------------------- sessions


def test_session_ids_increase_once_per_local_date() -> None:
    data = load_history(sap_strategy(), sap_provider(), now=AFTER_CAPTURE)
    ids = session_ids(data.index)
    assert ids[0] == 0
    assert ids[-1] + 1 == session_count(data.index)
    assert (np.diff(ids) >= 0).all()


def test_session_helpers_tolerate_an_empty_index() -> None:
    empty = pd.DatetimeIndex([])
    assert session_count(empty) == 0
    assert median_bars_per_session(empty) == 0
    assert len(session_ids(empty)) == 0


def test_median_bars_per_session_ignores_the_trailing_partial_session() -> None:
    """Otherwise every run would be dragged down by the session it happens to end in."""
    frame, _ = prepare_history(
        load_fixture("sap_de_30m"),
        ticker="SAP.DE",
        interval=Interval.M30,
        now=datetime(2026, 8, 19, 12, 0, tzinfo=BERLIN),
    )
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    assert median_bars_per_session(index) == 17


def test_half_days_do_not_move_the_median() -> None:
    """The LSE trades four-bar Dec 24 and Dec 31 sessions; the median shrugs them off."""
    frame, _ = prepare_history(
        load_fixture("vod_l_1h_halfdays"),
        ticker="VOD.L",
        interval=Interval.H1,
        now=AFTER_CAPTURE,
    )
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    assert median_bars_per_session(index) == 9


# ---------------------------------------------------------------------- cache


def test_the_cache_key_separates_intervals(tmp_path: Path) -> None:
    """Otherwise a 30-minute request would be served the daily frame for the same range."""
    cache = FrameCache(tmp_path)
    start, end = date(2026, 7, 1), date(2026, 8, 18)
    frame = load_fixture("sap_de_30m")

    cache.put("yfinance", "SAP.DE", start, end, frame, Interval.M30)
    assert cache.get("yfinance", "SAP.DE", start, end, Interval.M30) is not None
    assert cache.get("yfinance", "SAP.DE", start, end, Interval.H1) is None
    assert cache.get("yfinance", "SAP.DE", start, end, Interval.D1) is None


def test_a_cached_intraday_frame_round_trips_with_its_timezone(tmp_path: Path) -> None:
    """Parquet must not quietly drop the offset; the zone is the whole provenance."""
    cache = FrameCache(tmp_path)
    start, end = date(2026, 7, 1), date(2026, 8, 18)
    cache.put("yfinance", "SAP.DE", start, end, load_fixture("sap_de_30m"), Interval.M30)
    restored = cache.get("yfinance", "SAP.DE", start, end, Interval.M30)
    assert restored is not None
    assert source_timezone(restored) == "Europe/Berlin"


def test_load_history_uses_the_cache_for_the_right_interval(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path)
    strategy = sap_strategy()
    first = load_history(strategy, sap_provider(), cache=cache, now=AFTER_CAPTURE)

    # Second load is served entirely from disk: an empty provider would fail without it.
    second = load_history(strategy, StaticProvider(), cache=cache, now=AFTER_CAPTURE)
    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert second.timezone == "Europe/Berlin"


# ------------------------------------------------------------- range refusal


def test_a_range_beyond_the_interval_limit_is_refused_before_any_fetch() -> None:
    """Refused by the schema, so no download is attempted at all."""
    from cracktrade.errors import StrategyValidationError

    with pytest.raises(StrategyValidationError, match="58 days"):
        sap_strategy("30m", start="2020-01-01", end="2026-08-18")


def test_an_aged_but_narrow_range_still_loads_from_a_provider_that_has_it() -> None:
    """Parsing must not depend on the wall clock. Only fetching does."""
    strategy = parse_strategy(
        {
            "strategy": {"name": "old"},
            "universe": {
                "ticker": "VOD.L",
                "start_date": "2024-12-02",
                "end_date": "2025-01-31",
                "interval": "1h",
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": {"max_holding_bars": 4},
        }
    )
    provider = StaticProvider(
        by_interval={("VOD.L", Interval.H1): load_fixture("vod_l_1h_halfdays")}
    )
    data = load_history(strategy, provider, now=AFTER_CAPTURE)
    assert data.timezone == "Europe/London"
    assert session_count(data.index) > 30
