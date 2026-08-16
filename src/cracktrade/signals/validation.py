"""Semantic validation of ``signal`` expressions.

Runs at load time. Every expression is parsed under the grammar and every name it references is
resolved against the names the strategy's indicators will produce -- before any data is
fetched, so a typo costs a second rather than a download.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cracktrade.errors import SignalSyntaxError, UnknownIndicatorError
from cracktrade.indicators import registry
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.spec import RAW_SERIES
from cracktrade.signals.grammar import parse_expression

if TYPE_CHECKING:
    from cracktrade.config import Strategy


def available_names(strategy: Strategy) -> set[str]:
    """Every name a signal in ``strategy`` may reference.

    Derived from the registry's *declared* outputs, so this works without computing anything.
    """
    install()
    names = set(RAW_SERIES)
    for config in strategy.indicators:
        try:
            spec = registry.get(config.type)
        except UnknownIndicatorError:
            # Reported by the indicator validator; not this one's business.
            continue
        names.update(spec.output_names(config.name))
    return names


def validate_signals(strategy: Strategy) -> list[str]:
    """Check the entry and exit signals parse and resolve."""
    names = available_names(strategy)
    issues = _check("entry.signal", strategy.entry.signal, names)

    if strategy.exit.signal is not None:
        issues.extend(_check("exit.signal", strategy.exit.signal, names))

    return issues


def _check(location: str, source: str, names: set[str]) -> list[str]:
    try:
        expression = parse_expression(source)
    except SignalSyntaxError as error:
        return [f"{location}: {error}"]

    unknown = sorted(expression.names - names)
    if not unknown:
        return []

    known = ", ".join(sorted(names))
    return [
        f"{location}: references undefined name(s) {', '.join(unknown)}. "
        f"Define them in the indicators section. Available: {known}"
    ]
