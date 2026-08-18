"""The genetic algorithm itself.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.4.

An elitist generational GA: tournament selection, per-slot uniform crossover, per-slot mutation,
and the best few genomes carried forward untouched. It is written out rather than taken from a
framework because the alternative was carrying an untyped dependency through a strict-mypy
codebase and pinning tests for the tenth of it we would use -- and because the working agreement
is to verify library behaviour rather than assume it, which is a poor trade for two hundred lines
of operators whose behaviour is this easy to assert directly.

Everything stochastic comes from one seeded :class:`random.Random`. Two runs with the same seed,
the same chassis and the same history produce the same winner, and a test asserts it: an
irreproducible search is defect D12, and it is not less of one for being evolutionary.

The search is deliberately *not* parallelised. The optimizer's ``workers=-1`` survives only
because differential evolution with ``updating='deferred'`` evaluates a whole generation
synchronously, so the result is worker-count independent (section 9.2). Nothing here has been
proven to have that property yet, and a search whose answer depends on the machine it ran on
would be worse than a slow one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.evolution.blocks import BLOCKS
from cracktrade.evolution.genome import (
    COMBINATORS,
    MAX_HOLDING_GENE,
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

#: Chance that an optional exit mechanism is present in a freshly drawn genome.
OPTIONAL_PRESENCE: Final = 0.5


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
) -> EvolutionOutcome:
    """Evolve a population under ``fitness``, lower being better.

    Args:
        fitness: scores one genome in minimisation space, ``+inf`` for infeasible or failed.
        settings: population, generations, and operator rates.
        seed: seeds every stochastic decision in the run.
        on_generation: called with the generation index and the best score so far.
        control: progress and cooperative cancellation.

    Raises:
        RunCancelled: the caller asked for the run to stop.
    """
    rng = random.Random(seed)

    population = [random_genome(rng) for _ in range(settings.population)]
    scores = [fitness(genome) for genome in population]
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

        population, scores = _advance(population, scores, fitness, settings, rng)
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
    fitness: Callable[[Genome], float],
    settings: GaSettings,
    rng: random.Random,
) -> tuple[list[Genome], list[float]]:
    """One generation: keep the elite, breed the rest, score what changed."""
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
            crossover(parent_a, parent_b, rng)
            if rng.random() < settings.crossover_rate
            else parent_a
        )
        children.append(mutate(child, rng, rate=settings.mutation_rate))

    return survivors + children, survivor_scores + [fitness(child) for child in children]


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


def random_genome(rng: random.Random) -> Genome:
    """Draw a genome uniformly from the reachable space."""
    return repair(
        Genome(
            entry_a=random_slot(rng),
            entry_b=random_slot(rng) if rng.random() < OPTIONAL_PRESENCE else None,
            combinator=rng.choice(COMBINATORS),
            exit_condition=random_slot(rng) if rng.random() < OPTIONAL_PRESENCE else None,
            stop=random_stop(rng) if rng.random() < OPTIONAL_PRESENCE else None,
            take_profit_pct=(
                _draw(TAKE_PROFIT_GENE, rng) if rng.random() < OPTIONAL_PRESENCE else None
            ),
            max_holding_days=(
                int(_draw(MAX_HOLDING_GENE, rng)) if rng.random() < OPTIONAL_PRESENCE else None
            ),
            min_holding_days=(
                int(_draw(MIN_HOLDING_GENE, rng)) if rng.random() < OPTIONAL_PRESENCE else None
            ),
        )
    )


def random_slot(rng: random.Random) -> Slot:
    """Draw a block and a value for each of its genes."""
    index = rng.randrange(len(BLOCKS))
    return Slot(block=index, values=tuple(_draw(gene, rng) for gene in BLOCKS[index].genes))


def random_stop(rng: random.Random) -> Stop:
    """Draw a stop kind and a value inside that kind's range."""
    kind = rng.choice(STOP_KINDS)
    return Stop(kind=kind, value=_draw(STOP_GENES[kind], rng))


def crossover(first: Genome, second: Genome, rng: random.Random) -> Genome:
    """Uniform crossover over slots.

    The unit of exchange is a whole slot -- block choice together with its genes -- because the
    gene vector's length and meaning are properties of the block. Mixing a block index from one
    parent with gene values from the other would produce arities that do not match and numbers
    that mean something else.
    """
    return repair(
        Genome(
            entry_a=_pick(first.entry_a, second.entry_a, rng),
            entry_b=_pick(first.entry_b, second.entry_b, rng),
            combinator=_pick(first.combinator, second.combinator, rng),
            exit_condition=_pick(first.exit_condition, second.exit_condition, rng),
            stop=_pick(first.stop, second.stop, rng),
            take_profit_pct=_pick(first.take_profit_pct, second.take_profit_pct, rng),
            max_holding_days=_pick(first.max_holding_days, second.max_holding_days, rng),
            min_holding_days=_pick(first.min_holding_days, second.min_holding_days, rng),
        )
    )


def mutate(genome: Genome, rng: random.Random, *, rate: float) -> Genome:
    """Perturb a genome slot by slot.

    A condition slot mutates in one of two ways: it swaps its block, discarding genes that no
    longer mean anything, or it nudges the numbers inside the block it already has. The optional
    slots -- second entry condition, exit condition, stop, take-profit, holding bounds -- toggle
    between present and absent, which is how the search reaches simpler strategies rather than
    only more elaborate ones.
    """
    return repair(
        Genome(
            entry_a=_mutate_slot(genome.entry_a, rng, rate=rate),
            entry_b=_mutate_optional_slot(genome.entry_b, rng, rate=rate),
            combinator=(rng.choice(COMBINATORS) if rng.random() < rate else genome.combinator),
            exit_condition=_mutate_optional_slot(genome.exit_condition, rng, rate=rate),
            stop=_mutate_stop(genome.stop, rng, rate=rate),
            take_profit_pct=_mutate_optional_gene(
                genome.take_profit_pct, TAKE_PROFIT_GENE, rng, rate=rate
            ),
            max_holding_days=_mutate_optional_int(
                genome.max_holding_days, MAX_HOLDING_GENE, rng, rate=rate
            ),
            min_holding_days=_mutate_optional_int(
                genome.min_holding_days, MIN_HOLDING_GENE, rng, rate=rate
            ),
        )
    )


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


def _mutate_optional_slot(slot: Slot | None, rng: random.Random, *, rate: float) -> Slot | None:
    if slot is None:
        return random_slot(rng) if rng.random() < rate else None
    if rng.random() < rate * BLOCK_SWAP_RATE:
        return None
    return _mutate_slot(slot, rng, rate=rate)


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
