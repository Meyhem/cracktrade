"""Synthetic price data for tests.

Deterministic by construction -- no randomness without an explicit seed -- so a failure is
always reproducible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    bars: int = 300,
    *,
    start: str = "2020-01-01",
    first_close: float = 100.0,
    trend: float = 0.05,
    wobble: float = 2.0,
    seed: int = 0,
) -> pd.DataFrame:
    """Build a well-formed OHLCV frame.

    The series trends upward with a deterministic sinusoidal wobble, which gives moving
    averages something to cross and stops indicators from degenerating on a straight line.

    Args:
        bars: number of rows.
        start: first date, business-day frequency thereafter.
        first_close: closing price of the first bar.
        trend: added to the close each bar.
        wobble: amplitude of the oscillation around the trend.
        seed: seeds the volume series.
    """
    index = pd.date_range(start=start, periods=bars, freq="B", name="Date")
    steps = np.arange(bars, dtype=np.float64)
    close = first_close + trend * steps + wobble * np.sin(steps / 7.0)

    # Open trails the previous close slightly; high/low bracket both so the OHLC relationships
    # in the frame contract hold on every bar.
    open_ = np.concatenate([[first_close], close[:-1]])
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = 1_000_000 + (np.arange(bars) * 37 + seed * 101) % 250_000

    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume.astype(np.float64),
        },
        index=index,
    )
