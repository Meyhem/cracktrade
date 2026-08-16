"""The backtest engine: holding rules, simulation, and metrics.

vectorbt is imported only inside this package (see ``PLAN.md``), so the layers above it depend
on the frozen value objects in :mod:`cracktrade.domain` rather than on a third-party portfolio
object. :func:`run_backtest` is the boundary.
"""

from __future__ import annotations

from cracktrade.backtest.benchmark import buy_and_hold_portfolio, compare
from cracktrade.backtest.holding import holding_bounds
from cracktrade.backtest.metrics import (
    extract_metrics,
    extract_trades,
    per_period_risk_free,
    worst_rolling_12m,
    yearly_returns,
)
from cracktrade.backtest.portfolio import (
    FREQ,
    STOP_ENTRY_PRICE,
    STOP_EXIT_PRICE,
    YEAR_FREQ,
    metric,
    require_daily_bars,
)
from cracktrade.backtest.results import frame_digest, run_backtest, vintage_of
from cracktrade.backtest.runner import Simulation, run_simulation
from cracktrade.backtest.stops import ATR_WINDOW, StopConfiguration, atr_stop_series, build_stops

__all__ = [
    "ATR_WINDOW",
    "FREQ",
    "STOP_ENTRY_PRICE",
    "STOP_EXIT_PRICE",
    "YEAR_FREQ",
    "Simulation",
    "StopConfiguration",
    "atr_stop_series",
    "build_stops",
    "buy_and_hold_portfolio",
    "compare",
    "extract_metrics",
    "extract_trades",
    "frame_digest",
    "holding_bounds",
    "metric",
    "per_period_risk_free",
    "require_daily_bars",
    "run_backtest",
    "run_simulation",
    "vintage_of",
    "worst_rolling_12m",
    "yearly_returns",
]
