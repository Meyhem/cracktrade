"""Validating a configuration without storing or running anything.

Behind the editor. Every keystroke can ask this what is wrong, what would be searched, and
which names a signal may refer to -- all before a byte of market data is fetched, which is what
spec section 3.6 already required of the engine.

Invalidity is an *answer* here, not a transport failure: the endpoint returns 200 with
``valid: false``. A 4xx would be right if the request were malformed, but a config the user is
halfway through typing is exactly what this is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from cracktrade.api.errors import FieldIssue
from cracktrade.config import Strategy, dump_strategy, parse_strategy
from cracktrade.errors import ConfigError, CracktradeError, StrategyValidationError
from cracktrade.indicators import RAW_SERIES
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import discover_parameters
from cracktrade.strategy import build_strategy, strategy_warnings

#: Separator the engine uses when rendering "<dotted.path>: <message>" (spec section 3.9).
_PATH_SEPARATOR = ": "


@dataclass(frozen=True, slots=True)
class SearchableParameter:
    """One numeric parameter the optimizer could move, and the range it may move within."""

    path: str
    value: float
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class ConfigReview:
    """What validation found. ``valid`` false is a normal outcome, not an error."""

    valid: bool
    errors: tuple[FieldIssue, ...] = ()
    warnings: tuple[FieldIssue, ...] = ()
    canonical_yaml: str | None = None
    config: dict[str, Any] | None = None
    namespace: tuple[str, ...] = ()
    searchable: tuple[SearchableParameter, ...] = ()


def field_issue(text: str, line: int | None = None) -> FieldIssue:
    """Split an engine issue into the field it belongs to and the message about it.

    The engine renders these as ``path: message`` (spec section 3.9). Splitting on the first
    separator is what lets the editor put an error on the field that caused it rather than in a
    banner above the form. A message with no path is kept whole rather than guessed at.
    """
    path, separator, message = text.partition(_PATH_SEPARATOR)
    if not separator or " " in path:
        return FieldIssue(path="", message=text, line=line)
    return FieldIssue(path=path, message=message, line=line)


def _namespace(strategy: Strategy) -> tuple[str, ...]:
    """Every name a signal expression may reference: raw series plus indicator outputs.

    The editor shows these as chips beside the signal field. Taken from the registry rather
    than assembled by hand, so a multi-output indicator contributes all of its names.
    """
    from cracktrade.indicators import registry

    names = list(RAW_SERIES)
    for indicator in strategy.indicators:
        names.extend(registry.get(indicator.type).output_names(indicator.name))
    return tuple(names)


def _searchable(strategy: Strategy) -> tuple[SearchableParameter, ...]:
    """The parameters an optimization would move, with their bounds.

    Shown per indicator as "200 -> search 100-300". Numbers written inside a signal expression
    are absent by design (spec section 9.1), which is why the editor tells the user to move a
    literal into an indicator to tune it.
    """
    return tuple(
        SearchableParameter(
            path=parameter.path,
            value=float(parameter.value),
            low=float(parameter.low),
            high=float(parameter.high),
        )
        for parameter in discover_parameters(strategy)
    )


def _review(strategy: Strategy) -> ConfigReview:
    return ConfigReview(
        valid=True,
        warnings=tuple(
            FieldIssue(path=warning.path, message=warning.message)
            for warning in strategy_warnings(strategy)
        ),
        canonical_yaml=dump_strategy(strategy),
        config=parse_strategy(strategy.model_dump(mode="json")).model_dump(mode="json"),
        namespace=_namespace(strategy),
        searchable=_searchable(strategy),
    )


def review_mapping(data: dict[str, Any]) -> ConfigReview:
    """Validate an already-parsed configuration."""
    install()
    try:
        return _review(build_strategy(data))
    except StrategyValidationError as error:
        return ConfigReview(valid=False, errors=tuple(field_issue(text) for text in error.issues))
    except CracktradeError as error:
        return ConfigReview(valid=False, errors=(FieldIssue(path="", message=str(error)),))


def review_yaml(text: str) -> ConfigReview:
    """Validate YAML as typed, reporting a parse failure on the line that caused it."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        line = getattr(getattr(error, "problem_mark", None), "line", None)
        return ConfigReview(
            valid=False,
            errors=(
                FieldIssue(
                    path="",
                    message=f"the file is not valid YAML: {error}",
                    # PyYAML counts from zero; editors count from one.
                    line=line + 1 if isinstance(line, int) else None,
                ),
            ),
        )

    if not isinstance(data, dict):
        return ConfigReview(
            valid=False,
            errors=(FieldIssue(path="", message="a strategy file must be a YAML mapping"),),
        )
    return review_mapping(data)


def strategy_from(data: dict[str, Any] | None, text: str | None) -> Strategy:
    """Build a validated strategy from whichever form the caller supplied.

    Raises:
        ConfigError: neither form was supplied, or both were.
        StrategyValidationError: the configuration is invalid.
    """
    install()
    if (data is None) == (text is None):
        raise ConfigError("supply exactly one of `config` or `yaml`")
    if text is not None:
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, dict):
            raise ConfigError("a strategy file must be a YAML mapping")
        return build_strategy(parsed)
    assert data is not None
    return build_strategy(data)
