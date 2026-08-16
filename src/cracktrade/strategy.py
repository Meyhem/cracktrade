"""The strategy façade: structural validation plus semantic checks.

:mod:`cracktrade.config` validates *shape*. It cannot validate meaning, because deciding
whether ``type: sma`` exists or whether ``(close > sma_long)`` resolves requires the indicator
registry and the signal grammar -- layers that import ``config`` and so cannot be imported by
it.

This module sits above all three and composes them. It is the entry point interfaces should
use: a strategy that loads here is valid in every sense the engine cares about, checked before
a single byte of market data is downloaded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from cracktrade.config import Strategy, parse_strategy, read_strategy_file
from cracktrade.errors import StrategyValidationError


class SemanticValidator(Protocol):
    """A check that needs more context than the schema has.

    Returns a list of human-readable problems; an empty list means the check passed. Validators
    report *all* the problems they find rather than raising on the first, so a user sees the
    whole picture in one pass.
    """

    def __call__(self, strategy: Strategy) -> list[str]: ...


#: Registered in dependency order by the layers that own them. Populated by
#: :mod:`cracktrade.indicators` and :mod:`cracktrade.signals` at import time.
_VALIDATORS: list[SemanticValidator] = []


def register_validator(validator: SemanticValidator) -> None:
    """Add a semantic check to the validation pass."""
    if validator not in _VALIDATORS:
        _VALIDATORS.append(validator)


def registered_validators() -> Sequence[SemanticValidator]:
    """The active semantic checks, in the order they run."""
    return tuple(_VALIDATORS)


def load_strategy(path: Path) -> Strategy:
    """Read, validate, and return the strategy at ``path``.

    Raises:
        ConfigError: the file cannot be read or is not YAML.
        StrategyValidationError: the strategy is structurally or semantically invalid.
    """
    return _apply_semantic_checks(read_strategy_file(path))


def build_strategy(data: Mapping[str, Any]) -> Strategy:
    """Validate an in-memory mapping. The API interface's entry point."""
    return _apply_semantic_checks(parse_strategy(data))


def required_warmup(strategy: Strategy) -> int:
    """Bars the strategy needs before any of its indicators is defined.

    Declared warm-up, taken as the maximum across the indicator list. Callers pass this plus a
    margin to :func:`cracktrade.data.load_history`, so a 200-day moving average over a 60-day
    range is refused at the data layer rather than producing a backtest that is entirely
    warm-up (defect D11).
    """
    import importlib

    registry = importlib.import_module("cracktrade.indicators.registry")
    importlib.import_module("cracktrade.indicators.compute").registry_installed()

    warmup = 0
    for indicator in strategy.indicators:
        spec = registry.get(indicator.type)
        warmup = max(warmup, spec.warmup(spec.params.model_validate(indicator.params)))
    return warmup


def _ensure_validators_registered() -> None:
    """Import the layers that own semantic checks, so they register themselves.

    Imported here rather than at module scope because those layers import this module: the
    dependency runs downward at import time and upward only at call time.
    """
    import importlib

    importlib.import_module("cracktrade.indicators")
    importlib.import_module("cracktrade.signals")


def _apply_semantic_checks(strategy: Strategy) -> Strategy:
    _ensure_validators_registered()
    issues: list[str] = []
    for validator in _VALIDATORS:
        issues.extend(validator(strategy))
    if issues:
        raise StrategyValidationError(issues)
    return strategy
