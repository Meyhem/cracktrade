"""Strategy configuration: schema, parsing, and serialisation.

This package performs *structural* validation only -- shape, types, ranges, uniqueness. It
deliberately knows nothing about the indicator registry or the signal grammar, because those
layers import this one and the dependency must not be circular. Semantic validation (does this
indicator type exist, does this expression parse, does every name resolve) is composed on top
by :mod:`cracktrade.strategy`.
"""

from __future__ import annotations

from cracktrade.config.loader import dump_strategy, parse_strategy, read_strategy_file
from cracktrade.config.models import (
    PRICE_SERIES_NAMES,
    EntryVariant,
    ExecutionConfig,
    ExitVariant,
    IndicatorConfig,
    OptimizeBounds,
    PositionSizing,
    PositionSizingType,
    PriceSeries,
    Strategy,
    StrategyMeta,
    Universe,
)

__all__ = [
    "PRICE_SERIES_NAMES",
    "EntryVariant",
    "ExecutionConfig",
    "ExitVariant",
    "IndicatorConfig",
    "OptimizeBounds",
    "PositionSizing",
    "PositionSizingType",
    "PriceSeries",
    "Strategy",
    "StrategyMeta",
    "Universe",
    "dump_strategy",
    "parse_strategy",
    "read_strategy_file",
]
