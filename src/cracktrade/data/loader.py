"""Turning a provider's raw response into a frame the engine will accept.

Normative reference: ``docs/ENGINE_SPEC.md`` section 4.3. Two rules here matter more than the
rest, and both are causality rules rather than tidiness:

* ``bfill`` is never applied. Backward-filling moves a *later* price onto an *earlier* bar,
  which is look-ahead in the data layer, and for a late-listing ticker it invents an entire
  pre-listing history from the first real close (defect D5).
* The current day's partial bar is dropped, so a run at 14:00 and a run at 22:00 see the same
  history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pandas as pd

from cracktrade.data.contract import DTYPE, OHLCV_COLUMNS, MarketData, validate_frame
from cracktrade.errors import DataContractError, DataQualityError, InsufficientHistoryError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from cracktrade.config import Strategy
    from cracktrade.data.cache import FrameCache
    from cracktrade.data.provider import MarketDataProvider

logger = get_logger(__name__)


def load_history(
    strategy: Strategy,
    provider: MarketDataProvider,
    *,
    cache: FrameCache | None = None,
    min_bars: int = 0,
    max_filled_fraction: float = 1.0,
    today: date | None = None,
) -> MarketData:
    """Fetch and prepare the price history a strategy needs.

    Args:
        strategy: supplies the ticker and date range.
        provider: where the bars come from.
        cache: optional input cache; ``None`` disables it.
        min_bars: reject the history if fewer than this many bars survive preparation. Callers
            pass the strategy's warm-up requirement plus a margin (defect D11).
        max_filled_fraction: reject the history if more than this share of bars had to be
            forward-filled. Defaults to permitting everything; callers pass
            ``Settings.max_filled_fraction`` (audit finding A3).
        today: overrides "today" for incomplete-bar detection. Tests pass it; nothing else
            should.

    Raises:
        DataUnavailableError: the provider returned nothing.
        DataContractError: the response could not be normalised into the engine's shape.
        InsufficientHistoryError: too few bars survived.
        DataQualityError: too much of the history is forward-filled.
    """
    ticker = strategy.universe.ticker
    start = strategy.universe.start_date
    end = strategy.universe.end_date

    raw = _fetch(provider, ticker, start, end, cache)
    frame, filled = prepare_history(raw, ticker=ticker, today=today)

    if len(frame) < min_bars:
        msg = (
            f"{ticker} has {len(frame)} usable bars between {start} and {end}, "
            f"but this strategy needs at least {min_bars} to warm up its indicators; "
            f"widen the date range"
        )
        raise InsufficientHistoryError(msg)

    data = MarketData(
        ticker=ticker,
        frame=frame,
        requested_start=start,
        requested_end=end,
        filled=filled,
    )

    if data.filled_fraction > max_filled_fraction:
        msg = (
            f"{ticker} has {data.filled_bars} forward-filled bars out of {len(data)} "
            f"({data.filled_fraction:.1%}), above the {max_filled_fraction:.1%} limit. "
            f"A filled bar repeats the previous session wholesale -- zero return, and a "
            f"high/low range belonging to another date -- so this much of it would make the "
            f"backtest a study of invented prices"
        )
        raise DataQualityError(msg)

    if data.filled_bars:
        logger.warning(
            "%s: %d of %d bars (%.2f%%) were forward-filled and are not observed prices",
            ticker,
            data.filled_bars,
            len(data),
            100 * data.filled_fraction,
        )
    if data.effective_start > start:
        logger.info(
            "%s has no data before %s; the backtest starts there rather than %s",
            ticker,
            data.effective_start,
            start,
        )
    return data


def _fetch(
    provider: MarketDataProvider,
    ticker: str,
    start: date,
    end: date,
    cache: FrameCache | None,
) -> pd.DataFrame:
    if cache is not None:
        cached = cache.get(provider.name, ticker, start, end)
        if cached is not None:
            return cached

    logger.info("fetching %s from %s (%s to %s)", ticker, provider.name, start, end)
    raw = provider.fetch(ticker, start, end)

    if cache is not None:
        cache.put(provider.name, ticker, start, end, raw)
    return raw


def prepare_frame(
    raw: pd.DataFrame,
    *,
    ticker: str,
    today: date | None = None,
) -> pd.DataFrame:
    """Normalise a provider response into the engine's OHLCV contract."""
    return prepare_history(raw, ticker=ticker, today=today)[0]


