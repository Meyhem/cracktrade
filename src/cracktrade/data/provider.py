"""Market-data providers.

The engine depends on the :class:`MarketDataProvider` protocol, never on yfinance. Swapping in
a different vendor, a fixture, or a database is a matter of writing one ``fetch`` method.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

from cracktrade.data.contract import OHLCV_COLUMNS
from cracktrade.errors import DataUnavailableError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Supplies raw daily bars for one ticker.

    Implementations return whatever shape their source produces; normalisation into the
    engine's contract is :mod:`cracktrade.data.loader`'s job. The only hard requirements are a
    DatetimeIndex and the presence of the five OHLCV columns under recognisable names.
    """

    @property
    def name(self) -> str:
        """Short identifier, used in cache keys and error messages."""
        ...

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return daily bars for ``ticker`` covering ``start`` to ``end`` inclusive.

        Raises:
            DataUnavailableError: nothing was returned for this ticker and range.
        """
        ...


class YFinanceProvider:
    """Daily bars from Yahoo Finance.

    Every yfinance option that affects the numbers is passed explicitly rather than inherited,
    because their defaults have changed between releases -- ``auto_adjust`` in particular
    flipped, which silently switches the whole history between adjusted and unadjusted prices.
    """

    name = "yfinance"

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Download bars, translating yfinance's exclusive end date into an inclusive one."""
        import yfinance

        # yfinance treats `end` as exclusive; add a day so the user's end_date is included.
        inclusive_end = end + timedelta(days=1)

        frame = yfinance.download(
            tickers=ticker,
            start=start.isoformat(),
            end=inclusive_end.isoformat(),
            interval="1d",
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
            group_by="column",
        )

        if frame is None or frame.empty:
            msg = f"no data returned for {ticker!r} between {start} and {end}"
            raise DataUnavailableError(msg)

        return _flatten_columns(frame, ticker)


def _flatten_columns(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Collapse yfinance's (Price, Ticker) MultiIndex to flat price labels.

    Recent yfinance releases return a MultiIndex even for a single ticker. An unexpected shape
    raises rather than being reshaped on a guess.
    """
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame

    levels: Sequence[object] = list(frame.columns.levels[1])
    if len(levels) != 1:
        msg = f"expected one ticker in the response for {ticker!r}, got {levels}"
        raise DataUnavailableError(msg)

    flattened = frame.droplevel(axis="columns", level=1)
    logger.debug("flattened MultiIndex response for %s", ticker)
    return flattened


class StaticProvider:
    """Serves a frame supplied up front.

    The seam that makes the engine testable without a network, and the shape a future
    database-backed provider takes.
    """

    name = "static"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return the configured frame for ``ticker``, restricted to the requested range."""
        if ticker not in self._frames:
            msg = f"no data configured for {ticker!r}"
            raise DataUnavailableError(msg)
        frame = self._frames[ticker]
        window = frame.loc[str(start) : str(end)]
        if window.empty:
            msg = f"no data for {ticker!r} between {start} and {end}"
            raise DataUnavailableError(msg)
        missing = [column for column in OHLCV_COLUMNS if column not in window.columns]
        if missing:
            msg = f"configured frame for {ticker!r} is missing columns: {missing}"
            raise DataUnavailableError(msg)
        return window
