"""Scoring a generation across processes, without the answer depending on how many.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.4.

The genetic algorithm is generational: every genome of a generation is bred from the seeded
:class:`random.Random` *before* any of them is scored, and no score is consumed until the whole
generation has one. That is the same structure that makes the optimizer's ``updating='deferred'``
safe at any worker count (section 9.2), so a generation may be scored in parallel and reassembled
by position with a bit-for-bit identical result.

What is deliberately *not* copied from section 9.2 is its concession. The optimizer reports
``counts_exact=False`` under ``workers=-1`` because scipy's pool mutates copies of the fitness
object in child processes and their tallies never come back. Evolution cannot afford that: the
count of distinct configurations is the trial count the deflated Sharpe divides by (section 12.3)
and the finalists are what the overfitting probability is computed across (section 12.4). So the
split of labour here is the other way round -- the parent renders, digests and deduplicates, and
the children do nothing but simulate and hand back a value. Every count stays in the one
authoritative :class:`~cracktrade.evolution.protocol.Fitness`, in the calling process.

**Spawn, not fork.** Forking a process that has already initialised NumPy's BLAS backend inherits
its threads in an unusable state, and the child deadlocks the first time it hits a matrix
operation. Spawn costs an interpreter start per worker, which is why the pool is created once per
run and reused across every generation rather than per generation -- and why more workers is not
reliably faster. :data:`EVALUATIONS_PER_WORKER` carries the measurements and the rule they imply.

Spawn also means a worker re-imports the module that started it, so a caller that runs a search
from a bare script needs the usual ``if __name__ == "__main__":`` guard. Console entry points --
which is how both the CLI and the API worker start -- already satisfy this, as they must for the
optimizer's own pool.
"""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from cracktrade.errors import CracktradeError, EvolutionError
from cracktrade.evolution.protocol import ScoreFailure, score_strategy
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from cracktrade.config import Strategy
    from cracktrade.evolution.protocol import EvolutionWindow, Fitness, ScoreOutcome
    from cracktrade.optimize.objective import Objective

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _WorkerState:
    """Everything a worker needs that does not change between candidates."""

    segments: tuple[EvolutionWindow, ...]
    objective: Objective
    min_trades: int


#: Evaluations of budget a worker must have to be worth starting, when ``-1`` is choosing.
#:
#: Measured, not guessed -- one more instance of the working agreement that an algorithm is not
#: to be trusted until it has been timed. A worker spends about 2.5 seconds importing vectorbt
#: and numba before it can score anything, and that is paid per worker, so a pool only pays for
#: itself if each worker then has real work to do. Often it does not: the dedup cache in
#: :class:`~cracktrade.evolution.protocol.Fitness` is served in the parent, and a converging
#: population repeats configurations heavily, so the *new* configurations in a late generation
#: are far fewer than the population and can easily be fewer than the workers.
#:
#: On a 32-core machine, scoring a 1500-bar history over four segments:
#:
#: =========== ======= ======== ========= =========
#: search      serial  8        16        32
#: =========== ======= ======== ========= =========
#: pop 40/5      9.6s    13.4s     24.2s     29.8s
#: pop 200/5    37.3s    17.2s     24.4s     44.9s
#: pop 100/30  102.4s    28.2s     32.3s     54.6s
#: pop 300/30  290.5s    57.5s     49.9s     66.3s
#: =========== ======= ======== ========= =========
#:
#: The best worker count tracks the *budget* rather than the core count, which is what this
#: constant expresses: the shortest search is fastest with no pool at all, and the longest is
#: still improving at sixteen. Pinning the BLAS thread count changed none of these numbers, so
#: the cost is interpreter start-up rather than the thread contention it might look like.
EVALUATIONS_PER_WORKER: Final = 500

#: Set once per child by :func:`_initialise`. A module global because that is the only channel a
#: pool initializer has to the task function.
_STATE: _WorkerState | None = None


