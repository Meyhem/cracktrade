"""Phase 3: the market data layer and its contract (spec section 4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import (
    DTYPE,
    OHLCV_COLUMNS,
    FrameCache,
    MarketData,
    StaticProvider,
    load_history,
    prepare_frame,
    prepare_history,
    validate_frame,
)
from cracktrade.data.loader import _utc_today
from cracktrade.data.provider import _flatten_columns
from cracktrade.errors import (
    DataContractError,
    DataQualityError,
    DataUnavailableError,
    InsufficientHistoryError,
)
from tests.factories import make_ohlcv

TICKER = "TEST"


def strategy_for(start: str = "2020-01-01", end: str = "2024-01-01") -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "s"},
            "universe": {"ticker": TICKER, "start_date": start, "end_date": end},
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": {"max_holding_days": 5},
        }
    )


# ------------------------------------------------------------------ preparation


def test_prepare_produces_a_conforming_frame() -> None:
    frame = prepare_frame(make_ohlcv(50), ticker=TICKER, now=date(2030, 1, 1))
    validate_frame(frame)
    assert tuple(frame.columns) == OHLCV_COLUMNS
    assert all(frame[column].dtype == DTYPE for column in OHLCV_COLUMNS)
    assert isinstance(frame.index, pd.DatetimeIndex)


def test_column_labels_are_normalised() -> None:
    raw = make_ohlcv(20).rename(columns=str.lower)
    frame = prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))
    assert tuple(frame.columns) == OHLCV_COLUMNS


def test_missing_column_is_rejected() -> None:
    raw = make_ohlcv(20).drop(columns=["Volume"])
    with pytest.raises(DataContractError, match="missing required column"):
        prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))


def test_timezone_aware_index_is_made_naive() -> None:
    raw = make_ohlcv(20)
    raw.index = pd.DatetimeIndex(raw.index).tz_localize("America/New_York")
    frame = prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.tz is None


def test_unsorted_and_duplicated_index_is_repaired() -> None:
    raw = make_ohlcv(20)
    shuffled = pd.concat([raw.iloc[10:], raw.iloc[:10], raw.iloc[:1]])
    frame = prepare_frame(shuffled, ticker=TICKER, now=date(2030, 1, 1))
    assert frame.index.is_monotonic_increasing
    assert frame.index.is_unique
    assert len(frame) == 20


def test_reset_index_is_never_applied() -> None:
    """vectorbt annualises from the index; losing it would corrupt every time-based metric."""
    raw = make_ohlcv(20)
    frame = prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))
    assert frame.index[0] == raw.index[0]


# ------------------------------------------------------------------ incomplete bars


def test_todays_partial_bar_is_dropped() -> None:
    raw = make_ohlcv(20, start="2024-01-01")
    last_day = raw.index[-1].date()
    frame = prepare_frame(raw, ticker=TICKER, now=last_day)
    assert len(frame) == 19
    assert frame.index[-1].date() < last_day


def test_history_is_identical_regardless_of_run_time() -> None:
    raw = make_ohlcv(20, start="2024-01-01")
    last_day = raw.index[-1].date()
    morning = prepare_frame(raw, ticker=TICKER, now=last_day)
    evening = prepare_frame(raw, ticker=TICKER, now=last_day)
    pd.testing.assert_frame_equal(morning, evening)


# ------------------------------------------------------------------ defect D5: no bfill


def test_late_listing_history_is_dropped_not_fabricated() -> None:
    """The single most important test in this module.

    A backward fill would invent a flat pre-listing history out of the first real close, warm
    indicators up on invented data, and take trades in a period when the instrument did not
    trade.
    """
    raw = make_ohlcv(30)
    raw.iloc[:5] = np.nan
    first_real_close = float(raw["Close"].iloc[5])

    frame = prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))

    assert len(frame) == 25, "leading gap should be dropped, not filled"
    assert float(frame["Close"].iloc[0]) == pytest.approx(first_real_close)
    assert frame.index[0] == raw.index[5]


def test_mid_series_gap_is_carried_forward() -> None:
    raw = make_ohlcv(30)
    previous_close = float(raw["Close"].iloc[9])
    raw.iloc[10] = np.nan

    frame = prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))

    assert len(frame) == 30
    assert float(frame["Close"].iloc[10]) == pytest.approx(previous_close)


def test_all_missing_history_is_an_error() -> None:
    raw = make_ohlcv(10)
    raw.iloc[:] = np.nan
    with pytest.raises(DataContractError, match="no complete bars"):
        prepare_frame(raw, ticker=TICKER, now=date(2030, 1, 1))


# ------------------------------------------------------------------ contract checks


def test_contract_rejects_wrong_dtype() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))
    frame = frame.astype({"Close": np.float32})
    with pytest.raises(DataContractError, match="float64"):
        validate_frame(frame)


def test_contract_rejects_nan() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))
    frame.iloc[3, 0] = np.nan
    with pytest.raises(DataContractError, match="NaN"):
        validate_frame(frame)


def test_contract_rejects_inconsistent_high() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))
    frame.loc[frame.index[3], "High"] = 0.0
    with pytest.raises(DataContractError, match="High is not the highest"):
        validate_frame(frame)


def test_sub_ulp_high_low_noise_is_repaired() -> None:
    """A High one ulp below Close -- a yfinance auto_adjust artifact -- must not fail."""
    raw = make_ohlcv(20)
    victim = raw.index[5]
    close = raw.loc[victim, "Close"]
    nudged_high = np.nextafter(close, -np.inf)
    raw.loc[victim, "High"] = nudged_high
    assert nudged_high < close  # sanity: still "violates" pre-repair

    frame, _ = prepare_history(raw, ticker=TICKER, now=date(2030, 1, 1))

    assert frame.loc[victim, "High"] == frame.loc[victim, ["Open", "Close"]].max()


def test_genuinely_wrong_high_is_not_repaired() -> None:
    raw = make_ohlcv(20)
    victim = raw.index[5]
    raw.loc[victim, "High"] = raw.loc[victim, "Close"] * 0.5  # not float noise -- a real error

    with pytest.raises(DataContractError, match="High is not the highest"):
        prepare_history(raw, ticker=TICKER, now=date(2030, 1, 1))


def test_contract_rejects_negative_volume() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))
    frame.loc[frame.index[3], "Volume"] = -1.0
    with pytest.raises(DataContractError, match="Volume contains negative"):
        validate_frame(frame)


def test_contract_rejects_extra_columns() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))
    frame["Adj Close"] = frame["Close"]
    with pytest.raises(DataContractError, match="columns must be exactly"):
        validate_frame(frame)


# ------------------------------------------------------------------ provider


def test_multiindex_response_is_flattened() -> None:
    raw = make_ohlcv(10)
    raw.columns = pd.MultiIndex.from_product([list(raw.columns), [TICKER]])
    flattened = _flatten_columns(raw, TICKER)
    assert tuple(flattened.columns) == OHLCV_COLUMNS


def test_multi_ticker_response_is_refused() -> None:
    single = make_ohlcv(10)
    both = pd.concat([single, single], axis="columns")
    both.columns = pd.MultiIndex.from_product([list(single.columns), ["A", "B"]])
    with pytest.raises(DataUnavailableError, match="expected one ticker"):
        _flatten_columns(both, TICKER)


def test_static_provider_reports_unknown_tickers() -> None:
    provider = StaticProvider({})
    with pytest.raises(DataUnavailableError, match="no data configured"):
        provider.fetch(TICKER, date(2020, 1, 1), date(2021, 1, 1))


# ------------------------------------------------------------------ load_history


def test_load_history_returns_market_data() -> None:
    provider = StaticProvider({TICKER: make_ohlcv(400)})
    data = load_history(strategy_for(), provider, now=date(2030, 1, 1))
    assert isinstance(data, MarketData)
    assert data.ticker == TICKER
    assert len(data) > 0
    assert data.effective_start >= date(2020, 1, 1)


def test_load_history_enforces_minimum_bars() -> None:
    provider = StaticProvider({TICKER: make_ohlcv(400)})
    with pytest.raises(InsufficientHistoryError, match="needs at least"):
        load_history(
            strategy_for(),
            provider,
            min_bars=10_000,
            now=date(2030, 1, 1),
        )


def test_market_data_slices_stay_valid() -> None:
    provider = StaticProvider({TICKER: make_ohlcv(400)})
    data = load_history(strategy_for(), provider, now=date(2030, 1, 1))
    head = data.head(100)
    assert len(head) == 100
    assert head.ticker == data.ticker
    assert len(data.slice(10, 20)) == 10


# ------------------------------------------------------------------ cache


def test_cache_round_trips_exactly(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path)
    frame = prepare_frame(make_ohlcv(50), ticker=TICKER, now=date(2030, 1, 1))
    start, end = date(2020, 1, 1), date(2021, 1, 1)

    assert cache.get("p", TICKER, start, end) is None
    cache.put("p", TICKER, start, end, frame)
    restored = cache.get("p", TICKER, start, end)

    assert restored is not None
    pd.testing.assert_frame_equal(frame, restored)


def test_cache_entries_are_keyed_by_range(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path)
    frame = prepare_frame(make_ohlcv(50), ticker=TICKER, now=date(2030, 1, 1))
    cache.put("p", TICKER, date(2020, 1, 1), date(2021, 1, 1), frame)
    assert cache.get("p", TICKER, date(2020, 1, 1), date(2022, 1, 1)) is None


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    cache = FrameCache(tmp_path)
    start, end = date(2020, 1, 1), date(2021, 1, 1)
    frame = prepare_frame(make_ohlcv(10), ticker=TICKER, now=date(2030, 1, 1))
    cache.put("p", TICKER, start, end, frame)

    path = next(tmp_path.rglob("*.parquet"))
    path.write_bytes(b"not parquet")

    assert cache.get("p", TICKER, start, end) is None


# ------------------------------------------------- forward-fill provenance (audit A3, A4)


def gappy_frame(bars: int = 60, holes: tuple[int, ...] = (10, 11, 30)) -> pd.DataFrame:
    """A frame with interior sessions missing every field, as a halted ticker produces."""
    raw = make_ohlcv(bars)
    raw.iloc[list(holes)] = np.nan
    return raw


def test_prepare_history_reports_which_bars_were_forward_filled() -> None:
    frame, filled = prepare_history(gappy_frame(), ticker=TICKER, now=date(2030, 1, 1))

    assert int(filled.sum()) == 3
    assert list(filled.index) == list(frame.index)
    assert bool(filled.iloc[10]) and bool(filled.iloc[11]) and bool(filled.iloc[30])
    assert not bool(filled.iloc[12])


def test_a_forward_filled_bar_duplicates_the_previous_session() -> None:
    """The reason filled bars must be counted, and it is worse than a flat bar.

    ``ffill`` carries the whole row forward, so the fabricated bar has a zero close-to-close
    return *and* a high/low range copied from a session that is not this one. A stop can be
    triggered on that date by a price that never traded on it.
    """
    frame, _ = prepare_history(gappy_frame(), ticker=TICKER, now=date(2030, 1, 1))

    assert float(frame["Close"].iloc[10]) == float(frame["Close"].iloc[9])
    for column in OHLCV_COLUMNS:
        assert float(frame[column].iloc[10]) == float(frame[column].iloc[9])
    assert float(frame["High"].iloc[10]) > float(frame["Low"].iloc[10])


def test_leading_gaps_are_dropped_rather_than_counted_as_filled() -> None:
    """Defect D5: pre-listing bars are removed, so they are not fills."""
    raw = make_ohlcv(40)
    raw.iloc[:5] = np.nan

    frame, filled = prepare_history(raw, ticker=TICKER, now=date(2030, 1, 1))

    assert len(frame) == 35
    assert int(filled.sum()) == 0


def test_market_data_exposes_the_filled_fraction() -> None:
    frame, filled = prepare_history(gappy_frame(), ticker=TICKER, now=date(2030, 1, 1))

    data = MarketData(
        ticker=TICKER,
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
        filled=filled,
    )

    assert data.filled_bars == 3
    assert data.filled_fraction == pytest.approx(3 / len(frame))


def test_slicing_market_data_carries_the_filled_mask() -> None:
    frame, filled = prepare_history(gappy_frame(), ticker=TICKER, now=date(2030, 1, 1))
    data = MarketData(
        ticker=TICKER,
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
        filled=filled,
    )

    assert data.head(12).filled_bars == 2
    assert data.slice(12, 40).filled_bars == 1


def test_a_misaligned_filled_mask_is_rejected() -> None:
    frame = prepare_frame(make_ohlcv(20), ticker=TICKER, now=date(2030, 1, 1))

    with pytest.raises(DataContractError, match="must be aligned"):
        MarketData(
            ticker=TICKER,
            frame=frame,
            requested_start=date(2020, 1, 1),
            requested_end=date(2030, 1, 1),
            filled=pd.Series([True, False]),
        )


def test_too_much_forward_filling_is_refused() -> None:
    """Audit A3: past a small fraction the backtest studies invented prices."""
    raw = gappy_frame(60, holes=tuple(range(20, 40)))
    provider = StaticProvider({TICKER: raw})

    with pytest.raises(DataQualityError, match="forward-filled"):
        load_history(
            strategy_for(),
            provider,
            max_filled_fraction=0.01,
            now=date(2030, 1, 1),
        )


def test_a_few_filled_bars_are_permitted() -> None:
    provider = StaticProvider({TICKER: gappy_frame(600, holes=(10,))})

    data = load_history(
        strategy_for(),
        provider,
        max_filled_fraction=0.01,
        now=date(2030, 1, 1),
    )

    assert data.filled_bars == 1


def test_load_history_permits_everything_by_default() -> None:
    """The threshold is opt-in, so the data layer stays usable without settings."""
    provider = StaticProvider({TICKER: gappy_frame(60, holes=tuple(range(20, 40)))})

    assert load_history(strategy_for(), provider, now=date(2030, 1, 1)).filled_bars > 0


def test_the_incomplete_bar_cutoff_is_utc() -> None:
    """Audit A4: the index is normalised to UTC, so the cutoff must be too."""
    from datetime import UTC, datetime

    assert _utc_today() == datetime.now(UTC).date()
