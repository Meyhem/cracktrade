"""The curated block library evolution composes strategies from.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.2.

A block is one tradeable condition: the indicators it needs, the numeric ranges those
indicators and its own thresholds may take, and the signal expression that ties them together.
Evolution never writes an expression; it picks blocks and moves numbers inside them. That is
the whole safety argument for this feature. Every expression here was written by hand, parses
under the section 2.1 grammar, and is causal by construction, so a genome cannot compose its way
to something the four look-ahead layers would have to catch.

Two consequences worth stating plainly.

*The space is bounded and countable.* A genome is a choice among a fixed number of blocks plus a
fixed number of bounded reals, so "how many distinct strategies did the search consider" has an
answer, and section 12.3's deflation has something honest to deflate by. Open-ended expression
evolution would not.

*The library is opinionated, and its opinions are a prior.* Nothing here is a breakout on a
rolling high, because ``rolling_max`` includes the current bar -- ``close > rolling_max(close,
n)`` is false on every bar of every history, and the shifted form is unwritable by design
(section 2.1). Channel *position* stands in for it instead: ``bbands``' percent-B and Donchian's
fractional position both express "near the top of the range" without reading a bar the decision
could not have seen. A strategy this library cannot express is not one the engine judged badly;
it is one nobody wrote a block for.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Gene:
    """One bounded number a block exposes to the search.

    Attributes:
        name: referenced by the block's expression and by its indicator parameters.
        low: inclusive lower bound.
        high: inclusive upper bound.
        integer: whether the value is a lattice point. Windows are; thresholds are not.
    """

    name: str
    low: float
    high: float
    integer: bool = False

    def __post_init__(self) -> None:
        if self.low >= self.high:
            msg = f"gene {self.name!r}: low ({self.low}) must be below high ({self.high})"
            raise ValueError(msg)

    def clamp(self, value: float) -> float:
        """Bring ``value`` inside the bounds, and onto the lattice if this gene is integral."""
        bounded = min(max(value, self.low), self.high)
        return float(round(bounded)) if self.integer else bounded

    @property
    def span(self) -> float:
        """Width of the range, used to scale mutation steps."""
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class GeneRef:
    """Marks an indicator parameter as coming from a gene rather than being a constant."""

    name: str


#: An indicator parameter is either a searched gene or a value the block pins.
ParamValue = GeneRef | int | float


@dataclass(frozen=True, slots=True)
class IndicatorTemplate:
    """One indicator a block needs, before its genes have values.

    ``alias`` is what the block's expression refers to. The rendered strategy gets a slot-scoped
    name instead (``entry_a_ma`` and so on), because two slots may both want a 50-day EMA and
    the schema requires indicator names to be unique.
    """

    alias: str
    type: str
    params: tuple[tuple[str, ParamValue], ...] = ()
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ConditionBlock:
    """One tradeable condition, usable as an entry or an exit.

    Attributes:
        name: stable identifier. Appears in the evolved strategy's name and in the report, so a
            reader can see which conditions were composed without reading the YAML.
        description: one line, for the report.
        expression: a signal expression with ``{alias}`` and ``{gene}`` placeholders. Written by
            hand and never generated.
        genes: the numbers evolution may move.
        indicators: what the expression needs computed.
    """

    name: str
    description: str
    expression: str
    genes: tuple[Gene, ...] = ()
    indicators: tuple[IndicatorTemplate, ...] = ()

    def __post_init__(self) -> None:
        gene_names = {gene.name for gene in self.genes}
        if len(gene_names) != len(self.genes):
            msg = f"block {self.name!r} declares a duplicate gene name"
            raise ValueError(msg)
        aliases = {indicator.alias for indicator in self.indicators}
        if len(aliases) != len(self.indicators):
            msg = f"block {self.name!r} declares a duplicate indicator alias"
            raise ValueError(msg)
        # One substitution mapping serves both, so a collision would silently render the wrong
        # thing rather than fail.
        if gene_names & aliases:
            msg = f"block {self.name!r} uses the same name for a gene and an indicator alias"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BlockInstance:
    """A block with its genes bound to values and its indicators given real names."""

    block: ConditionBlock
    expression: str
    indicators: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


def instantiate(block: ConditionBlock, prefix: str, values: Sequence[float]) -> BlockInstance:
    """Bind ``values`` to ``block``'s genes and render it under ``prefix``.

    Args:
        block: the condition to render.
        prefix: slot identifier, prepended to every indicator name so that two slots holding the
            same block do not collide in the strategy's indicator list.
        values: one value per gene, in declaration order.

    Raises:
        ValueError: ``values`` does not match the block's arity.
    """
    if len(values) != len(block.genes):
        msg = f"block {block.name!r} takes {len(block.genes)} gene(s), got {len(values)}"
        raise ValueError(msg)

    bound = {
        gene.name: gene.clamp(float(value)) for gene, value in zip(block.genes, values, strict=True)
    }
    integral = {gene.name: gene.integer for gene in block.genes}

    names = {indicator.alias: f"{prefix}_{indicator.alias}" for indicator in block.indicators}
    literals = {
        name: str(int(value)) if integral[name] else f"{value:.2f}" for name, value in bound.items()
    }

    return BlockInstance(
        block=block,
        expression=block.expression.format(**names, **literals),
        indicators=tuple(
            _render_indicator(indicator, names[indicator.alias], bound, integral)
            for indicator in block.indicators
        ),
    )


def _render_indicator(
    template: IndicatorTemplate,
    name: str,
    bound: Mapping[str, float],
    integral: Mapping[str, bool],
) -> dict[str, Any]:
    """One entry of the strategy's ``indicators`` list, as the schema wants it."""
    rendered: dict[str, Any] = {"name": name, "type": template.type}
    if template.source is not None:
        rendered["source"] = template.source
    for parameter, value in template.params:
        rendered[parameter] = _resolve(value, bound, integral)
    return rendered