def _initialise(
    segments: tuple[EvolutionWindow, ...], objective: Objective, min_trades: int
) -> None:
    """Give one worker the segments it will score every candidate against.

    Runs once per child rather than once per candidate, which is the point: the segments carry
    the market data, and shipping them with every genome would spend more on serialisation than
    the simulation saves.

    The BLAS thread count is deliberately left alone. Pinning it here would be too late anyway --
    NumPy is imported while the arguments to this function are being unpickled -- so doing it
    properly needs ``threadpoolctl``, and that is worth adding only if measurement shows the
    workers actually contending.
    """
    global _STATE
    _STATE = _WorkerState(segments=segments, objective=objective, min_trades=min_trades)


def _score_one(strategy: Strategy) -> ScoreOutcome:
    """Simulate one configuration in a worker process.

    A failure is *returned* rather than raised. It is a fact about one candidate that the parent
    has to record against its own tallies, and an exception crossing the pool boundary would
    instead take down the batch. Anything that is not a
    :class:`~cracktrade.errors.CracktradeError` does propagate: that is a defect in the engine
    rather than a verdict on a candidate, and it should stop the run loudly.
    """
    if _STATE is None:
        msg = "a scoring worker was used before it was initialised"
        raise EvolutionError(msg)
    try:
        return score_strategy(
            strategy,
            segments=_STATE.segments,
            objective=_STATE.objective,
            min_trades=_STATE.min_trades,
        )
    except CracktradeError as error:
        return ScoreFailure(error_type=type(error).__name__)


def plan_workers(workers: int, *, budget: int) -> int:
    """Decide how many processes a search of this size should use.

    ``-1`` means "choose for me", and the choice is made from the budget rather than from the
    core count: one worker per :data:`EVALUATIONS_PER_WORKER`, never more than there are cores.
    A short search therefore gets ``1``, which is not a degenerate pool but no pool at all -- the
    right answer when the search would finish before its workers had started.

    An explicit count is obeyed whatever the budget. A caller who names a number has answered
    this question themselves, and a benchmark that could not ask for two workers on a small
    search could not test the parallel path at all.

    Args:
        workers: processes to use, or ``-1`` to decide from the budget.
        budget: evaluations the search would make if nothing were ever a cache hit, from
            :attr:`~cracktrade.evolution.search.GaSettings.budget`.

    Raises:
        ValueError: the setting is neither ``-1`` nor a positive count.
    """
    if workers == -1:
        return min(os.cpu_count() or 1, max(1, budget // EVALUATIONS_PER_WORKER))
    if workers < 1:
        msg = f"workers must be -1 (chosen from the budget) or a positive count, got {workers}"
        raise ValueError(msg)
    return workers


@contextmanager
def evolution_pool(
    fitness: Fitness, *, workers: int
) -> Iterator[Callable[[Sequence[Strategy]], list[ScoreOutcome]]]:
    """A pool of scoring workers, and the batch scorer that feeds them.

    The yielded callable takes the distinct configurations of one generation and returns their
    outcomes *in the order it was given them*, which is what lets the caller merge them as though
    it had scored them itself.

    Args:
        fitness: supplies the segments, objective and trade floor the workers are initialised
            with. It is not itself sent anywhere -- its counts stay in this process.
        workers: processes to use, already resolved by :func:`plan_workers`.

    Raises:
        ValueError: ``workers`` is not a positive count.
    """
    if workers < 1:
        msg = f"a pool needs a positive worker count, got {workers}"
        raise ValueError(msg)
    logger.info("scoring candidates across %d worker process(es)", workers)

    executor = ProcessPoolExecutor(
        max_workers=workers,
        # Spawn for the reason in the module docstring, and by the same choice the migration
        # tests make: a fresh interpreter inherits no half-initialised native state.
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_initialise,
        initargs=(fitness.segments, fitness.objective, fitness.min_trades),
    )

    def score_many(strategies: Sequence[Strategy]) -> list[ScoreOutcome]:
        # ``map`` yields in submission order however the work was actually scheduled, so the
        # result does not depend on which worker finished first.
        return list(executor.map(_score_one, strategies))

    try:
        yield score_many
    finally:
        # Waited on rather than abandoned: a cancelled run should leave no orphans behind, and
        # the generation in flight is at most a few seconds of work.
        executor.shutdown(wait=True, cancel_futures=True)
