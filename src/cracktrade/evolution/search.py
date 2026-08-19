"""The genetic algorithm itself.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.4.

An elitist generational GA: tournament selection, per-position uniform crossover within each
variable-length condition chain, per-slot content mutation plus chain grow/shrink, and the best
few genomes carried forward untouched. It is written out rather than taken from a
framework because the alternative was carrying an untyped dependency through a strict-mypy
codebase and pinning tests for the tenth of it we would use -- and because the working agreement
is to verify library behaviour rather than assume it, which is a poor trade for two hundred lines
of operators whose behaviour is this easy to assert directly.

Everything stochastic comes from one seeded :class:`random.Random`. Two runs with the same seed,
the same chassis and the same history produce the same winner, and a test asserts it: an
irreproducible search is defect D12, and it is not less of one for being evolutionary.

The search is generational, and that is what makes it safe to distribute. A generation is bred in
full from the seeded RNG before any of it is scored, and no score is read until every genome in
the generation has one -- the same property that makes the optimizer's ``updating='deferred'``
independent of worker count (section 9.2). So a caller may pass ``evaluate_batch`` to score a
whole generation however it likes, and a search whose answer depended on the machine it ran on
remains the thing this module refuses to be.

Nothing here knows what a process is. The module's only concession to parallelism is that it
hands out generations rather than genomes; the pool that scores them lives in
:mod:`cracktrade.evolution.parallel`, and the accounting that keeps the trial count exact lives
in :meth:`~cracktrade.evolution.protocol.Fitness.evaluate_batch`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.evolution.blocks import BLOCKS
from cracktrade.evolution.genome import (
    COMBINATORS,
    MAX_CONDITIONS,
    MAX_HOLDING_GENE,
    MIN_ENTRY_CONDITIONS,
    MIN_EXIT_CONDITIONS,
    MIN_HOLDING_GENE,
    STOP_GENES,
    STOP_KINDS,
    TAKE_PROFIT_GENE,
    Genome,
    Slot,
    Stop,
    repair,
)
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from cracktrade.evolution.blocks import Gene

logger = get_logger(__name__)

#: Population members that survive to the next generation untouched. Small on purpose: elitism
#: keeps the best answer from being lost to an unlucky recombination, but a large elite turns a
#: search into a slow hill climb around whatever the first few generations happened to find.
DEFAULT_ELITES: Final = 2

#: Candidates drawn per tournament. Two is nearly random selection; five converges on the first
#: decent genome and stops exploring.
DEFAULT_TOURNAMENT: Final = 3

DEFAULT_POPULATION: Final = 40
DEFAULT_GENERATIONS: Final = 25

#: Probability that a pair of parents is recombined rather than copied.
DEFAULT_CROSSOVER_RATE: Final = 0.9

#: Probability that any one slot of a child is mutated.
DEFAULT_MUTATION_RATE: Final = 0.2

#: Given that a slot mutates, the chance it swaps its block outright rather than nudging the
#: numbers inside the block it already has. Structural jumps are the rarer move: they discard
#: everything the slot's genes had learned, so they are exploration, not refinement.
BLOCK_SWAP_RATE: Final = 0.3

#: Mutation step for a gene, as a share of its range.
GENE_SIGMA: Final = 0.15

#: Chance that an optional exit mechanism is present in a freshly drawn genome, and, per slot
#: beyond a chain's minimum length, the chance the chain keeps growing while it is being drawn.
OPTIONAL_PRESENCE: Final = 0.5

#: Given a chain is chosen to mutate structurally, and both a grow and a shrink are legal, the
#: chance the move is a shrink. Equal-weighted so the chain does not drift toward the arity
#: ceiling by construction of the operator itself -- see :func:`_mutate_chain` for the (accepted,
#: unsolved-here) drift risk that remains regardless.
STRUCTURE_SHRINK_RATE: Final = 0.5


@dataclass(frozen=True, slots=True)
class GaSettings:
    """How the search is run.

    Raises:
        ValueError: a setting is outside its usable range.
    """

    population: int = DEFAULT_POPULATION
    generations: int = DEFAULT_GENERATIONS
    elites: int = DEFAULT_ELITES
    tournament: int = DEFAULT_TOURNAMENT
    crossover_rate: float = DEFAULT_CROSSOVER_RATE
    mutation_rate: float = DEFAULT_MUTATION_RATE

    def __post_init__(self) -> None:
        if self.population < 2:
            msg = f"population must be at least 2, got {self.population}"
            raise ValueError(msg)
        if self.generations < 1:
            msg = f"generations must be at least 1, got {self.generations}"
            raise ValueError(msg)
        if not 0 <= self.elites < self.population:
            msg = f"elites must be between 0 and population-1, got {self.elites}"
            raise ValueError(msg)
        if not 2 <= self.tournament <= self.population:
            msg = f"tournament size must be between 2 and the population, got {self.tournament}"
            raise ValueError(msg)
        for name, rate in (
            ("crossover_rate", self.crossover_rate),
            ("mutation_rate", self.mutation_rate),
        ):
            if not 0.0 <= rate <= 1.0:
                msg = f"{name} must be between 0 and 1, got {rate}"
                raise ValueError(msg)

    @property
    def budget(self) -> int:
        """Genomes scored if nothing is ever a cache hit. The upper bound on the run's cost."""
        return self.population * (self.generations + 1)


