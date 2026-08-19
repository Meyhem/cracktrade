"""Turning a provider's raw response into a frame the engine will accept.

Normative reference: ``docs/ENGINE_SPEC.md`` section 4.3. Two rules here matter more than the
rest, and both are causality rules rather than tidiness:

* ``bfill`` is never applied. Backward-filling moves a *later* price onto an *earlier* bar,
  which is look-ahead in the data layer, and for a late-listing ticker it invents an entire
  pre-listing history from the first real close (defect D5).
* The still-forming bar is dropped, so a run at 14:00 and a run at 22:00 see the same history.

Intraday data adds a third rule, and it is the one that is easy to get wrong: **forward-filling
never crosses a session boundary.** A global ``ffill`` on 30-minute bars would repair a missing
09:00 print by copying yesterday's 17:00 row onto it -- pinning the entire overnight gap onto a
bar that never traded, and handing a stop-loss a high/low range from a different day to fire
against. Filling happens within a session or not at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from cracktrade.config import Interval
from cracktrade.data.contract import DTYPE, OHLCV_COLUMNS, PRICE_COLUMNS, MarketData, validate_frame
from cracktrade.errors import DataContractError, DataQualityError, InsufficientHistoryError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from cracktrade.config import Strategy
    from cracktrade.data.cache import FrameCache
    from cracktrade.data.provider import MarketDataProvider

logger = get_logger(__name__)

#: yfinance's split/dividend adjustment computes each OHLC column independently, which
#: occasionally leaves the adjusted High a few ulps below the adjusted Close (or Low a few ulps
#: above Open) on the same bar -- a ~1e-12 relative discrepancy, nowhere near a real pricing
#: error. Repaired here with a tolerance far tighter than any plausible real error, rather than
#: loosening validate_frame's strict bound, so a genuinely malformed bar is still rejected there.
_ADJUSTMENT_NOISE_RTOL: Final = 1e-8


def load_history(
    strategy: Strategy,
    provider: MarketDataProvider,
    *,
    cache: FrameCache | None = None,
    min_bars: int = 0,
    max_filled_fraction: float = 1.0,
    now: date | datetime | None = None,
) -> MarketData:
    """Fetch and prepare the price history a strategy needs.

    Args:
        strategy: supplies the ticker, date range, and bar interval.
        provider: where the bars come from.
        cache: optional input cache; ``None`` disables it.
        min_bars: reject the history if fewer than this many bars survive preparation. Callers
            pass the strategy's warm-up requirement plus a margin (defect D11).
        max_filled_fraction: reject the history if more than this share of bars had to be
            forward-filled. Defaults to permitting everything; callers pass
            ``Settings.max_filled_fraction`` (audit finding A3).
        now: overrides the present moment for incomplete-bar detection. Tests pass it; nothing
            else should.

    Raises:
        DataUnavailableError: the provider returned nothing.
        DataContractError: the response could not be normalised into the engine's shape.
        InsufficientHistoryError: too few bars survived.
        DataQualityError: too much of the history is forward-filled.
    """
    ticker = strategy.universe.ticker
    start = strategy.universe.start_date
    end = strategy.universe.end_date
    interval = strategy.universe.interval

    raw = _fetch(provider, ticker, start, end, interval, cache)
    timezone = source_timezone(raw)
    frame, filled = prepare_history(raw, ticker=ticker, interval=interval, now=now)

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
        interval=interval,
        timezone=timezone,
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
    interval: Interval,
    cache: FrameCache | None,
) -> pd.DataFrame:
    if cache is not None:
        cached = cache.get(provider.name, ticker, start, end, interval)
        if cached is not None:
            return cached

    logger.info(
        "fetching %s from %s (%s to %s at %s)", ticker, provider.name, start, end, interval.value
    )
    raw = provider.fetch(ticker, start, end, interval)

    if cache is not None:
        cache.put(provider.name, ticker, start, end, raw, interval)
    return raw


def source_timezone(raw: pd.DataFrame) -> str | None:
    """The IANA zone a raw response's index carries, or ``None`` if it is already naive.

    Read from the *raw* frame, before normalisation strips the offset. Intraday bars arrive
    tagged with the exchange's own zone -- ``Europe/Berlin`` for Xetra, ``Europe/London`` for
    the LSE -- and that name is the only thing that lets a later reader turn a naive
    wall-clock timestamp back into a real moment.
    """
    index = raw.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        return None
    return str(index.tz)


def prepare_frame(
    raw: pd.DataFrame,
    *,
    ticker: str,
    interval: Interval = Interval.D1,
    now: date | datetime | None = None,
) -> pd.DataFrame:
    """Normalise a provider response into the engine's OHLCV contract."""
    return prepare_history(raw, ticker=ticker, interval=interval, now=now)[0]


