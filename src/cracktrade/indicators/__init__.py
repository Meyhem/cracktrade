"""Indicators: the registry, the catalogue, and namespace construction.

Importing this package installs the catalogue and registers its semantic validator with
:mod:`cracktrade.strategy`, so a strategy loaded through the façade is checked against the
registry without the façade having to know the registry exists.
"""

from __future__ import annotations

from cracktrade.indicators import registry
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.compute import IndicatorNamespace, compute_indicators
from cracktrade.indicators.spec import RAW_SERIES, Causality, IndicatorSpec
from cracktrade.indicators.validation import validate_indicators
from cracktrade.strategy import register_validator

install()
register_validator(validate_indicators)

__all__ = [
    "RAW_SERIES",
    "Causality",
    "IndicatorNamespace",
    "IndicatorSpec",
    "compute_indicators",
    "install",
    "registry",
    "validate_indicators",
]
