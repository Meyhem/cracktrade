"""Loading, validating, and serialising strategy files.

Validation errors are aggregated rather than raised one at a time (spec section 3.9), and each
is annotated with the YAML line it came from plus a suggestion when a key looks like a typo of
a real one. A user fixing a strategy file should need one round trip, not five.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from cracktrade.config.models import (
    EntryVariant,
    ExecutionConfig,
    ExitVariant,
    IndicatorConfig,
    PositionSizing,
    Strategy,
    StrategyMeta,
    Universe,
)
from cracktrade.errors import ConfigError, StrategyValidationError

#: Maps a top-level section name to the model describing one of its entries, so an error deep
#: in the tree can be turned into "here are the keys this section actually accepts".
_SECTION_MODELS: Mapping[str, type[BaseModel]] = {
    "strategy": StrategyMeta,
    "universe": Universe,
    "execution": ExecutionConfig,
    "indicators": IndicatorConfig,
    "entry_variants": EntryVariant,
    "exit_variants": ExitVariant,
    "position_sizing": PositionSizing,
}

#: Pydantic prefixes messages raised from custom validators. Users should not see the prefix.
_VALUE_ERROR_PREFIX = "Value error, "


def read_strategy_file(path: Path) -> Strategy:
    """Read and validate a strategy from a YAML file.

    Raises:
        ConfigError: the file is missing, unreadable, or is not a YAML mapping.
        StrategyValidationError: the contents failed validation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        msg = f"cannot read strategy file {path}: {error}"
        raise ConfigError(msg) from error

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        msg = f"{path} is not valid YAML: {error}"
        raise ConfigError(msg) from error

    if data is None:
        msg = f"{path} is empty"
        raise ConfigError(msg)
    if not isinstance(data, Mapping):
        msg = f"{path} must contain a mapping of sections, found {type(data).__name__}"
        raise ConfigError(msg)

    return parse_strategy(data, source=text)


def parse_strategy(data: Mapping[str, Any], *, source: str | None = None) -> Strategy:
    """Validate a mapping into a :class:`Strategy`.

    Args:
        data: the parsed YAML mapping.
        source: the original YAML text. When given, errors carry line numbers.

    Raises:
        StrategyValidationError: with one message per problem found.
    """
    try:
        return Strategy.model_validate(dict(data))
    except ValidationError as error:
        raise StrategyValidationError(_format_errors(error, source)) from error


def dump_strategy(strategy: Strategy) -> str:
    """Serialise a strategy back to YAML.

    The output round-trips: type-specific indicator parameters are flattened back out of
    ``params``, defaults that were not set stay absent, and section order is preserved.
    """
    payload = strategy.model_dump(mode="json", exclude_none=True)
    payload["indicators"] = [_flatten_indicator(item) for item in payload.get("indicators", [])]
    if not payload["indicators"]:
        del payload["indicators"]
    for section in ("indicators", "entry_variants", "exit_variants"):
        for item in payload.get(section, []):
            if item.get("optimize") is True:
                del item["optimize"]
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _flatten_indicator(item: dict[str, Any]) -> dict[str, Any]:
    """Undo the ``params`` collection so the YAML looks the way the user wrote it."""
    params = item.pop("params", {})
    return {**item, **params}


# --------------------------------------------------------------------- error rendering


def _format_errors(error: ValidationError, source: str | None) -> list[str]:
    """Turn pydantic's error list into user-facing messages."""
    node = _compose(source) if source else None
    messages: list[str] = []
    for raw in _without_cascades(error.errors()):
        loc = raw["loc"]
        path = ".".join(str(part) for part in loc)
        message = _message_for(raw)
        line = _line_of(node, loc) if node is not None else None
        location = f"{path} (line {line})" if line is not None else path
        messages.append(f"{location}: {message}")
    return messages