@dataclass(frozen=True, slots=True)
class Generation:
    """What one generation produced, for the convergence trace."""

    index: int
    best_score: float
    mean_score: float
    feasible: int


@dataclass(slots=True)
class EvolutionOutcome:
    """The end state of a search."""

    best: Genome
    best_score: float
    history: tuple[Generation, ...] = ()
    #: Distinct genomes of the final population, best first. These are the *configurations* the
    #: overfitting probability in section 12.4 is computed across: PBO asks whether selecting
    #: the in-sample best beats selecting at random, which needs the alternatives that were on
    #: the table, not only the one that won.
    finalists: tuple[Genome, ...] = field(default_factory=tuple)


def run_evolution(
    fitness: Callable[[Genome], float],
    *,
    settings: GaSettings,
    seed: int,
    on_generation: Callable[[int, float], None] | None = None,
    control: RunControl = NO_CONTROL,
    evaluate_batch: Callable[[Sequence[Genome]], list[float]] | None = None,
    session_bars: int | None = None,
) -> EvolutionOutcome:
    """Evolve a population under ``fitness``, lower being better.

    Args:
        fitness: scores one genome in minimisation space, ``+inf`` for infeasible or failed.
        settings: population, generations, and operator rates.
        seed: seeds every stochastic decision in the run.
        on_generation: called with the generation index and the best score so far.
        control: progress and cooperative cancellation.
        evaluate_batch: scores a whole generation at once, returning one score per genome in
            order. It must be observationally identical to mapping ``fitness`` over the batch;
            given that, the result of the search does not depend on how the work was
            distributed. Defaults to doing exactly that, one genome at a time.
        session_bars: bars in one trading session, when the run is intraday. Bounds the minimum
            holding period the search may propose; see
            :func:`~cracktrade.evolution.genome.repair`.

    Raises:
        RunCancelled: the caller asked for the run to stop.
    """
    rng = random.Random(seed)

    def serially(batch: Sequence[Genome]) -> list[float]:
        return [fitness(genome) for genome in batch]

    evaluate = evaluate_batch if evaluate_batch is not None else serially

    population = [random_genome(rng, session_bars=session_bars) for _ in range(settings.population)]
    scores = evaluate(population)
    history = [_trace(0, scores)]

    logger.info(
        "evolving %d genome(s) over %d generation(s), budget %d evaluations",
        settings.population,
        settings.generations,
        settings.budget,
    )

    for generation in range(1, settings.generations + 1):
        # Between generations, never inside one: a generation stopped half-way has a population
        # that is part parent and part child, and nothing downstream could interpret it.
        control.raise_if_cancelled()

        population, scores = _advance(population, scores, evaluate, settings, rng, session_bars)
        history.append(_trace(generation, scores))

        best = min(scores)
        control.progress(
            f"generation {generation} of {settings.generations}",
            100.0 * generation / settings.generations,
        )
        if on_generation is not None:
            on_generation(generation, best)

    order = _ranking(scores)
    return EvolutionOutcome(
        best=population[order[0]],
        best_score=scores[order[0]],
        history=tuple(history),
        finalists=_distinct([population[index] for index in order]),
    )


