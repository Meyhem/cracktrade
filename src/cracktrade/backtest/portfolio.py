"""The vectorbt invocation. The only module in the engine that imports vectorbt.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7.5.

Every parameter that affects the result is passed explicitly. Legacy passed nine arguments and
inherited the rest, which made results silently dependent on the installed library version:
a vectorbt release that changed a default would have changed every backtest with no diff and no
warning. The defaults are pinned here so that a version bump breaks a test instead.

The three conventions of section 2.6 were established empirically against vectorbt 1.0.0 rather
than read from documentation, and are asserted by tests in ``tests/test_backtest.py``:

* with ``stop_exit_price='stoplimit'``, a bar that touches both the stop-loss and the
  take-profit exits at the **stop-loss** -- the pessimistic reading, since a daily bar does not
  record which came first;
* a bar that gaps straight through the stop fills at the **actual open**, not at the
  unreachable stop level;
* an ``sl_stop`` of ``0.0`` closes the position immediately, while ``NaN`` means no stop at all
  (defect D7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd
import vectorbt as vbt

from cracktrade.backtest.holding import (
    as_signal_column,
    holding_signal_nb,
    holding_state,
)
from cracktrade.config import PositionSizingType
from cracktrade.errors import BacktestError

if TYPE_CHECKING:
    import numpy.typing as npt

    from cracktrade.backtest.stops import StopConfiguration
    from cracktrade.config import ExecutionConfig, PositionSizing
    from cracktrade.data import MarketData

#: Integer bar positions at which positions were opened.
type BarPositions = npt.NDArray[np.int64]

#: Fill convention for stop orders. Verified: fills at the stop level when the bar reaches it,
#: and at the open when the bar gaps past it.
STOP_EXIT_PRICE: Final = "stoplimit"

#: Where a stop distance is measured from. vectorbt defaults to ``'close'`` -- the close of the
#: entry bar -- but the engine fills at that bar's *open*, so a "5% stop" would not be 5% below
#: what was actually paid. ``'fillprice'`` measures from the executed price, slippage included,
#: which is what the configuration means.
STOP_ENTRY_PRICE: Final = "fillprice"

#: Bar frequency. Daily only -- see :func:`require_daily_bars`.
FREQ: Final = "1D"

#: Trading days per year, for annualising. Passed at every metric call site rather than through
#: ``vbt.settings``, which is process-global state a library must not mutate.
YEAR_FREQ: Final = "252 days"

#: How each sizing type maps onto vectorbt. Spec section 3.8.
_SIZING: Final[dict[PositionSizingType, str]] = {
    PositionSizingType.FIXED_PCT: "percent",
    PositionSizingType.FIXED_CASH: "value",
    PositionSizingType.FIXED_SHARES: "amount",
}


def require_daily_bars(data: MarketData) -> None:
    """Reject any history that is not daily bars.

    Annualisation assumes 252 bars per year. An intraday or weekly series would be silently
    mis-annualised by a factor of several, so the frequency is checked rather than assumed
    (spec section 7.5).

    Real daily data is not evenly spaced -- weekends and holidays leave gaps -- so the test is
    on the *median* spacing rather than on an inferred frequency, which is ``None`` for any
    genuine market history.

    Raises:
        BacktestError: the bars are not daily.
    """
    if len(data) < 3:
        return

    gaps = np.diff(data.index.to_numpy()).astype("timedelta64[h]").astype(np.float64)
    median_gap = float(np.median(gaps))

    if median_gap < 24.0:
        msg = (
            f"{data.ticker} has a median bar spacing of {median_gap:.1f} hours, which is "
            f"intraday. This engine annualises on 252 daily bars and would overstate every "
            f"time-based metric on intraday data"
        )
        raise BacktestError(msg)

    if median_gap > 24.0 * 4:
        msg = (
            f"{data.ticker} has a median bar spacing of {median_gap / 24:.1f} days, which is "
            f"not daily. This engine annualises on 252 daily bars"
        )
        raise BacktestError(msg)


def simulate(
    data: MarketData,
    entries: pd.Series,
    exits: pd.Series,
    stops: StopConfiguration,
    execution: ExecutionConfig,
    sizing: PositionSizing | None,
    *,
    holding: tuple[int, int] = (0, 0),
    seed: int,
) -> vbt.Portfolio:
    """Run one simulation with every result-affecting parameter pinned.

    ``entries`` and ``exits`` are already aligned for next-open execution by the signal layer,
    and ``price=Open`` makes the fill happen there.

    The signals are delivered through ``signal_func_nb`` rather than as plain arrays, so that
    the holding-period rules can be applied against realised positions during the run (spec
    section 7.2). A side effect worth noting: the signal function returns one decision per bar,
    so an entry and an exit can never collide and vectorbt's conflict resolution is never
    reached.
    """
    frame = data.frame
    size, size_type = _resolve_size(sizing)
    minimum, maximum = holding

    return vbt.Portfolio.from_signals(
        close=frame["Close"],
        signal_func_nb=holding_signal_nb,
        signal_args=(
            as_signal_column(entries),
            as_signal_column(exits),
            holding_state(),
            minimum,
            maximum,
        ),
        # Fill at the next open. The signal layer has already shifted the decision forward one
        # bar, so the value read here belongs to the bar after the condition was observed.
        price=frame["Open"],
        open=frame["Open"],
        high=frame["High"],
        low=frame["Low"],
        init_cash=execution.initial_capital,
        size=size,
        size_type=size_type,
        fees=execution.commission_fraction,
        slippage=execution.slippage_fraction,
        sl_stop=stops.sl_stop,
        sl_trail=stops.sl_trail,
        tp_stop=stops.tp_stop,
        stop_entry_price=STOP_ENTRY_PRICE,
        stop_exit_price=STOP_EXIT_PRICE,
        upon_stop_exit="close",
        use_stops=True,
        # Pinned defaults. Each of these is what vectorbt happens to default to today; stating
        # them means a library change breaks a test rather than a backtest.
        direction="longonly",
        accumulate=False,
        cash_sharing=False,
        size_granularity=None,
        freq=FREQ,
        seed=seed,
    )


def entry_positions(portfolio: vbt.Portfolio) -> BarPositions:
    """The integer bar positions at which positions were actually opened.

    This is what the holding constraints key off, rather than the entry *signals* -- a signal
    that fires while already in a position opens nothing (defect D8).
    """
    records = portfolio.trades.records
    if records.empty:
        return np.zeros(0, dtype=np.int64)
    opened: BarPositions = records["entry_idx"].to_numpy().astype(np.int64)
    return opened


def _resolve_size(sizing: PositionSizing | None) -> tuple[float, str]:
    """Map the strategy's position sizing onto vectorbt's ``size`` / ``size_type``.

    Omitted sizing means all available cash, which vectorbt spells as an infinite share count.
    """
    if sizing is None:
        return np.inf, "amount"

    size_type = _SIZING.get(sizing.type)
    if size_type is None:  # pragma: no cover - the enum is closed
        msg = f"unsupported position sizing type {sizing.type!r}"
        raise BacktestError(msg)

    value = sizing.value / 100.0 if sizing.type is PositionSizingType.FIXED_PCT else sizing.value
    return value, size_type


def metric(portfolio: vbt.Portfolio, name: str, **kwargs: Any) -> Any:
    """Call a vectorbt metric with the trading-day calendar pinned.

    ``year_freq`` is *not* a ``from_signals`` parameter in vectorbt 1.0.0 -- it is a per-call
    argument on the metric methods, defaulting to a 365-day year. Left alone it would inflate
    every annualised figure: Sharpe by a factor of sqrt(365/252), about 1.20 (audit finding A2).
    """
    return getattr(portfolio, name)(year_freq=YEAR_FREQ, **kwargs)
