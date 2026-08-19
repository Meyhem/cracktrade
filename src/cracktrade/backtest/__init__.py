"""The backtest engine: holding rules, simulation, and metrics.

vectorbt is imported only inside this package (spec section 1.1), so the layers above it depend
on the frozen value objects in :mod:`cracktrade.domain` rather than on a third-party portfolio
object. :func:`run_backtest` is the boundary.
"""

from __future__ import annotations

from cracktrade.backtest.benchmark import buy_and_hold_portfolio, compare
from cracktrade.backtest.calendar import DAILY, Calendar
from cracktrade.backtest.holding import holding_bounds
from cracktrade.backtest.metrics import (
    extract_metrics,
    extract_trades,
    per_period_risk_free,
    worst_rolling_12m,
    yearly_returns,
)
from cracktrade.backtest.portfolio import (
    STOP_ENTRY_PRICE,
    STOP_EXIT_PRICE,
    metric,
    require_interval,
)
from cracktrade.backtest.results import frame_digest, run_backtest, vintage_of
from cracktrade.backtest.runner import Simulation, overnight_carries, run_simulation
from cracktrade.backtest.sessionclose import SessionRules, session_rules
from cracktrade.backtest.stops import ATR_WINDOW, StopConfiguration, atr_stop_series, build_stops

__all__ = [
    "ATR_WINDOW",
    "DAILY",
    "STOP_ENTRY_PRICE",
    "STOP_EXIT_PRICE",
    "Calendar",
    "SessionRules",
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
    "overnight_carries",
    "per_period_risk_free",
    "require_interval",
    "run_backtest",
    "run_simulation",
    "session_rules",
    "vintage_of",
    "worst_rolling_12m",
    "yearly_returns",
]