def _advance(
    population: list[Genome],
    scores: list[float],
    evaluate: Callable[[Sequence[Genome]], list[float]],
    settings: GaSettings,
    rng: random.Random,
    session_bars: int | None,
) -> tuple[list[Genome], list[float]]:
    """One generation: keep the elite, breed the rest, score what changed.

    Every child is bred before any of them is scored, which is not merely how it reads: it is
    what lets ``evaluate`` distribute the generation without the outcome depending on how it
    chose to.
    """
    order = _ranking(scores)

    survivors = [population[index] for index in order[: settings.elites]]
    # Carried forward with their scores rather than re-scored. The fitness function is
    # deterministic, so re-scoring would return the same number at the cost of a backtest.
    survivor_scores = [scores[index] for index in order[: settings.elites]]

    children: list[Genome] = []
    while len(children) + len(survivors) < settings.population:
        parent_a = _tournament(population, scores, settings.tournament, rng)
        parent_b = _tournament(population, scores, settings.tournament, rng)
        child = (
            crossover(parent_a, parent_b, rng, session_bars=session_bars)
            if rng.random() < settings.crossover_rate
            else parent_a
        )
        children.append(mutate(child, rng, rate=settings.mutation_rate, session_bars=session_bars))

    return survivors + children, survivor_scores + evaluate(children)


def _ranking(scores: Sequence[float]) -> list[int]:
    """Population indices, best first.

    Sorted on the index as well as the score so that equal scores -- which are common, since
    every infeasible genome scores ``+inf`` -- resolve the same way on every run and on every
    platform.
    """
    return sorted(range(len(scores)), key=lambda index: (scores[index], index))


def _trace(index: int, scores: Sequence[float]) -> Generation:
    feasible = [score for score in scores if score != float("inf")]
    return Generation(
        index=index,
        best_score=min(scores),
        mean_score=sum(feasible) / len(feasible) if feasible else float("inf"),
        feasible=len(feasible),
    )


def _distinct(genomes: Sequence[Genome]) -> tuple[Genome, ...]:
    """Genomes with duplicates removed, order preserved."""
    seen: dict[Genome, None] = {}
    for genome in genomes:
        seen.setdefault(genome, None)
    return tuple(seen)


def _tournament(
    population: Sequence[Genome], scores: Sequence[float], size: int, rng: random.Random
) -> Genome:
    """Pick the best of ``size`` randomly drawn members."""
    drawn = [rng.randrange(len(population)) for _ in range(size)]
    best = min(drawn, key=lambda index: (scores[index], index))
    return population[best]


# --------------------------------------------------------------------------- operators


def random_genome(rng: random.Random, *, session_bars: int | None = None) -> Genome:
    """Draw a genome uniformly from the reachable space.

    ``session_bars`` is forwarded to :func:`~cracktrade.evolution.genome.repair`, which clamps a
    minimum holding period an intraday session could never satisfy. It is applied after the
    draw rather than by narrowing the gene, so the sequence of random numbers a seed produces is
    identical at every interval and a daily run is bit-for-bit what it was.
    """
    entries, entry_ops = _random_chain(rng, minimum=MIN_ENTRY_CONDITIONS)
    exits, exit_ops = _random_chain(rng, minimum=MIN_EXIT_CONDITIONS)
    return repair(
        Genome(
            entries=entries,
            entry_ops=entry_ops,
            exits=exits,
            exit_ops=exit_ops,
            stop=random_stop(rng) if rng.random() < OPTIONAL_PRESENCE else None,
            take_profit_pct=(
                _draw(TAKE_PROFIT_GENE, rng) if rng.random() < OPTIONAL_PRESENCE else None
            ),
            max_holding_bars=(
                int(_draw(MAX_HOLDING_GENE, rng)) if rng.random() < OPTIONAL_PRESENCE else None
            ),
            min_holding_bars=(
                int(_draw(MIN_HOLDING_GENE, rng)) if rng.random() < OPTIONAL_PRESENCE else None
            ),
        ),
        session_bars=session_bars,
    )


def _random_chain(rng: random.Random, *, minimum: int) -> tuple[tuple[Slot, ...], tuple[str, ...]]:
    """Draw a chain of between ``minimum`` and :data:`MAX_CONDITIONS` condition slots.

    ``minimum`` slots are always drawn; each slot past that is conditional on a coin flip at
    :data:`OPTIONAL_PRESENCE` -- the same geometric process that already governed whether the
    old fixed-shape genome's optional second entry condition or exit condition was present,
    generalised from a single yes/no choice to a chain that may keep growing up to the cap.
    """
    slots = [random_slot(rng) for _ in range(minimum)]
    ops: list[str] = []
    while len(slots) < MAX_CONDITIONS and rng.random() < OPTIONAL_PRESENCE:
        if slots:
            ops.append(rng.choice(COMBINATORS))
        slots.append(random_slot(rng))
    return tuple(slots), tuple(ops)


def random_slot(rng: random.Random) -> Slot:
    """Draw a block and a value for each of its genes."""
    index = rng.randrange(len(BLOCKS))
    return Slot(block=index, values=tuple(_draw(gene, rng) for gene in BLOCKS[index].genes))


