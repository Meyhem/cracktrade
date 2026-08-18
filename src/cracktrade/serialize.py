"""Turning result models into JSON- and YAML-safe structures.

Normative reference: ``docs/ENGINE_SPEC.md`` section 11.

This lives in the library rather than the CLI because it is not a rendering concern. The CLI is
one consumer; the planned HTTP interface will serialise the same objects and must produce the
same shape, so the mapping belongs next to the models it maps.

Two things need care:

* **Derived properties carry the verdict.** ``is_credible``, ``failures`` and ``fold_win_rate``
  are computed, not stored, and a naive field walk would drop exactly the parts a consumer most
  needs. Each type declares which of its properties are part of its serialised form.
* **JSON has no infinity.** ``profit_factor`` is legitimately ``inf`` for a strategy with no
  losing trades, and ``float('inf')`` is not valid JSON -- most parsers reject it and some
  silently produce ``Infinity``, which is not portable. Non-finite floats become ``null``.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date
from enum import Enum
from typing import Any

import yaml

#: Properties to include alongside the stored fields, keyed by class name.
#:
#: Declared explicitly rather than by walking every property, because some are expensive, some
#: are internal, and a consumer should not have to guess which of them are contractual.
_EXPOSED_PROPERTIES: dict[str, tuple[str, ...]] = {
    "Metrics": ("has_enough_trades_to_judge",),
    "Trade": ("is_winner",),
    "BenchmarkComparison": ("beats_buy_and_hold",),
    "DataVintage": ("filled_pct",),
    "ParameterChange": ("moved", "at_bound"),
    "OptimizationResult": (
        "improvement_pct",
        "overfitting_gap_pct",
        "parameters_at_bound",
    ),
    "FoldResult": ("was_profitable",),
    "SegmentResult": ("was_profitable",),
    "EvolutionResult": (
        "checks",
        "profitable_segments",
        "median_segment_return_pct",
        "overfitting_gap_pct",
        "failures",
        "is_credible",
    ),
    "DeflatedSharpe": ("is_significant", "beats_the_lucky_threshold"),
    "OverfittingProbability": ("is_acceptable", "is_computed"),
    "StabilityReport": (
        "worst_degradation",
        "worst_small_degradation",
        "is_stable",
        "fragile_parameters",
    ),
    "CostSensitivity": ("survives_double_costs", "break_even_multiple"),
    "Interval": ("excludes_zero",),
    "Check": (),
    "ValidationReport": (
        "checks",
        "combined_return_pct",
        "beats_buy_and_hold",
        "returns",
        "profitable_folds",
        "fold_win_rate",
        "median_return_pct",
        "return_iqr_pct",
        "total_trades",
        "failures",
        "is_credible",
    ),
}


def to_dict(value: Any) -> Any:
    """Recursively convert a result model into plain Python types.

    Dataclasses become mappings of their fields plus their declared properties; enums become
    their values; dates become ISO strings; tuples become lists; non-finite floats become
    ``None``.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result = {
            field.name: to_dict(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
        for name in _EXPOSED_PROPERTIES.get(type(value).__name__, ()):
            result[name] = to_dict(getattr(value, name))
        return result

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_dict(item) for item in value]
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, float):
        # JSON has no representation for infinity or NaN. An infinite profit factor is a real
        # result (no losing trades), so it is reported as null rather than dropped or clamped.
        return value if math.isfinite(value) else None
    return str(value)


def to_json(value: Any, *, indent: int = 2) -> str:
    """Serialise a result model as JSON."""
    return json.dumps(to_dict(value), indent=indent, sort_keys=False)


def to_yaml(value: Any) -> str:
    """Serialise a result model as YAML."""
    return yaml.safe_dump(to_dict(value), sort_keys=False, default_flow_style=False)