def prepare_history(
    raw: pd.DataFrame,
    *,
    ticker: str,
    today: date | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Normalise a provider response, and report which bars were forward-filled.

    Returns the frame and a boolean series, aligned to it, that is True on bars where at least
    one field had to be carried forward from the previous session.

    A forward-filled bar is a fabricated bar, and in a way that is easy to underestimate:
    ``ffill`` carries the *entire previous row* forward, not just the close. So the bar has a
    zero close-to-close return, which understates realised volatility and inflates Sharpe --
    and it also carries a high/low range copied from a different session, which means a
    stop-loss can be triggered on that date by a price that never traded on it.

    The engine keeps forward-filling, because the alternative is a hole in the index, but it
    stops doing so invisibly.
    """
    frame = _select_columns(raw, ticker)
    frame = _normalise_index(frame, ticker)
    frame = _drop_incomplete_bar(frame, today=today or _utc_today())
    frame = frame.astype(DTYPE)

    incomplete = frame.isna().any(axis=1)

    # Forward fill only. See the module docstring.
    frame = frame.ffill()
    frame = _drop_leading_gap(frame, ticker)

    # Rows still incomplete after the leading gap was trimmed are the ones ffill repaired;
    # the leading ones were dropped rather than filled and must not be counted here.
    filled = incomplete.reindex(frame.index).fillna(value=False).astype(bool)

    validate_frame(frame)
    return frame, filled


def _utc_today() -> date:
    """Today in UTC.

    ``date.today()`` reads the host's local calendar while the index has been normalised to
    UTC, which would make the incomplete-bar cutoff depend on where the machine is. The
    determinism guarantee is per UTC day.
    """
    return datetime.now(UTC).date()


def _select_columns(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Capitalise labels and select the five columns the engine needs."""
    renamed = raw.rename(columns=lambda label: str(label).strip().capitalize())
    missing = [column for column in OHLCV_COLUMNS if column not in renamed.columns]
    if missing:
        msg = (
            f"response for {ticker!r} is missing required column(s) {missing}; "
            f"it has {sorted(str(column) for column in renamed.columns)}"
        )
        raise DataContractError(msg)
    return renamed.loc[:, list(OHLCV_COLUMNS)].copy()


def _normalise_index(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Guarantee a sorted, unique, timezone-naive DatetimeIndex.

    ``reset_index`` is never used: vectorbt derives annualisation from the index, so losing it
    silently changes every time-based metric.
    """
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        try:
            index = pd.DatetimeIndex(index)
        except (TypeError, ValueError) as error:
            msg = f"response for {ticker!r} has a non-datetime index: {error}"
            raise DataContractError(msg) from error

    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    index = index.normalize()
    # Rebuild the index to drop any inferred frequency. Real market data has holidays and
    # halts, so a synthetic frame carrying freq="B" would differ in metadata from an
    # identical downloaded one -- and metadata differences make round-trip and truncation
    # comparisons fail for reasons that have nothing to do with the prices.
    index = pd.DatetimeIndex(index.to_numpy(), name=index.name)

    result = frame.set_axis(index, axis="index")
    result = result[~result.index.duplicated(keep="last")]
    return result.sort_index()


def _drop_incomplete_bar(frame: pd.DataFrame, *, today: date) -> pd.DataFrame:
    """Drop the current day's bar, which may still be forming.

    Deterministic by construction: whatever time of day the engine runs, it sees the same
    history. The cost is one bar; the alternative is results that change between runs.
    """
    if frame.empty:
        return frame
    last = frame.index[-1]
    if last.date() >= today:
        return frame.iloc[:-1]
    return frame


def _drop_leading_gap(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Remove bars before the ticker's first complete observation.

    After a forward fill, any remaining NaN is a leading one -- the ticker had not started
    trading yet. Those rows are dropped rather than filled backwards.
    """
    complete = frame.notna().all(axis=1)
    if not bool(complete.any()):
        msg = f"{ticker!r} has no complete bars in the requested range"
        raise DataContractError(msg)

    first_complete = int(complete.to_numpy().argmax())
    trimmed = frame.iloc[first_complete:]

    remaining = trimmed.isna().sum().sum()
    if remaining:
        # A hole in the middle of the history: forward fill should have closed it, so this is
        # a malformed response rather than a gap we are entitled to paper over.
        msg = (
            f"{ticker!r} still has {int(remaining)} missing value(s) after forward fill, "
            f"which suggests a malformed response"
        )
        raise DataContractError(msg)
    return trimmed