def _without_cascades(raws: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Drop errors that are only consequences of another error already reported.

    A single invalid entry in ``exit_variants`` also empties the list, producing a "needs at
    least one entry" error alongside the real one. Reporting both invites the user to fix the
    wrong thing.
    """
    sections_with_entry_errors = {
        str(raw["loc"][0]) for raw in raws if len(raw["loc"]) > 1 and raw["loc"][0]
    }
    return [
        raw
        for raw in raws
        if not (
            raw["type"] == "too_short"
            and len(raw["loc"]) == 1
            and str(raw["loc"][0]) in sections_with_entry_errors
        )
    ]


def _message_for(raw: Mapping[str, Any]) -> str:
    """Render one pydantic error, adding a suggestion for near-miss key names."""
    kind = str(raw["type"])
    if kind == "missing":
        return "this field is required but is missing"
    if kind == "extra_forbidden":
        return _unknown_key_message(raw["loc"])

    message = str(raw["msg"])
    if message.startswith(_VALUE_ERROR_PREFIX):
        message = message[len(_VALUE_ERROR_PREFIX) :]
    return _despecialise(message)


def _despecialise(message: str) -> str:
    """Replace pydantic wording that leaks our internal representation.

    Sections are stored as tuples for immutability. Users wrote a YAML list and should be told
    about a list.
    """
    replacements = {
        "Input should be a valid tuple": "must be a list of entries",
        "Tuple should have at least": "must have at least",
        "Input should be a valid list": "must be a list of entries",
    }
    for pattern, replacement in replacements.items():
        if message.startswith(pattern):
            return replacement + message[len(pattern) :]
    return message


def _unknown_key_message(loc: Sequence[str | int]) -> str:
    """Explain an unrecognised key, suggesting a valid one when it looks like a typo."""
    key = str(loc[-1])
    model = _model_for(loc)
    if model is None:
        return f"unknown field {key!r}"

    valid = sorted(model.model_fields)
    suggestions = difflib.get_close_matches(key, valid, n=1, cutoff=0.7)
    if suggestions:
        return f"unknown field {key!r} -- did you mean {suggestions[0]!r}?"
    return f"unknown field {key!r}; accepted here: {', '.join(valid)}"


def _model_for(loc: Sequence[str | int]) -> type[BaseModel] | None:
    """Resolve the model that owns the field at ``loc``."""
    if len(loc) <= 1:
        return Strategy
    section = str(loc[0])
    return _SECTION_MODELS.get(section)


def _compose(source: str) -> yaml.Node | None:
    """Parse YAML into a node tree that retains source positions."""
    try:
        composed: yaml.Node | None = yaml.compose(source)
    except yaml.YAMLError:
        return None
    return composed


def _line_of(node: yaml.Node | None, loc: Sequence[str | int]) -> int | None:
    """Find the 1-based line of the value at ``loc`` in the composed YAML tree.

    Falls back to the deepest node reached, so an error on a key that does not exist in the
    source still points at the section containing it.
    """
    if node is None:
        return None

    current = node
    for part in loc:
        # `params` is a synthetic level introduced when collecting type-specific indicator
        # parameters; the YAML has those keys inline on the indicator itself.
        if part == "params":
            continue
        found = _descend(current, part)
        if found is None:
            break
        current = found
    return current.start_mark.line + 1


def _descend(node: yaml.Node, part: str | int) -> yaml.Node | None:
    """Take one step into a composed YAML node."""
    if isinstance(node, yaml.MappingNode) and isinstance(part, str):
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == part:
                chosen: yaml.Node = key_node if _is_leaf(value_node) else value_node
                return chosen
        return None
    if isinstance(node, yaml.SequenceNode) and isinstance(part, int):
        if 0 <= part < len(node.value):
            entry: yaml.Node = node.value[part]
            return entry
        return None
    return None


def _is_leaf(node: yaml.Node) -> bool:
    """Whether a node holds a scalar, in which case the key's line is the useful one."""
    return isinstance(node, yaml.ScalarNode)