def prepare_history(
    raw: pd.DataFrame,
    *,
    ticker: str,
    interval: Interval = Interval.D1,
    now: date | datetime | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Normalise a provider response, and report which bars were forward-filled.

    Returns the frame and a boolean series, aligned to it, that is True on bars where at least
    one field had to be carried forward from the previous bar.

    A forward-filled bar is a fabricated bar, and in a way that is easy to underestimate:
    ``ffill`` carries the *entire previous row* forward, not just the close. So the bar has a
    zero close-to-close return, which understates realised volatility and inflates Sharpe --
    and it also carries a high/low range copied from a different bar, which means a stop-loss
    can be triggered by a price that never traded then.

    The engine keeps forward-filling, because the alternative is a hole in the index, but it
    stops doing so invisibly -- and on intraday data it stops doing so across sessions at all.
    """
    frame = _select_columns(raw, ticker)
    frame, timezone = _normalise_index(frame, ticker, interval)
    frame = _drop_unclosed_bars(frame, interval=interval, timezone=timezone, now=now)
    frame = frame.astype(DTYPE)

    incomplete = frame.isna().any(axis=1)

    # Forward fill only, and never across a session boundary. See the module docstring.
    frame = _forward_fill(frame, interval)
    frame = _drop_leading_gap(frame, ticker, interval)

    # Rows still incomplete after the leading gap was trimmed are the ones ffill repaired;
    # the leading ones were dropped rather than filled and must not be counted here.
    filled = incomplete.reindex(frame.index).fillna(value=False).astype(bool)

    frame = _repair_adjustment_noise(frame, ticker)

    validate_frame(frame)
    return frame, filled


def _forward_fill(frame: pd.DataFrame, interval: Interval) -> pd.DataFrame:
    """Carry the previous bar forward over a hole -- within one session only, when intraday.

    A global ``ffill`` is correct on daily bars, where "the previous bar" is the previous
    trading day and carrying it forward models a day the ticker did not trade.

    On intraday bars it is not. A missing 09:00 print filled from the previous day's 17:00 row
    does not model a quiet half hour: it fabricates a bar that swallows the entire overnight
    gap, reports a zero return across it, and offers a stop-loss a high/low range from a
    different day to fire against. So the fill is grouped by local calendar date, and a hole at
    a session's open -- which has no earlier bar in its own session to copy -- is left as NaN
    and dropped later rather than invented.

    In practice yfinance omits untraded intraday bars rather than emitting NaN rows, so this
    path is rarely exercised. It is written correctly anyway; "rarely" is not "never", and the
    failure it would otherwise produce is silent.
    """
    if not interval.is_intraday:
        return frame.ffill()

    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    # groupby(...).ffill() drops the grouping key and returns the value columns aligned to the
    # original index, which is exactly the shape wanted here.
    return frame.groupby(index.normalize()).ffill()


def _repair_adjustment_noise(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Snap High/Low back onto the bar's other prices when they differ by float noise only.

    A bar where the discrepancy exceeds the tolerance is left untouched -- that is a real data
    problem and must still fail :func:`validate_frame`.
    """
    price = frame[list(PRICE_COLUMNS)]
    highest = price.max(axis=1)
    lowest = price.min(axis=1)

    high_noise = (frame["High"] < highest) & np.isclose(
        frame["High"], highest, rtol=_ADJUSTMENT_NOISE_RTOL, atol=0.0
    )
    low_noise = (frame["Low"] > lowest) & np.isclose(
        frame["Low"], lowest, rtol=_ADJUSTMENT_NOISE_RTOL, atol=0.0
    )

    repaired = int(high_noise.sum() + low_noise.sum())
    if not repaired:
        return frame

    frame = frame.copy()
    frame.loc[high_noise, "High"] = highest[high_noise]
    frame.loc[low_noise, "Low"] = lowest[low_noise]
    logger.debug("%s: repaired sub-ulp High/Low adjustment noise on %d bar(s)", ticker, repaired)
    return frame


def _utc_today() -> date:
    """Today in UTC.

    ``date.today()`` reads the host's local calendar while a daily index has been normalised to
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


def _normalise_index(
    frame: pd.DataFrame, ticker: str, interval: Interval
) -> tuple[pd.DataFrame, str | None]:
    """Guarantee a sorted, unique, timezone-naive DatetimeIndex, and report the zone dropped.

    ``reset_index`` is never used: vectorbt derives annualisation from the index, so losing it
    silently changes every time-based metric.

    The two intervals take different routes to "naive", and the difference is load-bearing:

    * **Daily** converts to UTC and then truncates to midnight, as it always has. Unchanged,
      byte for byte -- daily results are a frozen regression surface.
    * **Intraday** drops the offset *without converting*, keeping exchange-local wall-clock
      time, and does **not** truncate. ``.normalize()`` on intraday data would set every
      timestamp of a session to midnight, collapsing seventeen distinct Xetra bars into
      seventeen duplicates of the same instant -- most of which the duplicate filter below
      would then silently delete. Converting to UTC first would be subtler and just as wrong:
      the EU and the US change to summer time on different dates, so a Xetra session that
      reads 09:00-17:30 all year would drift between 07:00-15:30 and 08:00-16:30 in UTC,
      moving the session boundary that the forced-close logic keys off.
    """
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        try:
            index = pd.DatetimeIndex(index)
        except (TypeError, ValueError) as error:
            msg = f"response for {ticker!r} has a non-datetime index: {error}"
            raise DataContractError(msg) from error

    timezone = str(index.tz) if index.tz is not None else None

    if interval.is_intraday:
        if index.tz is not None:
            # tz_localize(None) keeps the wall-clock reading and discards the offset.
            index = index.tz_localize(None)
    else:
        if index.tz is not None:
            index = index.tz_convert("UTC").tz_localize(None)
        index = index.normalize()
        timezone = None

    # Rebuild the index to drop any inferred frequency. Real market data has holidays and
    # halts, so a synthetic frame carrying freq="B" would differ in metadata from an
    # identical downloaded one -- and metadata differences make round-trip and truncation
    # comparisons fail for reasons that have nothing to do with the prices.
    index = pd.DatetimeIndex(index.to_numpy(), name=index.name)

    result = frame.set_axis(index, axis="index")
    result = result[~result.index.duplicated(keep="last")]
    return result.sort_index(), timezone


def _drop_unclosed_bars(
    frame: pd.DataFrame,
    *,
    interval: Interval,
    timezone: str | None,
    now: date | datetime | None,
) -> pd.DataFrame:
    """Drop bars that have not finished forming.

    Deterministic by construction: whatever time of day the engine runs, it sees the same
    history. The cost is one bar; the alternative is results that change between runs.

    On daily data the rule is unchanged -- the last row is dropped if its date has reached
    today in UTC. On intraday data the guarantee shifts from "the same history per UTC day" to
    "the same history per closed bar": a bar stamped 17:00 on a 30-minute Xetra strategy is
    complete at 17:30 Frankfurt time and not before, so it is kept only once its close has
    passed. Yahoo does return the still-forming bar, which is what makes this necessary rather
    than theoretical.
    """
    if frame.empty:
        return frame

    if not interval.is_intraday:
        today = now.date() if isinstance(now, datetime) else now or _utc_today()
        last = frame.index[-1]
        if last.date() >= today:
            return frame.iloc[:-1]
        return frame

    cutoff = _local_now(timezone, now)
    index = frame.index
    assert isinstance(index, pd.DatetimeIndex)
    closed = index + interval.bar_timedelta <= cutoff
    dropped = int((~closed).sum())
    if dropped:
        logger.debug("dropped %d bar(s) that had not closed as of %s", dropped, cutoff)
    return frame[closed]


def _local_now(timezone: str | None, now: date | datetime | None) -> pd.Timestamp:
    """The present moment, as a naive timestamp on the same wall clock as the index.

    An intraday index carries exchange-local wall-clock time, so the cutoff it is compared
    against has to be on that clock too. A tz-aware ``now`` is converted into the exchange's
    zone; a naive one is taken to be exchange-local already; a bare date means midnight, which
    makes "everything from this date onward is unclosed" the natural reading for a test that
    only cares about the day.
    """
    if isinstance(now, datetime):
        moment = pd.Timestamp(now)
    elif now is not None:
        return pd.Timestamp(now)
    else:
        moment = pd.Timestamp(datetime.now(UTC))

    if moment.tz is None:
        return moment
    if timezone is None:
        return moment.tz_convert("UTC").tz_localize(None)
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:  # pragma: no cover - provider-supplied
        msg = f"response carries an unknown timezone {timezone!r}: {error}"
        raise DataContractError(msg) from error
    return moment.tz_convert(zone).tz_localize(None)


def _drop_leading_gap(frame: pd.DataFrame, ticker: str, interval: Interval) -> pd.DataFrame:
    """Remove bars before the ticker's first complete observation.

    After a forward fill, any remaining NaN is a leading one -- the ticker had not started
    trading yet. Those rows are dropped rather than filled backwards.

    Intraday adds a second legitimate source of residual NaN: a hole at a session's *open*,
    which the session-scoped fill deliberately refuses to close because the only row it could
    copy belongs to the previous day. Those rows are dropped too. Dropping a bar loses
    information; synthesising one invents it, and an invented opening bar is a price a stop can
    fire against on a morning when the market never printed it.
    """
    complete = frame.notna().all(axis=1)
    if not bool(complete.any()):
        msg = f"{ticker!r} has no complete bars in the requested range"
        raise DataContractError(msg)

    first_complete = int(complete.to_numpy().argmax())
    trimmed = frame.iloc[first_complete:]

    remaining = int(trimmed.isna().sum().sum())
    if remaining and interval.is_intraday:
        holes = trimmed.isna().any(axis=1)
        logger.warning(
            "%s: dropped %d intraday bar(s) that were still incomplete after a within-session "
            "forward fill; a hole at a session's open is not filled from the previous day",
            ticker,
            int(holes.sum()),
        )
        return trimmed[~holes]
    if remaining:
        # A hole in the middle of the history: forward fill should have closed it, so this is
        # a malformed response rather than a gap we are entitled to paper over.
        msg = (
            f"{ticker!r} still has {remaining} missing value(s) after forward fill, "
            f"which suggests a malformed response"
        )
        raise DataContractError(msg)
    return trimmed
