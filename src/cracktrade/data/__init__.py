"""Market data: retrieval, normalisation, and the frame contract the engine consumes."""

from __future__ import annotations

from cracktrade.data.cache import FrameCache
from cracktrade.data.contract import (
    DTYPE,
    OHLCV_COLUMNS,
    PRICE_COLUMNS,
    MarketData,
    validate_frame,
)
from cracktrade.data.loader import load_history, prepare_frame, prepare_history, source_timezone
from cracktrade.data.provider import MarketDataProvider, StaticProvider, YFinanceProvider
from cracktrade.data.sessions import (
    bars_per_session,
    is_session_open,
    median_bars_per_session,
    session_count,
    session_ids,
)

__all__ = [
    "DTYPE",
    "OHLCV_COLUMNS",
    "PRICE_COLUMNS",
    "FrameCache",
    "MarketData",
    "MarketDataProvider",
    "StaticProvider",
    "YFinanceProvider",
    "bars_per_session",
    "is_session_open",
    "load_history",
    "median_bars_per_session",
    "prepare_frame",
    "prepare_history",
    "session_count",
    "session_ids",
    "source_timezone",
    "validate_frame",
]
