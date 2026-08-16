"""The indicator registry.

A process-wide catalogue mapping ``type:`` values to :class:`IndicatorSpec`. Registration is
the only way to add an indicator, and registration refuses anything that is not causal.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from cracktrade.errors import UnknownIndicatorError
from cracktrade.indicators.spec import Causality, IndicatorSpec

if TYPE_CHECKING:
    from collections.abc import Iterator

_REGISTRY: dict[str, IndicatorSpec] = {}


def register(spec: IndicatorSpec) -> IndicatorSpec:
    """Add ``spec`` to the registry.

    Raises:
        ValueError: the type is already registered, or the indicator is not causal. A
            non-causal indicator cannot be made safe by careful use, so it is refused at
            registration rather than guarded at every call site.
    """
    if spec.type in _REGISTRY:
        msg = f"indicator type {spec.type!r} is already registered"
        raise ValueError(msg)
    if spec.causality is not Causality.CAUSAL:
        msg = (
            f"refusing to register {spec.type!r}: it is {spec.causality.value}, and the engine "
            f"only computes values that depend on past bars"
        )
        raise ValueError(msg)
    _REGISTRY[spec.type] = spec
    return spec


def get(indicator_type: str) -> IndicatorSpec:
    """Look up an indicator by ``type``.

    Raises:
        UnknownIndicatorError: with a suggestion when the name looks like a typo.
    """
    key = indicator_type.strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]

    suggestions = difflib.get_close_matches(key, sorted(_REGISTRY), n=1, cutoff=0.7)
    if suggestions:
        msg = f"unknown indicator type {indicator_type!r} -- did you mean {suggestions[0]!r}?"
    else:
        msg = (
            f"unknown indicator type {indicator_type!r}; "
            f"run 'cracktrade indicators' to see the {len(_REGISTRY)} available types"
        )
    raise UnknownIndicatorError(msg)


def has(indicator_type: str) -> bool:
    """Whether ``type`` is registered."""
    return indicator_type.strip().lower() in _REGISTRY


def all_specs() -> Iterator[IndicatorSpec]:
    """Every registered indicator, in alphabetical order by type."""
    for key in sorted(_REGISTRY):
        yield _REGISTRY[key]


def count() -> int:
    """How many indicators are registered."""
    return len(_REGISTRY)
