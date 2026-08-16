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
from cracktrade.data.loader import load_history, prepare_frame, prepare_history
from cracktrade.data.provider import MarketDataProvider, StaticProvider, YFinanceProvider

__all__ = [
    "DTYPE",
    "OHLCV_COLUMNS",
    "PRICE_COLUMNS",
    "FrameCache",
    "MarketData",
    "MarketDataProvider",
    "StaticProvider",
    "YFinanceProvider",
    "load_history",
    "prepare_frame",
    "prepare_history",
    "validate_frame",
]
