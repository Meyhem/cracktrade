"""What evolution actually manipulates, and how it becomes a strategy.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.3.

A genome is a variable-length chain of one to five entry conditions and, independently, a
variable-length chain of zero to five exit conditions, each chain joined pairwise by an
independently-drawn ``&``/``|``, plus the exit mechanisms (stop, take-profit, holding bounds).
Each condition is a *slot*: a block choice together with that block's gene values, and the slot
is the unit crossover exchanges -- swapping a block index without its genes would hand the child
an arity that does not match its block.

:func:`render` turns a genome into a validated :class:`~cracktrade.config.Strategy`. It is
**total**: every genome the operators can produce renders to a strategy that passes structural
and semantic validation. That is not a hope, it is what :func:`repair` and the disjoint gene
ranges in :mod:`cracktrade.evolution.blocks` are for, and ``tests/test_evolution.py`` asserts it
over the whole reachable space. A genome that failed to render would otherwise be scored
``INFEASIBLE`` and quietly bias the search away from a region of the library rather than
reporting a bug in it.

The chassis -- ticker, dates, capital, costs, position sizing -- is *not* evolved. Section 9.1
never optimizes those sections either, and for the same reason: they are the user's statement of
what they are trading and with what, not a hypothesis about the market.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from cracktrade.config import Interval
from cracktrade.evolution.blocks import BLOCKS, Gene, instantiate
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from cracktrade.config import PositionSizing, Strategy

#: Prefix for each condition's rendered indicator names, combined with its position in the chain
#: (``f"{ENTRY_PREFIX}{index}"``, e.g. ``"e0"``, ``"e1"``) so two conditions in the same chain --
#: or an entry and an exit condition -- never collide, even when they share a block.
ENTRY_PREFIX: Final = "e"
EXIT_PREFIX: Final = "x"

#: How conditions in a chain may be combined, pairwise. Element-wise operators, per the section
#: 2.1 grammar -- ``and``/``or`` do not vectorise over price series and are rejected by it.
COMBINATORS: Final[tuple[str, ...]] = ("&", "|")

#: Bounds on a condition chain's length. An entry chain may never be empty -- a strategy needs at
#: least one entry signal -- while an exit chain may, which preserves the "mechanical exits only"
#: case where the only exit logic is a stop, a take-profit, or a holding cap.
MIN_ENTRY_CONDITIONS: Final = 1
MIN_EXIT_CONDITIONS: Final = 0
MAX_CONDITIONS: Final = 5

#: The stop mechanisms a genome may choose between, plus their gene bounds. Only one stop is
#: ever active (section 3.7's priority chain), so the genome picks a kind rather than carrying
#: three independent stops of which two would be silently shadowed.
STOP_KINDS: Final[tuple[str, ...]] = ("fixed", "trailing", "atr")

STOP_GENES: Final[Mapping[str, Gene]] = {
    "fixed": Gene("stop_loss_pct", 1.0, 20.0),
    "trailing": Gene("trailing_stop_pct", 2.0, 25.0),
    "atr": Gene("atr_stop_multiplier", 1.0, 6.0),
}

#: Field name each stop kind renders to.
STOP_FIELDS: Final[Mapping[str, str]] = {
    "fixed": "stop_loss_pct",
    "trailing": "trailing_stop_pct",
    "atr": "atr_stop_multiplier",
}

TAKE_PROFIT_GENE: Final = Gene("take_profit_pct", 2.0, 50.0)
MAX_HOLDING_GENE: Final = Gene("max_holding_bars", 3, 120, integer=True)
MIN_HOLDING_GENE: Final = Gene("min_holding_bars", 1, 20, integer=True)

#: Holding cap given to a genome that repair found had no way out of a position at all.
FALLBACK_MAX_HOLDING: Final = 20


@dataclass(frozen=True, slots=True)
class Slot:
    """One block choice with its genes bound.

    Attributes:
        block: index into :data:`~cracktrade.evolution.blocks.BLOCKS`. Stored as an index rather
            than a name so a genome is a compact fixed-shape record; the tuple's order is part
            of the reproducibility contract.
        values: one value per gene of that block, in declaration order.
    """

    block: int
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Stop:
    """The one active stop, if the genome chose to carry one."""

    kind: str
    value: float


@dataclass(frozen=True, slots=True)
class Genome:
    """A complete candidate strategy, before it is rendered.

    Frozen, so the operators produce new genomes rather than mutating a population member that
    something else still holds a reference to -- the same discipline the config models follow.
    """

    entries: tuple[Slot, ...]
    entry_ops: tuple[str, ...]
    exits: tuple[Slot, ...]
    exit_ops: tuple[str, ...]
    stop: Stop | None
    take_profit_pct: float | None
    max_holding_bars: int | None
    min_holding_bars: int | None

    def __post_init__(self) -> None:
        _validate_chain(self.entries, self.entry_ops, MIN_ENTRY_CONDITIONS, "entries")
        _validate_chain(self.exits, self.exit_ops, MIN_EXIT_CONDITIONS, "exits")


def _validate_chain(
    slots: tuple[Slot, ...], ops: tuple[str, ...], minimum: int, label: str
) -> None:
    """The invariant every condition chain must hold: length in bounds, one op fewer than slots.

    A constructor-time check, not a repair -- the same precedent :class:`~cracktrade.evolution.
    blocks.Gene` and :class:`~cracktrade.evolution.blocks.ConditionBlock` already set. It matters
    here because tests build a ``Genome`` by hand at several sites, bypassing :func:`repair`.
    """
    if not minimum <= len(slots) <= MAX_CONDITIONS:
        msg = (
            f"{label}: expected between {minimum} and {MAX_CONDITIONS} conditions, got {len(slots)}"
        )
        raise ValueError(msg)
    if len(ops) != max(0, len(slots) - 1):
        msg = (
            f"{label}: expected {max(0, len(slots) - 1)} operator(s) for {len(slots)} "
            f"condition(s), got {len(ops)}"
        )
        raise ValueError(msg)
    if any(op not in COMBINATORS for op in ops):
        msg = f"{label}: operator(s) must be one of {COMBINATORS}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Chassis:
    """Everything about a run that evolution does not get to choose.

    Attributes:
        name: the evolved strategy's name.
        ticker: the single symbol, chosen by the user. Evolution never searches over tickers:
            letting it pick would add cross-asset selection bias on top of the structure search
            the deflation in section 16.6 is already accounting for, and the two are not
            separable after the fact.
        start_date: first bar to request.
        end_date: last bar to request.
        initial_capital: starting equity.
        slippage_pct: per-side slippage, in percent.
        commission_pct: per-side commission, in percent.
        risk_free_rate: annual rate, as a fraction.
        interval: bar width. Evolution never searches over it for the same reason it never
            searches over the ticker -- it is part of the question, not the answer.
        position_sizing: optional sizing rule, passed through unchanged.
    """

    name: str
    ticker: str
    start_date: date
    end_date: date
    interval: Interval = Interval.D1
    initial_capital: float = 100_000.0
    slippage_pct: float = 0.1
    commission_pct: float = 0.1
    risk_free_rate: float = 0.04
    position_sizing: PositionSizing | None = None

    def execution(self) -> dict[str, Any]:
        """The ``execution`` section, as the schema wants it."""
        return {
            "initial_capital": self.initial_capital,
            "slippage_pct": self.slippage_pct,
            "commission_pct": self.commission_pct,
            "risk_free_rate": self.risk_free_rate,
        }

    def universe(self) -> dict[str, Any]:
        """The ``universe`` section, as the schema wants it."""
        return {
            "ticker": self.ticker,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "interval": self.interval,
        }

    def data_config(self) -> Strategy:
        """A minimal valid strategy, used only to fetch this chassis's price history.

        :func:`~cracktrade.data.load_history` reads ``universe`` and nothing else, but it takes a
        whole strategy, and a chassis has no entry or exit rule until a genome supplies one. The
        placeholder below exists to satisfy the schema for the length of one download; it is
        never simulated, never scored, and never reaches a result.
        """
        return build_strategy(
            {
                "strategy": {"name": self.name},
                "universe": self.universe(),
                "execution": self.execution(),
                "entry": {"signal": "close > open"},
                "exit": {"max_holding_bars": 1},
            }
        )


def render(genome: Genome, chassis: Chassis) -> Strategy:
    """Turn a genome into a validated strategy.

    Raises:
        StrategyValidationError: the genome does not describe a valid strategy. This is a bug in
            the block library or in :func:`repair`, never a user error -- see the module
            docstring.
    """
    return build_strategy(mapping(genome, chassis))


def _fold(
    slots: Sequence[Slot], ops: Sequence[str], prefix: str
) -> tuple[str, list[Mapping[str, Any]]]:
    """Left-fold a condition chain into one expression, prefix-scoped by position.

    Every step re-wraps the running expression in parentheses, not just the newest term, so a
    chain of any length folds to e.g. ``((a) & (b)) | (c)`` and the grammar's precedence trap --
    '&'/'|' bind more tightly than comparison, so an unparenthesised operand parses as something
    else entirely -- can never bite regardless of how many conditions are chained. A single-slot
    chain folds to its bare expression, unwrapped.
    """
    indicators: list[Mapping[str, Any]] = []
    instance = instantiate(BLOCKS[slots[0].block], f"{prefix}0", slots[0].values)
    indicators.extend(instance.indicators)
    expression = instance.expression

    for index, (slot, op) in enumerate(zip(slots[1:], ops, strict=True), start=1):
        instance = instantiate(BLOCKS[slot.block], f"{prefix}{index}", slot.values)
        indicators.extend(instance.indicators)
        expression = f"({expression}) {op} ({instance.expression})"

    return expression, indicators


def mapping(genome: Genome, chassis: Chassis) -> dict[str, Any]:
    """The strategy mapping a genome describes, before validation."""
    indicators: list[Mapping[str, Any]] = []

    entry_signal, entry_indicators = _fold(genome.entries, genome.entry_ops, ENTRY_PREFIX)
    indicators.extend(entry_indicators)

    exit_rule: dict[str, Any] = {}
    if genome.exits:
        exit_signal, exit_indicators = _fold(genome.exits, genome.exit_ops, EXIT_PREFIX)
        indicators.extend(exit_indicators)
        exit_rule["signal"] = exit_signal

    if genome.stop is not None:
        exit_rule[STOP_FIELDS[genome.stop.kind]] = round(genome.stop.value, 2)
    if genome.take_profit_pct is not None:
        exit_rule["take_profit_pct"] = round(genome.take_profit_pct, 2)
    # Always the bar-denominated spelling. The genes were always bar counts, and the `_days`
    # fields are refused outright on an intraday strategy -- so emitting them would make every
    # genome invalid the moment the chassis was not daily.
    if genome.min_holding_bars is not None:
        exit_rule["min_holding_bars"] = int(genome.min_holding_bars)
    if genome.max_holding_bars is not None:
        exit_rule["max_holding_bars"] = int(genome.max_holding_bars)

    payload: dict[str, Any] = {
        "strategy": {"name": chassis.name},
        "universe": chassis.universe(),
        "execution": chassis.execution(),
        "indicators": indicators,
        "entry": {"signal": entry_signal},
        "exit": exit_rule,
    }
    if chassis.position_sizing is not None:
        payload["position_sizing"] = chassis.position_sizing.model_dump(mode="json")
    return payload


def repair(genome: Genome, *, session_bars: int | None = None) -> Genome:
    """Bring a genome back inside the schema's constraints.

    Two of the schema's rules are not expressible as independent gene bounds, so the operators
    can produce a genome that violates them. Repairing is deliberately preferred to rejecting:
    a rejected genome is a wasted evaluation, and a genome scored ``INFEASIBLE`` for a reason
    that has nothing to do with the market would bias the search away from a whole region of the
    library.

    Both repairs are deterministic. A repair that consulted the random number generator would
    make a run's reproducibility depend on how often it was needed.

    Args:
        genome: the candidate to repair.
        session_bars: bars in one trading session, when the run is intraday. A minimum holding
            period at or above it is unsatisfiable -- the forced session close overrides it, so
            no position could ever reach it -- and the engine refuses such a strategy outright
            (spec section 7.6). Left unclamped, a third of the reachable space would be
            unrenderable at ``1h``, where a Xetra session is nine bars against a gene drawn up
            to twenty, and the search would report the losses as a defect in the block library.
    """
    exits = (
        bool(genome.exits)
        or genome.stop is not None
        or genome.take_profit_pct is not None
        or genome.max_holding_bars is not None
    )
    max_holding = genome.max_holding_bars if exits else FALLBACK_MAX_HOLDING

    min_holding = genome.min_holding_bars
    if min_holding is not None and session_bars is not None:
        # Clamped, not dropped: a genome that asked to hold still asks to hold, for as long as
        # a session allows. Below two bars the floor cannot be expressed at all.
        ceiling = session_bars - 1
        min_holding = min(min_holding, ceiling) if ceiling >= 1 else None

    if min_holding is not None and max_holding is not None and min_holding >= max_holding:
        # The cap is the binding statement about how long a trade may run, so the floor yields.
        min_holding = max_holding - 1 if max_holding > 1 else None

    if max_holding == genome.max_holding_bars and min_holding == genome.min_holding_bars:
        return genome

    return Genome(
        entries=genome.entries,
        entry_ops=genome.entry_ops,
        exits=genome.exits,
        exit_ops=genome.exit_ops,
        stop=genome.stop,
        take_profit_pct=genome.take_profit_pct,
        max_holding_bars=max_holding,
        min_holding_bars=min_holding,
    )


def _describe_chain(slots: tuple[Slot, ...], ops: tuple[str, ...]) -> str:
    """Block names in a chain, joined by the operators between them."""
    text = BLOCKS[slots[0].block].name
    for slot, op in zip(slots[1:], ops, strict=True):
        text = f"{text} {op} {BLOCKS[slot.block].name}"
    return text


def describe(genome: Genome) -> str:
    """One line naming the conditions a genome composed, for the report.

    The evolved YAML says what the strategy *is*; this says what it was assembled *from*, which
    is the part a reader cannot recover from the YAML once the block names are gone.
    """
    parts = [f"entry: {_describe_chain(genome.entries, genome.entry_ops)}"]

    exits: list[str] = []
    if genome.exits:
        exits.append(_describe_chain(genome.exits, genome.exit_ops))
    if genome.stop is not None:
        exits.append(f"{genome.stop.kind} stop {genome.stop.value:.2f}")
    if genome.take_profit_pct is not None:
        exits.append(f"take profit {genome.take_profit_pct:.2f}%")
    # Bars, never days. The genes were always bar counts, and a "10d" that meant ten half-hour
    # bars would read as two calendar weeks to anyone glancing at an intraday report.
    if genome.min_holding_bars is not None:
        exits.append(f"hold at least {genome.min_holding_bars} bars")
    if genome.max_holding_bars is not None:
        exits.append(f"hold at most {genome.max_holding_bars} bars")
    parts.append(f"exit: {', '.join(exits)}")
    return "; ".join(parts)


def blocks_used(genome: Genome) -> tuple[str, ...]:
    """Names of every block the genome composed, in slot order."""
    return tuple(BLOCKS[slot.block].name for slot in (*genome.entries, *genome.exits))
