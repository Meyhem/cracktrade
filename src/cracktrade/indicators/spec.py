"""What the engine needs to know about an indicator before it will compute one.

Normative reference: ``docs/ENGINE_SPEC.md`` section 5.2. This replaces the legacy engine's
``getattr(pandas_ta, type)`` dispatch plus ``inspect.signature`` introspection, which worked
but coupled the schema to pandas_ta's internal parameter names: a library rename turned into a
user-facing error about an undefined name in a signal expression.

Everything is declared: which price series the indicator consumes, what it accepts, what it
produces, how long it takes to warm up, and -- the part that matters most -- whether it is
causal at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import pandas as pd
    from pydantic import BaseModel

#: The raw series available to every indicator's compute function, plus ``source``, which is
#: whichever of them the user selected as the primary input.
SeriesMap = Mapping[str, "pd.Series"]

#: A compute function returns one series, or a mapping of output suffix to series.
ComputeResult = "pd.Series | Mapping[str, pd.Series]"

#: Alias for a parameter schema class. Needed because :class:`IndicatorSpec` has a field named
#: ``type``, which shadows the builtin inside the class body.
ParamsModel = type["BaseModel"]


class Causality(StrEnum):
    """Whether an indicator's value at bar *t* depends only on bars up to *t*."""

    #: Value at bar t uses bars <= t only. The only kind that may be registered.
    CAUSAL = "causal"

    #: Value at bar t is *labelled* t but describes a future bar (Ichimoku's senkou spans) or
    #: a past one it should not see (the chikou span is the close shifted backwards). Such
    #: outputs are dropped, never exposed. See spec section 2.2.
    FORWARD_PROJECTED = "forward_projected"

    #: Value at bar t depends on the whole sample -- global min/max scaling, whole-series
    #: z-scores. Registering one is a programming error: adding a bar would change history.
    NON_CAUSAL = "non_causal"


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """A registered indicator.

    Attributes:
        type: the value users write as ``type:`` in YAML.
        params: pydantic model validating this indicator's own parameters. Each indicator owns
            its schema, so adding one never means editing a shared allowlist.
        compute: builds the output from the price series and validated parameters.
        outputs: suffixes appended to the user's ``name`` for a multi-output indicator, empty
            for single-output. Declared rather than discovered, and asserted against what the
            implementation actually returns.
        warmup: bars required before the output is meaningful, given the parameters. A
            conservative over-estimate is safe (a few extra bars are suppressed); an
            under-estimate is not, so implementations round up.
        inputs: which raw series the indicator consumes. ``source`` means "whichever series the
            user selected"; the others are taken regardless of ``source``.
        causality: must be :attr:`Causality.CAUSAL` to be registered.
        description: one line, shown by ``cracktrade indicators``.
    """

    type: str
    params: ParamsModel
    compute: Callable[[SeriesMap, BaseModel], pd.Series | Mapping[str, pd.Series] | None]
    warmup: Callable[[BaseModel], int]
    description: str
    outputs: tuple[str, ...] = ()
    inputs: frozenset[str] = field(default_factory=lambda: frozenset({"source"}))
    causality: Causality = Causality.CAUSAL

    @property
    def is_multi_output(self) -> bool:
        """Whether this indicator contributes several names to the signal namespace."""
        return bool(self.outputs)

    def output_names(self, name: str) -> tuple[str, ...]:
        """The namespace keys this indicator contributes under the user's ``name``."""
        if not self.outputs:
            return (name,)
        return tuple(f"{name}_{suffix}" for suffix in self.outputs)

    @property
    def uses_source(self) -> bool:
        """Whether ``source`` has any effect on this indicator.

        Multi-input indicators (ATR, Stochastic, ADX) always receive high/low/close, so setting
        ``source`` on them does nothing and the user is told so.
        """
        return "source" in self.inputs


#: Names the raw price series are bound to, matching the signal namespace.
RAW_SERIES: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")