def _resolve(value: ParamValue, bound: Mapping[str, float], integral: Mapping[str, bool]) -> Any:
    """A parameter's concrete value, typed the way the indicator's schema expects it.

    Int-ness is carried from the gene rather than inferred from the number, for the same reason
    the optimizer records it (section 9.1): a window that lands on 20.0 is still a window, and
    the indicator schemas are strict, so handing one a float where it declared an int is a
    validation error rather than a coercion.
    """
    if isinstance(value, GeneRef):
        return int(bound[value.name]) if integral[value.name] else round(bound[value.name], 2)
    return value


def library_warmup() -> int:
    """The largest warm-up any block in the library could ever need.

    Every scored window in an evolution run is preceded by this many bars, sized from the
    library rather than from a particular genome for the reason section 9.2 sizes the optimizer's
    test window from the widest candidate: a genome whose warm-up exceeded the prefix would have
    its signals suppressed *inside* the scored region, quietly losing bars it should have been
    able to trade. Sizing from the library is the only value that is right for every genome the
    search can reach.
    """
    registry = importlib.import_module("cracktrade.indicators.registry")
    importlib.import_module("cracktrade.indicators.compute").registry_installed()

    warmup = 0
    for block in BLOCKS:
        highest = {gene.name: gene.high for gene in block.genes}
        integral = {gene.name: gene.integer for gene in block.genes}
        for template in block.indicators:
            spec = registry.get(template.type)
            params = {
                parameter: _resolve(value, highest, integral)
                for parameter, value in template.params
            }
            warmup = max(warmup, int(spec.warmup(spec.params.model_validate(params))))
    return warmup


def _window(name: str, low: float, high: float) -> Gene:
    return Gene(name=name, low=low, high=high, integer=True)


def _level(name: str, low: float, high: float) -> Gene:
    return Gene(name=name, low=low, high=high, integer=False)


