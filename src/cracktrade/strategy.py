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

from collections.abc import Mapping
from dataclasses import dataclass
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


def load_strategy(path: Path) -> Strategy:
    """Read, validate, and return the strategy at ``path``.

    Raises:
        ConfigError: the file cannot be read or is not YAML.
        StrategyValidationError: the strategy is structurally or semantically invalid.
    """
    return _apply_semantic_checks(read_strategy_file(path))


def build_strategy(data: Mapping[str, Any], *, source: str | None = None) -> Strategy:
    """Validate an in-memory mapping. The API interface's entry point.

    Args:
        data: the parsed mapping.
        source: the YAML text ``data`` was parsed from, when there is one. Structural errors
            then carry the line that caused them (spec section 3.9), which is what lets an
            editor put a marker in the gutter rather than a message in a banner. Semantic
            errors have no line: they are about the strategy as a whole, not about one key.
    """
    return _apply_semantic_checks(parse_strategy(data, source=source))


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


@dataclass(frozen=True, slots=True)
class ConfigWarning:
    """A problem worth telling the user about that does not make the config invalid.

    Spec section 3.7: a shadowed stop is configured, accepted, and has no effect. Refusing the
    file would be wrong -- it is a valid strategy -- but so would silence, because the user
    wrote a stop and will assume it fires.

    Structured rather than logged, because two interfaces need to place it: the CLI prints it
    beside the run, and the editor attaches it to the field that caused it.
    """

    path: str
    message: str


def strategy_warnings(strategy: Strategy) -> tuple[ConfigWarning, ...]:
    """Everything accepted-but-suspect about a valid strategy.

    Warnings never block. A caller that ignores them still gets the run the user asked for,
    which is the difference between this and :class:`StrategyValidationError`.
    """
    warnings: list[ConfigWarning] = []

    warnings.extend(_history_reach_warnings(strategy))

    active = strategy.exit.active_stop
    for field_name in strategy.exit.shadowed_stops:
        warnings.append(
            ConfigWarning(
                path=f"exit.{field_name}",
                message=(
                    f"{field_name} is set but has no effect: the stop priority chain is won by "
                    f"{_ACTIVE_STOP_FIELD[active]} (spec section 3.7). Remove it, or drop the "
                    f"stop that outranks it."
                ),
            )
        )

    for indicator in strategy.indicators:
        if indicator.source is not None and not _uses_source(indicator.type):
            warnings.append(
                ConfigWarning(
                    path=f"indicators.{indicator.name}.source",
                    message=(
                        f"source has no effect on a {indicator.type} indicator: it always "
                        f"receives the series it needs regardless of this setting."
                    ),
                )
            )

    return tuple(warnings)


def _history_reach_warnings(strategy: Strategy) -> list[ConfigWarning]:
    """Warn when an intraday range starts further back than the provider still serves.

    A warning rather than a validation error, because the file is not wrong -- it has aged.
    The provider's window slides forward every day, so a range that was fetchable when it was
    written stops being fetchable without anything about the strategy changing. Refusing to
    *parse* it would mean a stored strategy became unreadable on a timer, and would take the
    detail view of every run it ever produced with it.

    The width of the range is checked at parse time instead (:class:`Universe`), and a run
    launched against unreachable data is refused loudly by the data layer. This warning exists
    so the editor can say so before the user waits for that.
    """
    from datetime import UTC, datetime

    universe = strategy.universe
    limit = universe.interval.max_lookback
    if limit is None:
        return []

    earliest = datetime.now(UTC).date() - limit
    if universe.start_date >= earliest:
        return []

    return [
        ConfigWarning(
            path="universe.start_date",
            message=(
                f"{universe.start_date} is further back than {universe.interval.value} bars are "
                f"still available: the provider serves roughly the last {limit.days} days, so "
                f"the earliest usable start is around {earliest}. Move the range forward, or "
                f"switch to 1h (about two years) or 1d (no limit)."
            ),
        )
    ]


#: Which YAML field each ``active_stop`` value corresponds to.
_ACTIVE_STOP_FIELD: Mapping[str | None, str] = {
    "atr": "atr_stop_multiplier",
    "trailing": "trailing_stop_pct",
    "fixed": "stop_loss_pct",
    None: "no stop",
}


def _uses_source(indicator_type: str) -> bool:
    import importlib

    registry = importlib.import_module("cracktrade.indicators.registry")
    importlib.import_module("cracktrade.indicators.compute").registry_installed()
    return bool(registry.get(indicator_type).uses_source)
