"""Per-bar series, captured while a run executes.

Normative reference: ``docs/ENGINE_SPEC.md`` section 8.1.

These are the series a chart is drawn from. They are captured **at run time** rather than
recomputed later, and that is the whole reason this module exists: the data provider
retroactively adjusts history for splits and dividends (§4.1, defect D10), so an equity curve
rebuilt next month would describe different prices than the metrics and the vintage block
recorded beside it. A chart that disagrees with the numbers under it is worse than no chart.

Capture is opt-in. The CLI does not ask for it, so nothing about an ordinary backtest changes.

Nothing here can introduce look-ahead. Every series is a projection of an already-simulated
portfolio, and the one windowed statistic -- the rolling twelve-month return -- uses a trailing
window, never a centred one.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cracktrade.data.contract import OHLCV_COLUMNS
from cracktrade.domain import MonthlyReturns, RunSeries, Series
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from cracktrade.signals import causal_shift

if TYPE_CHECKING:
    import vectorbt as vbt

    from cracktrade.data import MarketData

#: Bars in the trailing window for the rolling annual return.
ROLLING_WINDOW = TRADING_DAYS_PER_YEAR

#: The frame's close column. Taken from the data contract rather than spelled out, so a
#: renamed column breaks at import rather than at the first chart.
CLOSE = OHLCV_COLUMNS[3]


def _dates_of(index: pd.DatetimeIndex) -> tuple[date, ...]:
    """Bar timestamps, at the precision the bars actually have.

    A daily index is dates. An intraday one must keep its time, or every bar in a session lands
    on the same x-value: seventeen Xetra half-hours drawn at midnight is not a curve, it is a
    vertical line repeated thirty-five times, and the chart would silently disagree with the
    trade list beside it.

    Decided from the index rather than from a passed-in interval because it is exactly
    checkable here: every timestamp of a daily index is midnight, so a nonzero time component
    is proof the bars are intraday and nothing else. ``datetime`` is a subclass of ``date``, so
    the declared type covers both and the serializer's ``isoformat`` widens with the value.
    """
    if index.normalize().equals(index):
        return tuple(timestamp.date() for timestamp in index)
    return tuple(timestamp.to_pydatetime() for timestamp in index)


def _series(values: pd.Series) -> Series:
    """A pandas series as plain data, with non-finite values dropped to ``None``-free floats."""
    clean = values.astype(float).to_numpy()
    assert isinstance(values.index, pd.DatetimeIndex)
    return Series(
        dates=_dates_of(values.index),
        values=tuple(float(value) if np.isfinite(value) else 0.0 for value in clean),
    )


def _drawdown(equity: pd.Series) -> pd.Series:
    """Percentage below the running peak, as a negative number.

    Computed from the equity curve rather than taken from vectorbt so that the chart and the
    reported ``max_drawdown_pct`` cannot come from two different definitions.
    """
    peak = equity.cummax()
    return 100.0 * (equity - peak) / peak.replace(0.0, np.nan)


def _monthly(equity: pd.Series, holdings: pd.Series) -> MonthlyReturns:
    monthly_equity = equity.resample("ME").last()
    opening = equity.resample("ME").first()
    returns = 100.0 * (monthly_equity - opening) / opening.replace(0.0, np.nan)
    # ``max`` rather than ``any``: pandas-stubs types the resampler's ``any`` as a groupby
    # attribute rather than a method. On a boolean series the two agree, and this one
    # type-checks.
    held = holdings.ne(0).resample("ME").max()

    return MonthlyReturns(
        months=tuple(str(stamp)[:7] for stamp in returns.index),
        values=tuple(float(value) if pd.notna(value) else 0.0 for value in returns.to_numpy()),
        in_market=tuple(bool(flag) for flag in held.reindex(returns.index, fill_value=False)),
    )


def _rolling_annual(equity: pd.Series) -> Series:
    """Trailing twelve-month return at each bar.

    Shifted through :func:`~cracktrade.signals.causal_shift`, the engine's single alignment
    path, rather than calling ``.shift`` here. That helper raises on a negative period, so a
    centred or forward-looking window is not merely discouraged in this function -- it is
    unexpressible. A chart is exactly as capable of implying the future as a metric is, and the
    repo test that found this line was right to.
    """
    ratio = equity / causal_shift(equity, ROLLING_WINDOW)
    return _series((100.0 * (ratio - 1.0)).fillna(0.0))


def capture(
    *,
    portfolio: vbt.Portfolio,
    benchmark: vbt.Portfolio,
    data: MarketData,
    offset: int = 0,
) -> RunSeries:
    """Project a simulated portfolio into the series a chart needs.

    ``offset`` drops a leading warm-up prefix, matching
    :func:`cracktrade.backtest.metrics.extract_metrics`, so a chart covers exactly the bars the
    metrics beside it were computed from.
    """
    equity = portfolio.value().iloc[offset:]
    holdings = portfolio.assets().iloc[offset:]
    benchmark_equity = benchmark.value().iloc[offset:]
    close = data.frame[CLOSE].iloc[offset:]

    return RunSeries(
        equity=_series(equity),
        benchmark_equity=_series(benchmark_equity),
        drawdown=_series(_drawdown(equity).fillna(0.0)),
        close=_series(close),
        monthly_returns=_monthly(equity, holdings),
        rolling_12m_return=_rolling_annual(equity),
        filled=_filled_dates(data, offset),
    )


def _filled_dates(data: MarketData, offset: int) -> tuple[date, ...]:
    """Which bars in the captured window repeated the previous session rather than trading.

    ``None`` means the provider did not track provenance, which is not the same as "none were
    filled". An empty tuple is returned either way and the vintage block is where the two are
    told apart -- a chart cannot mark bars nobody recorded.
    """
    if data.filled is None:
        return ()
    marked = data.filled.iloc[offset:]
    index = marked.index[marked.to_numpy(dtype=bool)]
    assert isinstance(index, pd.DatetimeIndex)
    return _dates_of(index)
