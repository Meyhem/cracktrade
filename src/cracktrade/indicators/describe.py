"""A readable description of the registered catalogue.

The registry holds compute functions and pydantic models -- exactly what the engine needs and
nothing an interface can render. This module turns it into plain data, so that the CLI's
``indicators`` command and the API's metadata endpoint show the same catalogue without either
of them reaching into a pydantic model's internals.

That reaching is the reason this exists. The CLI previously read
``spec.params.model_fields`` itself, which put a piece of engine knowledge -- how a parameter
schema is shaped -- inside an interface, where the next consumer would have had to reproduce
it.
"""

from __future__ import annotations

from dataclasses import dataclass

from cracktrade.indicators import registry
from cracktrade.indicators.spec import IndicatorSpec


@dataclass(frozen=True, slots=True)
class ParameterDescription:
    """One tunable parameter of an indicator."""

    name: str
    default: float | int | str | None


@dataclass(frozen=True, slots=True)
class IndicatorDescription:
    """One registered indicator type, as a form builder or a table needs it.

    Attributes:
        type: the value written as ``type:`` in YAML.
        description: one line describing what it computes.
        parameters: the indicator's own parameters and their defaults.
        outputs: suffixes appended to the user's ``name`` for a multi-output indicator, empty
            when it contributes a single name.
        inputs: which raw price series it consumes. ``source`` means "whichever series the
            user selected".
        uses_source: whether setting ``source`` has any effect. ATR and the other multi-input
            indicators always receive high/low/close, so for them it does not.
    """

    type: str
    description: str
    parameters: tuple[ParameterDescription, ...]
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]
    uses_source: bool

    def namespace_names(self, name: str) -> tuple[str, ...]:
        """The signal-namespace names this indicator would contribute under ``name``."""
        if not self.outputs:
            return (name,)
        return tuple(f"{name}_{suffix}" for suffix in self.outputs)


def describe(spec: IndicatorSpec) -> IndicatorDescription:
    """Describe one registered indicator."""
    return IndicatorDescription(
        type=spec.type,
        description=spec.description,
        parameters=tuple(
            ParameterDescription(name=name, default=field.default)
            for name, field in spec.params.model_fields.items()
        ),
        outputs=spec.outputs,
        inputs=tuple(sorted(spec.inputs)),
        uses_source=spec.uses_source,
    )


def describe_catalogue() -> tuple[IndicatorDescription, ...]:
    """Every registered indicator, in registration order.

    :func:`cracktrade.indicators.catalogue.install` must have run; a caller that has not
    installed the catalogue gets an empty tuple rather than a partial one.
    """
    return tuple(describe(spec) for spec in registry.all_specs())