def random_stop(rng: random.Random) -> Stop:
    """Draw a stop kind and a value inside that kind's range."""
    kind = rng.choice(STOP_KINDS)
    return Stop(kind=kind, value=_draw(STOP_GENES[kind], rng))


def crossover(
    first: Genome, second: Genome, rng: random.Random, *, session_bars: int | None = None
) -> Genome:
    """Uniform crossover, per position within each variable-length chain.

    See :func:`_crossover_chain` for the chain algorithm. The exit mechanisms are still
    fixed-shape fields, so they keep the original slot-for-slot exchange: the unit of exchange is
    a whole slot -- block choice together with its genes -- because the gene vector's length and
    meaning are properties of the block. Mixing a block index from one parent with gene values
    from the other would produce arities that do not match and numbers that mean something else.
    """
    entries, entry_ops = _crossover_chain(
        first.entries, first.entry_ops, second.entries, second.entry_ops, rng
    )
    exits, exit_ops = _crossover_chain(
        first.exits, first.exit_ops, second.exits, second.exit_ops, rng
    )
    return repair(
        Genome(
            entries=entries,
            entry_ops=entry_ops,
            exits=exits,
            exit_ops=exit_ops,
            stop=_pick(first.stop, second.stop, rng),
            take_profit_pct=_pick(first.take_profit_pct, second.take_profit_pct, rng),
            max_holding_bars=_pick(first.max_holding_bars, second.max_holding_bars, rng),
            min_holding_bars=_pick(first.min_holding_bars, second.min_holding_bars, rng),
        ),
        session_bars=session_bars,
    )


def _crossover_chain(
    first_slots: tuple[Slot, ...],
    first_ops: tuple[str, ...],
    second_slots: tuple[Slot, ...],
    second_ops: tuple[str, ...],
    rng: random.Random,
) -> tuple[tuple[Slot, ...], tuple[str, ...]]:
    """Uniform crossover over a chain whose two parents may have different lengths.

    A fixed-shape slot can be exchanged position by position because both parents always have
    that position. A variable-length chain cannot: the child needs a length before any
    position-wise exchange means anything. So one parent is chosen, by coin flip, as the
    *structure donor* -- the child's length is exactly that parent's length, never a length
    neither parent had -- and then every position is filled independently: from either parent if
    both have a slot there, otherwise from whichever parent does (which is always at least the
    donor, by construction). This keeps the "slot is the unit of exchange" principle the
    fixed-shape genome already used, just applied per position within a donor-sized window
    instead of at two fixed names.
    """
    donor_is_first = rng.random() < 0.5
    length = len(first_slots) if donor_is_first else len(second_slots)

    slots = tuple(
        _pick(first_slots[i], second_slots[i], rng)
        if i < len(first_slots) and i < len(second_slots)
        else (first_slots[i] if i < len(first_slots) else second_slots[i])
        for i in range(length)
    )
    ops = tuple(
        _pick(first_ops[i], second_ops[i], rng)
        if i < len(first_ops) and i < len(second_ops)
        else (first_ops[i] if i < len(first_ops) else second_ops[i])
        for i in range(max(0, length - 1))
    )
    return slots, ops


def mutate(
    genome: Genome, rng: random.Random, *, rate: float, session_bars: int | None = None
) -> Genome:
    """Perturb a genome, chain by chain, then field by field.

    Each chain independently mutates the content of every slot it already has, redraws each
    operator it already has, and may grow or shrink by exactly one slot -- see
    :func:`_mutate_chain`. The exit mechanisms -- stop, take-profit, holding bounds -- keep their
    original per-field toggle-between-present-and-absent behaviour, which is how the search
    reaches simpler strategies rather than only more elaborate ones.
    """
    entries, entry_ops = _mutate_chain(
        genome.entries, genome.entry_ops, rng, rate=rate, minimum=MIN_ENTRY_CONDITIONS
    )
    exits, exit_ops = _mutate_chain(
        genome.exits, genome.exit_ops, rng, rate=rate, minimum=MIN_EXIT_CONDITIONS
    )
    return repair(
        Genome(
            entries=entries,
            entry_ops=entry_ops,
            exits=exits,
            exit_ops=exit_ops,
            stop=_mutate_stop(genome.stop, rng, rate=rate),
            take_profit_pct=_mutate_optional_gene(
                genome.take_profit_pct, TAKE_PROFIT_GENE, rng, rate=rate
            ),
            max_holding_bars=_mutate_optional_int(
                genome.max_holding_bars, MAX_HOLDING_GENE, rng, rate=rate
            ),
            min_holding_bars=_mutate_optional_int(
                genome.min_holding_bars, MIN_HOLDING_GENE, rng, rate=rate
            ),
        ),
        session_bars=session_bars,
    )


