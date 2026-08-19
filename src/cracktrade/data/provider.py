"""Market-data providers.

The engine depends on the :class:`MarketDataProvider` protocol, never on yfinance. Swapping in
a different vendor, a fixture, or a database is a matter of writing one ``fetch`` method.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

from cracktrade.config import Interval
from cracktrade.data.contract import OHLCV_COLUMNS
from cracktrade.errors import DataUnavailableError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Supplies raw bars for one ticker at one interval.

    Implementations return whatever shape their source produces; normalisation into the
    engine's contract is :mod:`cracktrade.data.loader`'s job. The only hard requirements are a
    DatetimeIndex and the presence of the five OHLCV columns under recognisable names.
    """

    @property
    def name(self) -> str:
        """Short identifier, used in cache keys and error messages."""
        ...

    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame:
        """Return bars for ``ticker`` covering ``start`` to ``end`` inclusive.

        Raises:
            DataUnavailableError: nothing was returned for this ticker and range.
        """
        ...


class YFinanceProvider:
    """Bars from Yahoo Finance.

    Every yfinance option that affects the numbers is passed explicitly rather than inherited,
    because their defaults have changed between releases -- ``auto_adjust`` in particular
    flipped, which silently switches the whole history between adjusted and unadjusted prices.
    """

    name = "yfinance"

    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame:
        """Download bars, translating yfinance's exclusive end date into an inclusive one."""
        import yfinance

        # yfinance treats `end` as exclusive; add a day so the user's end_date is included.
        inclusive_end = end + timedelta(days=1)

        frame = yfinance.download(
            tickers=ticker,
            start=start.isoformat(),
            end=inclusive_end.isoformat(),
            interval=interval.value,
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
            group_by="column",
            # Regular session only. Pinned rather than inherited for the same reason as
            # auto_adjust, and because it matches what the trader can actually execute: XTB
            # trades EU stocks and ETFs during exchange hours, so a backtest that filled on
            # a pre-market print would be modelling a trade nobody could place.
            prepost=False,
        )

        if frame is None or frame.empty:
            raise DataUnavailableError(_empty_response_message(ticker, start, end, interval))

        return _flatten_columns(frame, ticker)


def _empty_response_message(ticker: str, start: date, end: date, interval: Interval) -> str:
    """Explain an empty response, naming the interval's limit when that is the likely cause.

    yfinance does *not* raise when a range exceeds what it will serve: it prints the reason to
    stdout and returns an empty frame. So the only signal reaching this code is emptiness, and
    a bare "no data returned" would leave the user guessing between a wrong ticker, a holiday
    range, and a limit they have never heard of. Measured behaviour, pinned in
    ``tests/fixtures/README.md``.
    """
    base = f"no data returned for {ticker!r} between {start} and {end} at {interval.value} bars"
    limit = interval.max_lookback
    if limit is None:
        return base
    earliest = datetime.now(UTC).date() - limit
    return (
        f"{base}. {interval.value} bars are only served for roughly the last {limit.days} days, "
        f"so a range starting before about {earliest} cannot be fetched at all -- use 1h (about "
        f"two years) or 1d (no limit), or move the range forward"
    )


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
    """Serves frames supplied up front.

    The seam that makes the engine testable without a network, and the shape a future
    database-backed provider takes.

    Frames are keyed by ticker, or by ``(ticker, interval)`` when a test needs more than one
    bar width for the same symbol. The plain-ticker form serves any interval asked for, which
    keeps every daily test that predates intraday support working unchanged.
    """

    name = "static"

    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | None = None,
        *,
        by_interval: dict[tuple[str, Interval], pd.DataFrame] | None = None,
    ) -> None:
        self._frames = frames or {}
        self._by_interval = by_interval or {}

    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame:
        """Return the configured frame for ``ticker``, restricted to the requested range."""
        frame = self._by_interval.get((ticker, interval))
        if frame is None:
            frame = self._frames.get(ticker)
        if frame is None:
            configured = sorted(
                {*self._frames, *(f"{key[0]} at {key[1].value}" for key in self._by_interval)}
            )
            msg = f"no data configured for {ticker!r} at {interval.value}; have {configured}"
            raise DataUnavailableError(msg)

        window = _restrict(frame, start, end)
        if window.empty:
            msg = f"no data for {ticker!r} between {start} and {end}"
            raise DataUnavailableError(msg)
        missing = [column for column in OHLCV_COLUMNS if column not in window.columns]
        if missing:
            msg = f"configured frame for {ticker!r} is missing columns: {missing}"
            raise DataUnavailableError(msg)
        return window


def _restrict(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Slice a fixture frame to the requested inclusive date range.

    Boolean masking on the index's *date* rather than label slicing, because an intraday
    fixture's index is timezone-aware and a naive label slice against one is a comparison
    between an aware and a naive timestamp -- which pandas raises on.
    """
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):  # pragma: no cover - fixtures are datetime-indexed
        return frame.loc[str(start) : str(end)]
    dates = index.date
    return frame[(dates >= start) & (dates <= end)]
