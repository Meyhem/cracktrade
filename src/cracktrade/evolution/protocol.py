"""What evolution is allowed to see, and what it is scored on.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.5.

This is the correctness centrepiece of the feature, and it exists because structure search makes
selection bias worse than parameter search ever did. Section 9.4 keeps one test window away from
a search of a few thousand parameter vectors. Here the search also chooses *which conditions the
strategy is made of*, so the number of distinct strategies considered is far larger and the
maximum-of-noise problem grows with it. Nothing about the four look-ahead layers helps: every
candidate is causal, and the winner can still be a coincidence.

Three separations do the work.

**The holdout is untouchable.** The last share of history is split off before the first genome is
drawn and is evaluated exactly once, on the winner, after every choice is final. It is a
:class:`~cracktrade.optimize.windows.TestWindow`, and the fitness function accepts a tuple of
:class:`EvolutionWindow` and nothing else, so handing evolution the holdout fails mypy rather
than silently producing a fitted headline (the section 2.5 discipline, applied to a second
search).

**Fitness is a typical segment, not a total.** The evolution region is cut into contiguous
segments and a genome is scored by the *median* of its per-segment objective. A mean would let
one spectacular segment carry a genome that lost money in the other three, which is precisely
the genome a structure search is most likely to find and least likely to be right about. The
median asks a harder question: how did this strategy do in an ordinary stretch of history.

**The trade floor is one constraint over the whole region.** Segments are short, so a
per-segment floor at section 9.3's default would reject nearly everything for a reason that has
nothing to do with the market. The floor is applied once to the summed trade count, sized from
the whole evolution region, and the per-segment objective is bound with no floor of its own.
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cracktrade.backtest import extract_metrics, run_simulation
from cracktrade.config import dump_strategy
from cracktrade.errors import CracktradeError, EvolutionError
from cracktrade.evolution.genome import render
from cracktrade.optimize.objective import INFEASIBLE
from cracktrade.optimize.windows import TestWindow, TrainWindow

if TYPE_CHECKING:
    from cracktrade.config import Strategy
    from cracktrade.data import MarketData
    from cracktrade.evolution.genome import Chassis, Genome
    from cracktrade.optimize.objective import Objective

#: Contiguous segments the evolution region is cut into. Four rather than three because the
#: probability of backtest overfitting (section 12.4) partitions the slices into halves every
#: possible way and needs at least four to produce any combinations at all.
DEFAULT_SEGMENTS = 4

#: Share of history reserved for the single final evaluation.
DEFAULT_HOLDOUT_FRACTION = 0.2

#: Bars a segment or the holdout must have before it can support any conclusion.
DEFAULT_MIN_SEGMENT_BARS = 60


@dataclass(frozen=True, slots=True)
class EvolutionWindow:
    """One scored stretch of the evolution region.

    A distinct type from :class:`~cracktrade.optimize.windows.TrainWindow` for two reasons. It
    carries a warm-up prefix, which ``TrainWindow`` documents itself as never having; and being
    distinct is what lets the fitness function's signature refuse a
    :class:`~cracktrade.optimize.windows.TestWindow` at type-check time.

    Attributes:
        data: the segment's bars, preceded by enough earlier bars to warm up any indicator the
            block library can produce. Those bars are past data relative to every scored bar.
        offset: how many leading bars are warm-up.
    """

    data: MarketData
    offset: int

    @property
    def scored_bars(self) -> int:
        """Bars this segment's metrics actually cover."""
        return len(self.data) - self.offset


@dataclass(frozen=True, slots=True)
class Regions:
    """How one history is divided for an evolution run.

    Attributes:
        segments: the windows fitness is measured over.
        region: the whole evolution region as a train window, for the stability surface
            (section 12.5), which asks how the winner behaves in the neighbourhood of the data
            it was selected on.
        holdout: the terminal window, seen once, by the winner only.
    """

    segments: tuple[EvolutionWindow, ...]
    region: TrainWindow
    holdout: TestWindow

    @property
    def evolution_bars(self) -> int:
        """Bars in the evolution region, warm-up included."""
        return len(self.region.data)


def split_for_evolution(
    data: MarketData,
    *,
    warmup: int,
    segments: int = DEFAULT_SEGMENTS,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    min_segment_bars: int = DEFAULT_MIN_SEGMENT_BARS,
) -> Regions:
    """Divide ``data`` into evolution segments and one terminal holdout.

    The division is positional and never shuffled, and the holdout is always the *most recent*
    stretch: a holdout taken from the middle would be surrounded by data the search had seen,
    and the regime either side of it would leak in through nothing more exotic than the fact
    that markets are autocorrelated.

    Args:
        data: the full history.
        warmup: bars any candidate could need, from
            :func:`~cracktrade.evolution.blocks.library_warmup`.
        segments: how many scoring windows the evolution region is cut into.
        holdout_fraction: share of history reserved for the final evaluation.
        min_segment_bars: refuse a division whose segments or holdout are smaller than this.

    Raises:
        EvolutionError: the history cannot support the requested division.
    """
    if segments < 1:
        msg = f"segments must be at least 1, got {segments}"
        raise EvolutionError(msg)
    if not 0.0 < holdout_fraction < 1.0:
        msg = f"holdout_fraction must be between 0 and 1, got {holdout_fraction}"
        raise EvolutionError(msg)

    bars = len(data)
    holdout_bars = int(bars * holdout_fraction)
    evolution_bars = bars - holdout_bars

    if holdout_bars < min_segment_bars:
        msg = (
            f"a {holdout_fraction:.0%} holdout of {bars} bars leaves {holdout_bars} bars to "
            f"judge the winner on, below the {min_segment_bars} required. A holdout this small "
            f"cannot support a conclusion; widen the date range"
        )
        raise EvolutionError(msg)

    scored = evolution_bars - warmup
    if scored < segments * min_segment_bars:
        msg = (
            f"{bars} bars leave {max(scored, 0)} scorable bars for {segments} evolution "
            f"segment(s) after the {warmup}-bar warm-up the block library requires, below the "
            f"{segments * min_segment_bars} needed. Widen the date range, or use fewer segments"
        )
        raise EvolutionError(msg)

    length = scored // segments
    windows: list[EvolutionWindow] = []
    for index in range(segments):
        start = warmup + index * length
        # The last segment absorbs the remainder, so no bars are silently discarded.
        stop = evolution_bars if index == segments - 1 else start + length
        windows.append(EvolutionWindow(data=data.slice(start - warmup, stop), offset=warmup))

    return Regions(
        segments=tuple(windows),
        region=TrainWindow(data=data.head(evolution_bars)),
        holdout=TestWindow(data=data.slice(evolution_bars - warmup, bars), offset=warmup),
    )


