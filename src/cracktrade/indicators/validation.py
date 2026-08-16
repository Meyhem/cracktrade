"""Semantic validation of the ``indicators`` section.

Runs at load time, before any market data is fetched: an unknown indicator type or a bad
parameter should cost the user a second, not a download.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from cracktrade.errors import UnknownIndicatorError
from cracktrade.indicators import registry
from cracktrade.indicators.catalogue import install

if TYPE_CHECKING:
    from cracktrade.config import Strategy


def validate_indicators(strategy: Strategy) -> list[str]:
    """Check every indicator resolves and its parameters are accepted.

    Returns every problem found rather than stopping at the first, so one edit round-trip
    fixes the whole section.
    """
    install()
    issues: list[str] = []
    produced: dict[str, str] = {}

    for config in strategy.indicators:
        location = f"indicators.{config.name}"
        try:
            spec = registry.get(config.type)
        except UnknownIndicatorError as error:
            issues.append(f"{location}: {error}")
            continue

        try:
            spec.params.model_validate(config.params)
        except ValidationError as error:
            accepted = ", ".join(sorted(spec.params.model_fields)) or "(none)"
            for item in error.errors():
                field = ".".join(str(part) for part in item["loc"]) or "(parameters)"
                issues.append(f"{location}.{field}: {item['msg']}. Accepted parameters: {accepted}")

        if config.source.value != "close" and not spec.uses_source:
            issues.append(
                f"{location}.source: {config.type!r} always reads high/low/close, so "
                f"source={config.source.value!r} has no effect; remove it"
            )

        for name in spec.output_names(config.name):
            if name in produced:
                issues.append(
                    f"{location}: produces the name {name!r}, which "
                    f"{produced[name]!r} already produces"
                )
            produced[name] = config.name

    return issues
