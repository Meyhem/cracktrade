"""The differential-evolution driver.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.2.

Two fixes over the legacy search matter more than the rest:

* **Reproducibility.** Legacy passed no ``seed`` while running ``workers=-1`` (defect D12), so
  the same command produced a different strategy on consecutive runs and no result could be
  verified. The seed is mandatory here, and with ``updating='deferred'`` the population is
  evaluated synchronously per generation, which makes the result independent of worker count.
* **Integer parameters.** Continuous bounds over an integer parameter give a piecewise-flat
  landscape -- a window of 20.0 and one of 20.4 are the same strategy -- which destroys the
  differential signal DE relies on (defect D13). scipy's ``integrality`` makes the search respect
  the lattice directly instead of rounding after the fact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import numpy as np
from scipy.optimize import differential_evolution

from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.errors import RunCancelled
from cracktrade.log import get_logger
from cracktrade.optimize.objective import INFEASIBLE

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

    from cracktrade.optimize.discovery import Parameter

logger = get_logger(__name__)

#: scipy differential-evolution settings, ported from the legacy optimizer.
STRATEGY: Final = "best1bin"
POPSIZE: Final = 15
MUTATION: Final = (0.5, 1.0)
RECOMBINATION: Final = 0.7

#: A run in which more than this share of candidates failed is reported as suspect: it means the
#: bounds are producing invalid configurations rather than that the search is exploring.
FAILURE_WARNING_FRACTION: Final = 0.2


@dataclass(slots=True)
class SearchDiagnostics:
    """What the search actually did, as opposed to what it was asked to do."""

    evaluations: int = 0
    failures: int = 0
    infeasible: int = 0
    #: Whether ``failures`` and ``infeasible`` were observable. A parallel search mutates copies
    #: of the fitness object in child processes, so its tallies do not return; reporting zero
    #: for them would be a confident lie about a run that may have failed throughout.
    counts_exact: bool = True
    seed: int = 0
    workers: int = 1
    objective: str = ""
    message: str = ""
    elapsed_seconds: float = 0.0
    failure_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def failure_fraction(self) -> float:
        """Share of evaluations that raised."""
        return self.failures / self.evaluations if self.evaluations else 0.0

    @property
    def looks_unhealthy(self) -> bool:
        """Whether enough candidates failed that the search was not really searching."""
        return self.failure_fraction > FAILURE_WARNING_FRACTION

    @property
    def most_common_failure(self) -> str | None:
        """The exception type that failed most often, if any did."""
        if not self.failure_reasons:
            return None
        return max(self.failure_reasons, key=lambda name: self.failure_reasons[name])


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """The best parameter vector found, and how it was found."""

    values: tuple[float, ...]
    score: float
    diagnostics: SearchDiagnostics


def run_search(
    score: Callable[[npt.NDArray[np.float64]], float],
    parameters: Sequence[Parameter],
    *,
    epochs: int,
    seed: int,
    workers: int,
    diagnostics: SearchDiagnostics,
    on_generation: Callable[[int, float], None] | None = None,
    control: RunControl = NO_CONTROL,
) -> SearchOutcome:
    """Search ``parameters`` with differential evolution, minimising ``score``.

    ``on_generation`` is called once per completed generation with the generation number and
    scipy's convergence measure, so a caller can show progress without this module knowing
    anything about terminals.

    ``control`` adds the same for long-running callers: a percentage of the epoch budget, and
    a cancellation check between generations. Verified against scipy 1.18: returning ``True``
    from the callback halts the search and sets ``result.message`` to "callback function
    requested stop early", which is why cancellation is detected from a flag here rather than
    inferred from that string.
    """
    generation = 0
    cancelled = False

    def report(_vector: npt.NDArray[np.float64], convergence: float = 0.0) -> bool:
        nonlocal generation, cancelled
        generation += 1
        if on_generation is not None:
            on_generation(generation, float(convergence))
        control.progress(
            f"generation {generation} of {epochs}", 100.0 * generation / max(epochs, 1)
        )
        cancelled = control.cancelled
        # scipy halts the search when a callback returns True. Stopping between generations
        # means the population is whole and nothing is half-evaluated.
        return cancelled

    bounds = [(parameter.low, parameter.high) for parameter in parameters]
    integrality = np.array([parameter.is_integer for parameter in parameters], dtype=bool)

    result = differential_evolution(
        score,
        bounds=bounds,
        strategy=STRATEGY,
        popsize=POPSIZE,
        mutation=MUTATION,
        recombination=RECOMBINATION,
        maxiter=epochs,
        seed=seed,
        workers=workers,
        # Deferred updating evaluates a whole generation before selecting, which is what makes
        # the result identical at workers=1 and workers=-1.
        updating="deferred",
        polish=False,
        init=_initial_population(parameters, seed),
        integrality=integrality,
        # Always installed now: the callback carries cancellation as well as progress, and a
        # search that could not be stopped would hold a worker for its full budget.
        callback=report,
    )

    if cancelled:
        raise RunCancelled("the search was cancelled")

    diagnostics.message = str(result.message)
    # scipy's own count, authoritative however the work was distributed.
    diagnostics.evaluations = int(result.nfev)
    logger.debug("search finished: %s", result.message)

    return SearchOutcome(
        values=tuple(float(value) for value in result.x),
        score=float(result.fun),
        diagnostics=diagnostics,
    )


def _initial_population(parameters: Sequence[Parameter], seed: int) -> npt.NDArray[np.float64]:
    """Latin hypercube sampling, with the user's own configuration as member zero.

    Seeding the population with the baseline guarantees the search can never return something
    worse than what the user already had: the configuration they wrote is in the running from
    the first generation.
    """
    dimensions = len(parameters)
    members = max(POPSIZE * dimensions, 5)
    rng = np.random.default_rng(seed)

    population = np.empty((members, dimensions), dtype=np.float64)
    population[0] = [parameter.value for parameter in parameters]

    for column, parameter in enumerate(parameters):
        # One stratified sample per remaining member, shuffled independently per dimension --
        # that is what makes it a Latin hypercube rather than a grid.
        strata = (rng.permutation(members - 1) + rng.random(members - 1)) / (members - 1)
        population[1:, column] = parameter.low + strata * (parameter.high - parameter.low)

    return np.clip(
        population,
        [parameter.low for parameter in parameters],
        [parameter.high for parameter in parameters],
    )


def evaluation_budget(parameters: Sequence[Parameter], epochs: int) -> int:
    """Roughly how many candidates the search will score.

    Used for the trial count that spec section 12.3 deflates the reported Sharpe by: the
    maximum over N trials is inflated even when there is no edge, so N has to be known.
    """
    return (epochs + 1) * POPSIZE * max(len(parameters), 1)


__all__ = [
    "FAILURE_WARNING_FRACTION",
    "INFEASIBLE",
    "MUTATION",
    "POPSIZE",
    "RECOMBINATION",
    "STRATEGY",
    "SearchDiagnostics",
    "SearchOutcome",
    "evaluation_budget",
    "run_search",
]