@dataclass(frozen=True, slots=True)
class Scored:
    """One distinct configuration's result on the evolution segments.

    Attributes:
        score: the fitness the search minimises.
        segment_scores: the objective on each segment, in order.
        segment_sharpes: annualised Sharpe on each segment, in order. Their spread across
            candidates is what section 12.3 deflates the winner's Sharpe by.
        trades: closed trades summed across every segment.
    """

    score: float
    segment_scores: tuple[float, ...]
    segment_sharpes: tuple[float, ...]
    trades: int


@dataclass(slots=True)
class Fitness:
    """Scores a genome on the evolution segments, and remembers what it has already scored.

    The cache is not only an optimization. Elites survive generations untouched and crossover
    re-creates configurations that have been seen before, so without deduplication the trial
    count fed to the deflated Sharpe would be inflated by repetition -- which *understates* the
    deflation, because it treats one candidate examined ten times as ten independent looks.
    Distinct rendered configurations is the number section 12.3 wants, and it is what
    :attr:`trials` reports.

    ``segments`` is typed :class:`EvolutionWindow` and nothing else. Handing this the holdout is
    a type error, which is what keeps "the holdout is seen once" a property of the code rather
    than a convention someone has to remember.
    """

    chassis: Chassis
    segments: tuple[EvolutionWindow, ...]
    objective: Objective
    min_trades: int
    evaluations: int = 0
    failures: int = 0
    infeasible: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    results: dict[str, Scored] = field(default_factory=dict)

    def __call__(self, genome: Genome) -> float:
        self.evaluations += 1
        try:
            strategy = render(genome, self.chassis)
        except CracktradeError as error:
            self._record_failure(error)
            return INFEASIBLE

        digest = configuration_digest(strategy)
        cached = self.results.get(digest)
        if cached is not None:
            return cached.score

        try:
            scored = self._score(strategy)
        except CracktradeError as error:
            self._record_failure(error)
            return INFEASIBLE

        self.results[digest] = scored
        if scored.score == INFEASIBLE:
            self.infeasible += 1
        return scored.score

    @property
    def trials(self) -> int:
        """Distinct configurations scored. The trial count section 12.3 deflates by."""
        return len(self.results)

    @property
    def trial_sharpes(self) -> tuple[float, ...]:
        """Every distinct candidate's typical annualised Sharpe across the segments."""
        return tuple(
            statistics.median(result.segment_sharpes)
            for result in self.results.values()
            if result.segment_sharpes
        )

    @property
    def most_common_failure(self) -> str | None:
        """The exception type that failed most often, if any did."""
        if not self.failure_reasons:
            return None
        return max(self.failure_reasons.items(), key=lambda item: (item[1], item[0]))[0]

    def _score(self, strategy: Strategy) -> Scored:
        scores: list[float] = []
        sharpes: list[float] = []
        trades = 0

        for window in self.segments:
            simulation = run_simulation(strategy, window.data)
            metrics = extract_metrics(
                simulation.portfolio,
                risk_free_rate=strategy.execution.risk_free_rate,
                offset=window.offset,
            )
            scores.append(self.objective(metrics))
            sharpes.append(metrics.sharpe_ratio)
            trades += metrics.total_trades

        # One feasibility constraint, over the whole region. A candidate that traded too little
        # to be evidence about anything is rejected outright rather than discounted -- the
        # section 9.3 argument, unchanged: a three-trade result carries no information however
        # large its return.
        score = INFEASIBLE if trades < self.min_trades else statistics.median(scores)

        return Scored(
            score=score,
            segment_scores=tuple(scores),
            segment_sharpes=tuple(sharpes),
            trades=trades,
        )

    def _record_failure(self, error: CracktradeError) -> None:
        """Count a candidate that could not be built or simulated.

        Unlike the optimizer's equivalent, any failure here is a defect: the block library is
        curated and :func:`~cracktrade.evolution.genome.repair` is meant to make every reachable
        genome renderable, so a non-zero count means one of those two is wrong. The run
        continues -- an aborted search reports nothing at all -- but the count reaches the
        result and the report says so.
        """
        self.failures += 1
        name = type(error).__name__
        self.failure_reasons[name] = self.failure_reasons.get(name, 0) + 1


def configuration_digest(strategy: Strategy) -> str:
    """A stable identity for one rendered configuration.

    Taken over the serialised strategy rather than over the genome, because two genomes that
    differ only below the rendering's rounding are the same strategy and must count as one
    trial. The digest is what makes "distinct configurations scored" a fact rather than an
    estimate.
    """
    return hashlib.sha256(dump_strategy(strategy).encode("utf-8")).hexdigest()
