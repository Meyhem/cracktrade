"""Validating a configuration without storing or running anything.

Behind the editor. Every keystroke can ask this what is wrong, what would be searched, and
which names a signal may refer to -- all before a byte of market data is fetched, which is what
spec section 3.6 already required of the engine.

Invalidity is an *answer* here, not a transport failure: the endpoint returns 200 with
``valid: false``. A 4xx would be right if the request were malformed, but a config the user is
halfway through typing is exactly what this is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from cracktrade.api.errors import FieldIssue
from cracktrade.api.services.diff import SectionDiff, diff_by_section
from cracktrade.config import Strategy, dump_strategy, parse_strategy
from cracktrade.errors import ConfigError, CracktradeError, StrategyValidationError
from cracktrade.indicators import RAW_SERIES
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import discover_parameters
from cracktrade.strategy import build_strategy, strategy_warnings

#: Separator the engine uses when rendering "<dotted.path>: <message>" (spec section 3.9).
_PATH_SEPARATOR = ": "

#: The left-hand side of that rendering: a dotted path, optionally annotated with the YAML line
#: it came from. Matched exactly rather than merely split on, because the whole point is to tell
#: a path apart from prose that happens to contain a colon.
_LOCATION = re.compile(r"^(?P<path>[^\s:]+?)(?: \(line (?P<line>\d+)\))?$")


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

    The engine renders these as ``path: message``, or as ``path (line N): message`` when it was
    given the YAML the mapping came from (spec section 3.9). Splitting them apart is what lets
    the editor put an error on the field that caused it rather than in a banner above the form,
    and put a marker on the line in the YAML pane rather than nowhere at all.

    A message with no recognisable location is kept whole rather than guessed at. The location
    has to match a dotted path exactly, because the alternative -- splitting on the first
    ``": "`` and hoping -- turns any prose containing a colon into a field name.
    """
    location, separator, message = text.partition(_PATH_SEPARATOR)
    match = _LOCATION.match(location) if separator else None
    if match is None:
        return FieldIssue(path="", message=text, line=line)
    found = match.group("line")
    return FieldIssue(
        path=match.group("path"),
        message=message,
        line=int(found) if found is not None else line,
    )


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


def review_mapping(data: dict[str, Any], source: str | None = None) -> ConfigReview:
    """Validate an already-parsed configuration.

    ``source`` is the YAML the mapping was parsed from, when the caller has it. It buys nothing
    but line numbers on the errors, and costs nothing when absent.
    """
    install()
    try:
        return _review(build_strategy(data, source=source))
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
    return review_mapping(data, source=text)


@dataclass(frozen=True, slots=True)
class ConfigComparison:
    """Two configurations compared, grouped by section, with both YAML panes."""

    groups: tuple[SectionDiff, ...]
    from_yaml: str
    to_yaml: str


def compare_configs(older: Strategy, newer: Strategy) -> ConfigComparison:
    """Compare two configurations that need not be versions of anything.

    Both sides are canonicalised before the comparison so the two are described the same way.
    Diffing what the user typed against what a search emitted would report key order and
    omitted defaults as changes, and a promote dialog that claims the optimizer moved a field
    it never touched is worse than no dialog: it is the one screen whose whole job is to say
    precisely which numbers a machine chose.

    Canonicalisation goes through the YAML writer rather than through ``model_dump``. The two
    disagree: the model nests an indicator's parameters under ``params``, so a window would be
    addressed ``indicators.sma_long.params.window`` here and ``indicators.sma_long.window``
    everywhere else -- in the version diff, which reads stored configs, and in the searchable
    parameters the editor shows, which use the optimizer's addressing (spec section 9.1). One
    path per fact, and the YAML form is the one the user is reading in the pane beside it.
    """
    from_yaml = dump_strategy(older)
    to_yaml = dump_strategy(newer)
    return ConfigComparison(
        groups=diff_by_section(_mapping_of(from_yaml), _mapping_of(to_yaml)),
        from_yaml=from_yaml,
        to_yaml=to_yaml,
    )


def _mapping_of(canonical_yaml: str) -> dict[str, Any]:
    parsed = yaml.safe_load(canonical_yaml)
    assert isinstance(parsed, dict)
    return parsed


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