#: Every condition evolution may compose with, in a fixed order.
#:
#: The order is part of the reproducibility contract: a genome stores block *indices*, so
#: reordering this tuple would silently reinterpret every stored genome and every recorded seed.
#: Add to the end.
#:
#: Blocks come in opposed pairs wherever the condition has a natural opposite. Neither member is
#: labelled "entry" or "exit": a trend strategy enters above its average and leaves below it,
#: while a mean-reversion strategy does exactly the reverse, and a library that pre-assigned
#: roles would have excluded one of those two families by construction.
BLOCKS: Final[tuple[ConditionBlock, ...]] = (
    ConditionBlock(
        name="price_above_ma",
        description="Close above its moving average",
        expression="close > {ma}",
        genes=(_window("ma_window", 10, 200),),
        indicators=(IndicatorTemplate("ma", "ema", (("window", GeneRef("ma_window")),)),),
    ),
    ConditionBlock(
        name="price_below_ma",
        description="Close below its moving average",
        expression="close < {ma}",
        genes=(_window("ma_window", 10, 200),),
        indicators=(IndicatorTemplate("ma", "ema", (("window", GeneRef("ma_window")),)),),
    ),
    ConditionBlock(
        name="fast_ma_above_slow_ma",
        description="Fast moving average above the slow one",
        expression="{fast} > {slow}",
        genes=(_window("fast_window", 5, 50), _window("slow_window", 60, 200)),
        # The two ranges are disjoint on purpose. "Fast" and "slow" are only meaningful
        # relative to each other, and overlapping ranges would let the search produce a
        # genome whose fast average is the slower of the two -- a valid strategy that means
        # the opposite of what its block name says, and that no reader would catch.
        indicators=(
            IndicatorTemplate("fast", "sma", (("window", GeneRef("fast_window")),)),
            IndicatorTemplate("slow", "sma", (("window", GeneRef("slow_window")),)),
        ),
    ),
    ConditionBlock(
        name="fast_ma_below_slow_ma",
        description="Fast moving average below the slow one",
        expression="{fast} < {slow}",
        genes=(_window("fast_window", 5, 50), _window("slow_window", 60, 200)),
        indicators=(
            IndicatorTemplate("fast", "sma", (("window", GeneRef("fast_window")),)),
            IndicatorTemplate("slow", "sma", (("window", GeneRef("slow_window")),)),
        ),
    ),
    ConditionBlock(
        name="rsi_below_level",
        description="RSI below a threshold (oversold)",
        expression="{rsi} < {level}",
        genes=(_window("rsi_window", 5, 30), _level("level", 10.0, 45.0)),
        indicators=(IndicatorTemplate("rsi", "rsi", (("window", GeneRef("rsi_window")),)),),
    ),
    ConditionBlock(
        name="rsi_above_level",
        description="RSI above a threshold (overbought)",
        expression="{rsi} > {level}",
        genes=(_window("rsi_window", 5, 30), _level("level", 55.0, 90.0)),
        indicators=(IndicatorTemplate("rsi", "rsi", (("window", GeneRef("rsi_window")),)),),
    ),
    ConditionBlock(
        name="macd_above_signal",
        description="MACD line above its signal line",
        expression="{macd}_macd > {macd}_macds",
        genes=(
            _window("fast", 5, 20),
            _window("slow", 21, 60),
            _window("signal", 5, 15),
        ),
        indicators=(
            IndicatorTemplate(
                "macd",
                "macd",
                (
                    ("fast", GeneRef("fast")),
                    ("slow", GeneRef("slow")),
                    ("signal", GeneRef("signal")),
                ),
            ),
        ),
    ),
    ConditionBlock(
        name="macd_below_signal",
        description="MACD line below its signal line",
        expression="{macd}_macd < {macd}_macds",
        genes=(
            _window("fast", 5, 20),
            _window("slow", 21, 60),
            _window("signal", 5, 15),
        ),
        indicators=(
            IndicatorTemplate(
                "macd",
                "macd",
                (
                    ("fast", GeneRef("fast")),
                    ("slow", GeneRef("slow")),
                    ("signal", GeneRef("signal")),
                ),
            ),
        ),
    ),
    ConditionBlock(
        name="bollinger_position_above",
        description="Close in the upper part of its Bollinger band",
        expression="{bb}_bbp > {level}",
        genes=(
            _window("bb_window", 10, 60),
            _level("bb_std", 1.0, 3.0),
            _level("level", 0.55, 1.05),
        ),
        indicators=(
            IndicatorTemplate(
                "bb",
                "bbands",
                (("window", GeneRef("bb_window")), ("std", GeneRef("bb_std"))),
            ),
        ),
    ),
    ConditionBlock(
        name="bollinger_position_below",
        description="Close in the lower part of its Bollinger band",
        expression="{bb}_bbp < {level}",
        genes=(
            _window("bb_window", 10, 60),
            _level("bb_std", 1.0, 3.0),
            _level("level", -0.05, 0.45),
        ),
        indicators=(
            IndicatorTemplate(
                "bb",
                "bbands",
                (("window", GeneRef("bb_window")), ("std", GeneRef("bb_std"))),
            ),
        ),
    ),
    ConditionBlock(
        name="stochastic_below_level",
        description="Stochastic %K below a threshold",
        expression="{st}_stochk < {level}",
        genes=(
            _window("k", 5, 30),
            _window("d", 2, 8),
            _window("smooth_k", 2, 8),
            _level("level", 5.0, 35.0),
        ),
        indicators=(
            IndicatorTemplate(
                "st",
                "stoch",
                (("k", GeneRef("k")), ("d", GeneRef("d")), ("smooth_k", GeneRef("smooth_k"))),
            ),
        ),
    ),
    ConditionBlock(
        name="stochastic_above_level",
        description="Stochastic %K above a threshold",
        expression="{st}_stochk > {level}",
        genes=(
            _window("k", 5, 30),
            _window("d", 2, 8),
            _window("smooth_k", 2, 8),
            _level("level", 65.0, 95.0),
        ),
        indicators=(
            IndicatorTemplate(
                "st",
                "stoch",
                (("k", GeneRef("k")), ("d", GeneRef("d")), ("smooth_k", GeneRef("smooth_k"))),
            ),
        ),
    ),
    ConditionBlock(
        name="adx_above_level",
        description="ADX above a threshold (a trend is present)",
        expression="{adx}_adx > {level}",
        genes=(_window("adx_window", 7, 40), _level("level", 12.0, 40.0)),
        indicators=(IndicatorTemplate("adx", "adx", (("window", GeneRef("adx_window")),)),),
    ),
    ConditionBlock(
        name="adx_below_level",
        description="ADX below a threshold (no trend)",
        expression="{adx}_adx < {level}",
        genes=(_window("adx_window", 7, 40), _level("level", 10.0, 30.0)),
        indicators=(IndicatorTemplate("adx", "adx", (("window", GeneRef("adx_window")),)),),
    ),
    ConditionBlock(
        name="directional_bullish",
        description="Positive directional movement exceeds negative",
        expression="{adx}_dmp > {adx}_dmn",
        genes=(_window("adx_window", 7, 40),),
        indicators=(IndicatorTemplate("adx", "adx", (("window", GeneRef("adx_window")),)),),
    ),
    ConditionBlock(
        name="directional_bearish",
        description="Negative directional movement exceeds positive",
        expression="{adx}_dmp < {adx}_dmn",
        genes=(_window("adx_window", 7, 40),),
        indicators=(IndicatorTemplate("adx", "adx", (("window", GeneRef("adx_window")),)),),
    ),
    ConditionBlock(
        name="cci_below_level",
        description="Commodity channel index below a threshold",
        expression="{cci} < {level}",
        genes=(_window("cci_window", 7, 40), _level("level", -250.0, -50.0)),
        indicators=(IndicatorTemplate("cci", "cci", (("window", GeneRef("cci_window")),)),),
    ),
    ConditionBlock(
        name="cci_above_level",
        description="Commodity channel index above a threshold",
        expression="{cci} > {level}",
        genes=(_window("cci_window", 7, 40), _level("level", 50.0, 250.0)),
        indicators=(IndicatorTemplate("cci", "cci", (("window", GeneRef("cci_window")),)),),
    ),
    ConditionBlock(
        name="momentum_above_level",
        description="Rate of change above a threshold",
        expression="{roc} > {level}",
        genes=(_window("roc_window", 3, 60), _level("level", -2.0, 12.0)),
        indicators=(IndicatorTemplate("roc", "roc", (("window", GeneRef("roc_window")),)),),
    ),
    ConditionBlock(
        name="momentum_below_level",
        description="Rate of change below a threshold",
        expression="{roc} < {level}",
        genes=(_window("roc_window", 3, 60), _level("level", -12.0, 2.0)),
        indicators=(IndicatorTemplate("roc", "roc", (("window", GeneRef("roc_window")),)),),
    ),
    ConditionBlock(
        name="zscore_below_level",
        description="Rolling z-score of price below a threshold",
        expression="{z} < {level}",
        genes=(_window("z_window", 10, 90), _level("level", -3.0, -0.3)),
        indicators=(IndicatorTemplate("z", "zscore", (("window", GeneRef("z_window")),)),),
    ),
    ConditionBlock(
        name="zscore_above_level",
        description="Rolling z-score of price above a threshold",
        expression="{z} > {level}",
        genes=(_window("z_window", 10, 90), _level("level", 0.3, 3.0)),
        indicators=(IndicatorTemplate("z", "zscore", (("window", GeneRef("z_window")),)),),
    ),
    ConditionBlock(
        name="volume_above_average",
        description="Volume above a multiple of its own average",
        expression="volume > {multiple} * {vma}",
        genes=(_window("vma_window", 5, 60), _level("multiple", 1.1, 3.0)),
        indicators=(
            IndicatorTemplate("vma", "sma", (("window", GeneRef("vma_window")),), source="volume"),
        ),
    ),
    ConditionBlock(
        name="volatility_below_level",
        description="Normalised ATR below a threshold (quiet market)",
        expression="{natr} < {level}",
        genes=(_window("natr_window", 7, 40), _level("level", 0.5, 6.0)),
        indicators=(IndicatorTemplate("natr", "natr", (("window", GeneRef("natr_window")),)),),
    ),
    ConditionBlock(
        name="volatility_above_level",
        description="Normalised ATR above a threshold (active market)",
        expression="{natr} > {level}",
        genes=(_window("natr_window", 7, 40), _level("level", 1.0, 8.0)),
        indicators=(IndicatorTemplate("natr", "natr", (("window", GeneRef("natr_window")),)),),
    ),
    ConditionBlock(
        name="supertrend_bullish",
        description="Supertrend in its up state",
        expression="{stx}_supertd > 0",
        genes=(_window("st_window", 5, 20), _level("multiplier", 1.5, 5.0)),
        indicators=(
            IndicatorTemplate(
                "stx",
                "supertrend",
                (("window", GeneRef("st_window")), ("multiplier", GeneRef("multiplier"))),
            ),
        ),
    ),
    ConditionBlock(
        name="supertrend_bearish",
        description="Supertrend in its down state",
        expression="{stx}_supertd < 0",
        genes=(_window("st_window", 5, 20), _level("multiplier", 1.5, 5.0)),
        indicators=(
            IndicatorTemplate(
                "stx",
                "supertrend",
                (("window", GeneRef("st_window")), ("multiplier", GeneRef("multiplier"))),
            ),
        ),
    ),
    ConditionBlock(
        name="donchian_position_above",
        description="Close in the upper part of its Donchian channel",
        # Written as a fraction of the channel rather than as 'close > upper', which cannot
        # fire: the upper band at bar t includes bar t's own high, and the shifted form the
        # comparison actually wants is unexpressible under the section 2.1 grammar.
        expression="(close - {dc}_dcl) > {fraction} * ({dc}_dcu - {dc}_dcl)",
        genes=(_window("channel", 10, 60), _level("fraction", 0.5, 0.95)),
        indicators=(
            IndicatorTemplate(
                "dc",
                "donchian",
                (
                    ("lower_length", GeneRef("channel")),
                    ("upper_length", GeneRef("channel")),
                ),
            ),
        ),
    ),
    ConditionBlock(
        name="donchian_position_below",
        description="Close in the lower part of its Donchian channel",
        expression="(close - {dc}_dcl) < {fraction} * ({dc}_dcu - {dc}_dcl)",
        genes=(_window("channel", 10, 60), _level("fraction", 0.05, 0.5)),
        indicators=(
            IndicatorTemplate(
                "dc",
                "donchian",
                (
                    ("lower_length", GeneRef("channel")),
                    ("upper_length", GeneRef("channel")),
                ),
            ),
        ),
    ),
)


def block_named(name: str) -> ConditionBlock:
    """Look up a block by name.

    Raises:
        KeyError: no such block.
    """
    for block in BLOCKS:
        if block.name == name:
            return block
    raise KeyError(name)
