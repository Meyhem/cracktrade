"""Finding the numbers in a strategy that the optimizer is allowed to move.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.1.

Discovery walks only ``indicators``, ``entry`` and ``exit``. ``strategy``, ``universe``,
``execution`` and ``position_sizing`` are never searched: optimizing the start date or the
commission rate would be fitting the *question*, not the answer.

``entry`` holds only ``signal`` and ``optimize``, both structural, so in practice it never
yields a parameter -- an entry rule is tuned through the indicators its signal references. It is
walked anyway so that adding a numeric field to :class:`~cracktrade.config.EntryRule` does not
silently create an unsearchable one.

**[FIX]** Legacy traversed the raw YAML dict, never constructing a validated model, which is how
its own tests got away with a dict-shaped ``indicators`` section the schema forbids. Here the
strategy is validated first and discovery runs over the validated model's dump, so a parameter
can only be found where a real field exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cracktrade.config import OptimizeBounds
from cracktrade.errors import NoOptimizableParametersError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from cracktrade.config import Strategy

#: Sections holding a list of entries whose numeric leaves are searchable.
LIST_SECTIONS = ("indicators",)

#: Sections holding a single mapping whose numeric leaves are searchable.
SINGLE_SECTIONS = ("entry", "exit")

#: Keys that are structural rather than tunable, so they are skipped even though some hold
#: numbers in principle.
_STRUCTURAL_KEYS = frozenset({"name", "type", "signal", "source", "optimize"})

#: Default search range, as a multiple of the configured value (spec 9.1).
_DEFAULT_SPREAD = 0.5


@dataclass(frozen=True, slots=True)
class Parameter:
    """One tunable number.

    Attributes:
        section: which section it came from, e.g. ``indicators`` or ``exit``.
        index: position within that section's list, or ``None`` for a single-mapping section.
        owner: the ``name`` of the list entry it belongs to, for reporting. ``None`` for a
            single-mapping section, which has no name to qualify the path with.
        key: the parameter's field name.
        value: the configured (baseline) value.
        is_integer: whether the YAML literal was an integer. A window of 20.4 is the same
            strategy as one of 20, so the distinction has to survive the search.
        low: lower search bound.
        high: upper search bound.
    """

    section: str
    index: int | None
    owner: str | None
    key: str
    value: float
    is_integer: bool
    low: float
    high: float

    @property
    def path(self) -> str:
        """Dotted path used in reports and parameter diffs."""
        if self.owner is None:
            return f"{self.section}.{self.key}"
        return f"{self.section}.{self.owner}.{self.key}"


def discover_parameters(strategy: Strategy) -> tuple[Parameter, ...]:
    """Every numeric leaf the optimizer may move, with its bounds.

    Raises:
        NoOptimizableParametersError: the strategy pins everything, so there is nothing to
            search and running the optimizer would only burn time re-scoring one configuration.
    """
    dump = strategy.model_dump(mode="python")
    found: list[Parameter] = []

    for section in LIST_SECTIONS:
        for index, entry in enumerate(dump.get(section) or ()):
            found.extend(_parameters_in(section, index, str(entry.get("name", index)), entry))

    for section in SINGLE_SECTIONS:
        found.extend(_parameters_in(section, None, None, dump[section]))

    if not found:
        msg = (
            "this strategy has no optimizable parameters: every numeric field is either pinned "
            "with 'optimize: false' or absent. Note that numbers written inside a signal "
            "expression are not reachable by the optimizer -- move them into an indicator to "
            "make them tunable"
        )
        raise NoOptimizableParametersError(msg)

    return tuple(found)


def _parameters_in(
    section: str, index: int | None, owner: str | None, entry: Mapping[str, Any]
) -> list[Parameter]:
    """Numeric leaves of one section entry, honouring its ``optimize`` override."""
    control = entry.get("optimize", True)
    if control is False:
        return []

    overrides: Mapping[str, Any] = control if isinstance(control, dict) else {}

    parameters: list[Parameter] = []
    for key, value in _numeric_leaves(entry):
        override = overrides.get(key, True)
        if override is False:
            continue
        low, high = _bounds(value, override)
        parameters.append(
            Parameter(
                section=section,
                index=index,
                owner=owner,
                key=key,
                value=float(value),
                is_integer=isinstance(value, int),
                low=low,
                high=high,
            )
        )
    return parameters


def _numeric_leaves(entry: Mapping[str, Any]) -> list[tuple[str, int | float]]:
    """Tunable numbers directly on an entry, and inside its ``params`` mapping.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in Python, and treating a flag
    as a continuous parameter would search values that have no meaning.
    """
    leaves: list[tuple[str, int | float]] = []
    for key, value in entry.items():
        if key in _STRUCTURAL_KEYS:
            continue
        if key == "params" and isinstance(value, dict):
            leaves.extend(
                (nested_key, nested)
                for nested_key, nested in value.items()
                if _is_tunable_number(nested)
            )
            continue
        if _is_tunable_number(value):
            leaves.append((key, value))
    return leaves


def _is_tunable_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _bounds(value: int | float, override: object) -> tuple[float, float]:
    """The search range for one parameter.

    An explicit ``{min, max}`` wins. Otherwise the default is plus or minus 50% of the
    configured value, written with ``min``/``max`` so that a negative baseline still yields an
    ordered pair. A baseline of zero has no meaningful proportional range, so it gets a small
    absolute one.
    """
    if isinstance(override, OptimizeBounds):
        return float(override.min), float(override.max)
    if isinstance(override, dict) and "min" in override and "max" in override:
        return float(override["min"]), float(override["max"])

    if value == 0:
        return -_DEFAULT_SPREAD, _DEFAULT_SPREAD

    lower = value * (1 - _DEFAULT_SPREAD)
    upper = value * (1 + _DEFAULT_SPREAD)
    return float(min(lower, upper)), float(max(lower, upper))


def inject(
    strategy: Strategy, parameters: Sequence[Parameter], values: Sequence[float]
) -> dict[str, Any]:
    """Return ``strategy``'s configuration with ``values`` substituted in.

    The source strategy is never mutated -- the optimizer evaluates thousands of candidates and
    a shared mutable config would make every one of them depend on the last.

    Integer parameters are rounded and floats are held to two decimals, matching the precision
    the configuration format actually carries. Without this the reported "optimal" window would
    print as 20.399999999999999.
    """
    config = strategy.model_dump(mode="python")

    for parameter, raw in zip(parameters, values, strict=True):
        section = config[parameter.section]
        entry = section if parameter.index is None else section[parameter.index]
        target = (
            entry["params"]
            if "params" in entry and parameter.key in entry.get("params", {})
            else entry
        )
        target[parameter.key] = round(raw) if parameter.is_integer else round(float(raw), 2)

    return config
