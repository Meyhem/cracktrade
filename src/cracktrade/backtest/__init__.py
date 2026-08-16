"""The backtest engine: variant expansion, exit composition, and simulation.

vectorbt is imported only inside this package (see ``PLAN.md``), so the layers above it depend
on typed results rather than on a third-party portfolio object.
"""

from __future__ import annotations

from cracktrade.backtest.holding import holding_bounds
from cracktrade.backtest.portfolio import (
    FREQ,
    STOP_ENTRY_PRICE,
    STOP_EXIT_PRICE,
    YEAR_FREQ,
    metric,
    require_daily_bars,
)
from cracktrade.backtest.runner import VariantSimulation, run_variants
from cracktrade.backtest.stops import ATR_WINDOW, StopConfiguration, atr_stop_series, build_stops
from cracktrade.backtest.variants import VariantPair, expand_variants

__all__ = [
    "ATR_WINDOW",
    "FREQ",
    "STOP_ENTRY_PRICE",
    "STOP_EXIT_PRICE",
    "YEAR_FREQ",
    "StopConfiguration",
    "VariantPair",
    "VariantSimulation",
    "atr_stop_series",
    "build_stops",
    "expand_variants",
    "holding_bounds",
    "metric",
    "require_daily_bars",
    "run_variants",
]