def _mutate_chain(
    slots: tuple[Slot, ...],
    ops: tuple[str, ...],
    rng: random.Random,
    *,
    rate: float,
    minimum: int,
) -> tuple[tuple[Slot, ...], tuple[str, ...]]:
    """Mutate one condition chain: per-slot content, per-op value, and maybe its length.

    Growing and shrinking are mutually exclusive within one call, chosen with equal weight
    (:data:`STRUCTURE_SHRINK_RATE`) when both are legal at the current length -- the same
    reasoning :data:`BLOCK_SWAP_RATE` already applies to a single slot's content, generalised to
    the chain's shape. Shrinking removes a *randomly chosen* existing slot, not always the newest
    one: always pruning the latest addition would make every earlier slot permanent once added,
    the opposite of a search meant to keep revisiting structure.

    Known accepted risk, not solved here: nothing in this operator discourages growth, so a GA
    tends to drift toward :data:`~cracktrade.evolution.genome.MAX_CONDITIONS` over generations,
    since an extra condition rarely hurts in-sample fitness. A parsimony term belongs in the
    fitness function (``protocol.py``), not here.
    """
    slots_list = [_mutate_slot(slot, rng, rate=rate) for slot in slots]
    ops_list = [rng.choice(COMBINATORS) if rng.random() < rate else op for op in ops]

    can_grow = len(slots_list) < MAX_CONDITIONS
    can_shrink = len(slots_list) > minimum
    if (can_grow or can_shrink) and rng.random() < rate:
        shrink = can_shrink and (not can_grow or rng.random() < STRUCTURE_SHRINK_RATE)
        if shrink:
            index = rng.randrange(len(slots_list))
            del slots_list[index]
            if ops_list:
                del ops_list[min(index, len(ops_list) - 1)]
        else:
            had_neighbour = len(slots_list) >= 1
            slots_list.append(random_slot(rng))
            if had_neighbour:
                ops_list.append(rng.choice(COMBINATORS))

    return tuple(slots_list), tuple(ops_list)


def _mutate_slot(slot: Slot, rng: random.Random, *, rate: float) -> Slot:
    if rng.random() >= rate:
        return slot
    if rng.random() < BLOCK_SWAP_RATE:
        return random_slot(rng)
    block = BLOCKS[slot.block]
    return Slot(
        block=slot.block,
        values=tuple(
            _nudge(gene, value, rng) for gene, value in zip(block.genes, slot.values, strict=True)
        ),
    )


def _mutate_stop(stop: Stop | None, rng: random.Random, *, rate: float) -> Stop | None:
    if stop is None:
        return random_stop(rng) if rng.random() < rate else None
    if rng.random() >= rate:
        return stop
    if rng.random() < BLOCK_SWAP_RATE:
        # Swapping the kind means the value is measured in different units, so it is redrawn
        # rather than carried across: 8 is a reasonable percentage and an extreme ATR multiple.
        return random_stop(rng)
    return Stop(kind=stop.kind, value=_nudge(STOP_GENES[stop.kind], stop.value, rng))


def _mutate_optional_gene(
    value: float | None, gene: Gene, rng: random.Random, *, rate: float
) -> float | None:
    if value is None:
        return _draw(gene, rng) if rng.random() < rate else None
    if rng.random() < rate * BLOCK_SWAP_RATE:
        return None
    return _nudge(gene, value, rng) if rng.random() < rate else value


def _mutate_optional_int(
    value: int | None, gene: Gene, rng: random.Random, *, rate: float
) -> int | None:
    mutated = _mutate_optional_gene(None if value is None else float(value), gene, rng, rate=rate)
    return None if mutated is None else int(mutated)


def _pick[T](first: T, second: T, rng: random.Random) -> T:
    return first if rng.random() < 0.5 else second


def _draw(gene: Gene, rng: random.Random) -> float:
    return gene.clamp(rng.uniform(gene.low, gene.high))


def _nudge(gene: Gene, value: float, rng: random.Random) -> float:
    """Gaussian step scaled to the gene's range, clamped back inside it.

    Clamped rather than reflected or resampled: a bound is a statement that values outside it are
    not wanted, and a genome pushed against one is informative -- the same signal
    ``ParameterChange.at_bound`` reports for the optimizer.
    """
    return gene.clamp(value + rng.gauss(0.0, GENE_SIGMA * gene.span))
