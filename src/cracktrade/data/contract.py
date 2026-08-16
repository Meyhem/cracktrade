"""The OHLCV frame contract.

Normative reference: ``docs/ENGINE_SPEC.md`` section 4.1. The engine consumes exactly one
shape; providers adapt to it, never the other way round. The contract is *checked*, not
assumed -- a provider that drifts fails loudly at the boundary instead of producing subtly
wrong trades a thousand lines later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

from cracktrade.errors import DataContractError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Required columns, in order.
OHLCV_COLUMNS: Final[tuple[str, ...]] = ("Open", "High", "Low", "Close", "Volume")

#: Price columns, i.e. everything except volume. Used for the internal-consistency check.
PRICE_COLUMNS: Final[tuple[str, ...]] = ("Open", "High", "Low", "Close")

#: The engine's numeric type. float32 (legacy) accumulates visible error on a compounding
#: equity curve and makes the causality harness's exact-equality assertions flaky for reasons
#: that have nothing to do with causality. Defect D14.
DTYPE: Final = np.float64


@dataclass(frozen=True, slots=True)
class MarketData:
    """A validated price history for one ticker.

    Attributes:
        ticker: the symbol this history belongs to.
        frame: the OHLCV frame, guaranteed to satisfy :func:`validate_frame`.
        requested_start: the ``start_date`` the strategy asked for.
        requested_end: the ``end_date`` the strategy asked for.
        filled: optional boolean series, aligned to ``frame``, True on bars that were
            forward-filled rather than observed. ``None`` means the provenance was not tracked.
    """

    ticker: str
    frame: pd.DataFrame
    requested_start: date
    requested_end: date
    filled: pd.Series | None = None

    def __post_init__(self) -> None:
        validate_frame(self.frame)
        if self.filled is not None and len(self.filled) != len(self.frame):
            msg = (
                f"filled mask has {len(self.filled)} entries for {len(self.frame)} bars; "
                f"they must be aligned"
            )
            raise DataContractError(msg)

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def filled_bars(self) -> int:
        """How many bars were forward-filled rather than observed."""
        return 0 if self.filled is None else int(self.filled.sum())

    @property
    def filled_fraction(self) -> float:
        """Share of bars that were forward-filled, in ``[0, 1]``."""
        if self.filled is None or self.frame.empty:
            return 0.0
        return self.filled_bars / len(self.frame)

    @property
    def index(self) -> pd.DatetimeIndex:
        """The bar timestamps."""
        index = self.frame.index
        assert isinstance(index, pd.DatetimeIndex)  # guaranteed by validate_frame
        return index

    @property
    def effective_start(self) -> date:
        """First bar actually available, which may be later than requested for a late IPO."""
        return self.index[0].date()

    @property
    def effective_end(self) -> date:
        """Last bar actually available."""
        return self.index[-1].date()

    def head(self, bars: int) -> MarketData:
        """The first ``bars`` bars, as a new :class:`MarketData`.

        Used by the truncation-equivalence harness and by the train/test splitter. The
        requested range is preserved so downstream reporting still knows what was asked for.
        """
        return self.slice(0, bars)

    def slice(self, start: int, stop: int) -> MarketData:
        """A positional slice, as a new :class:`MarketData`."""
        return MarketData(
            ticker=self.ticker,
            frame=self.frame.iloc[start:stop],
            requested_start=self.requested_start,
            requested_end=self.requested_end,
            filled=None if self.filled is None else self.filled.iloc[start:stop],
        )


def validate_frame(frame: pd.DataFrame) -> None:
    """Assert that ``frame`` satisfies the engine's OHLCV contract.

    Raises:
        DataContractError: naming the first violation found.
    """
    _check(isinstance(frame, pd.DataFrame), f"expected a DataFrame, got {type(frame).__name__}")
    _check(not frame.empty, "frame is empty")

    columns: Sequence[object] = list(frame.columns)
    _check(
        tuple(columns) == OHLCV_COLUMNS,
        f"columns must be exactly {list(OHLCV_COLUMNS)}, got {list(columns)}",
    )

    index = frame.index
    _check(
        isinstance(index, pd.DatetimeIndex),
        f"index must be a DatetimeIndex, got {type(index).__name__}",
    )
    assert isinstance(index, pd.DatetimeIndex)
    _check(index.tz is None, "index must be timezone-naive")
    _check(not index.hasnans, "index contains NaT")
    _check(index.is_unique, "index contains duplicate timestamps")
    _check(index.is_monotonic_increasing, "index must be sorted ascending")

    for column in OHLCV_COLUMNS:
        _check(
            frame[column].dtype == DTYPE,
            f"column {column!r} must be {DTYPE.__name__}, got {frame[column].dtype}",
        )

    nan_counts = frame.isna().sum()
    offenders = {name: int(count) for name, count in nan_counts.items() if count}
    _check(not offenders, f"frame contains NaN values: {offenders}")

    _check(bool((frame["Volume"] >= 0).all()), "Volume contains negative values")

    highest = frame[list(PRICE_COLUMNS)].max(axis=1)
    lowest = frame[list(PRICE_COLUMNS)].min(axis=1)
    _check(
        bool((frame["High"] >= highest).all()),
        "High is not the highest price on at least one bar",
    )
    _check(
        bool((frame["Low"] <= lowest).all()),
        "Low is not the lowest price on at least one bar",
    )


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise DataContractError(message)
