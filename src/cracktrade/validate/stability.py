"""Whether the optimum is a plateau or a spike.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.5.

Differential evolution returns a point. Financially, a usable optimum is a **plateau**: nudge a
parameter and the result barely moves, because the edge is a property of the market rather than
of that exact number. A spike that collapses under a 10% nudge is a curve fit, and in a point
report the two look identical.

This is the cheapest honest overfitting tell available. It costs one backtest per perturbation
and needs no extra data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cracktrade.backtest import extract_metrics, run_variants
from cracktrade.domain import StabilityPoint, StabilityReport
from cracktrade.errors import CracktradeError
from cracktrade.optimize.discovery import inject
from cracktrade.optimize.objective import INFEASIBLE
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cracktrade.config import Strategy
    from cracktrade.optimize.discovery import Parameter
    from cracktrade.optimize.objective import Objective
    from cracktrade.optimize.windows import TrainWindow

#: Relative nudges applied to each parameter in turn.
DEFAULT_PERTURBATIONS = (-0.2, -0.1, 0.1, 0.2)


def stability_surface(
    strategy: Strategy,
    parameters: Sequence[Parameter],
    values: Sequence[float],
    train: TrainWindow,
    objective: Objective,
    *,
    perturbations: Sequence[float] = DEFAULT_PERTURBATIONS,
) -> StabilityReport:
    """Perturb each optimized parameter in turn and re-score on the training window.

    Scored on *train* deliberately. This asks whether the optimum is a real feature of the
    landscape the search explored, which is a question about that landscape. Using the test
    window would both answer a different question and spend the one evaluation it is owed.
    """
    baseline = _score(strategy, parameters, values, train, objective)
    points: list[StabilityPoint] = []

    for index, parameter in enumerate(parameters):
        for multiplier in perturbations:
            nudged = list(values)
            moved = values[index] * (1.0 + multiplier)
            if parameter.is_integer:
                # Round away from the baseline, so a small nudge to a small integer still moves
                # it. Rounding to nearest would make a 10% nudge to a 3-day window a no-op and
                # report perfect stability that was never tested.
                moved = _round_away(values[index], moved)
            nudged[index] = moved

            score = _score(strategy, parameters, nudged, train, objective)
            points.append(
                StabilityPoint(
                    path=parameter.path,
                    multiplier=multiplier,
                    value=moved,
                    score=score,
                    degradation=_degradation(baseline, score),
                )
            )

    return StabilityReport(baseline_score=baseline, points=tuple(points))


def _round_away(baseline: float, moved: float) -> float:
    """Round an integer perturbation away from the baseline, never onto it."""
    rounded = round(moved)
    if rounded == round(baseline):
        rounded = round(baseline) + (1 if moved > baseline else -1)
    return float(rounded)


def _score(
    strategy: Strategy,
    parameters: Sequence[Parameter],
    values: Sequence[float],
    train: TrainWindow,
    objective: Objective,
) -> float:
    """Objective value of one configuration on the training window."""
    try:
        candidate = build_strategy(inject(strategy, parameters, values))
        risk_free = candidate.execution.risk_free_rate
        scores = [
            objective(extract_metrics(simulation.portfolio, risk_free_rate=risk_free))
            for simulation in run_variants(candidate, train.data)
        ]
    except CracktradeError:
        return INFEASIBLE
    return min(scores) if scores else INFEASIBLE


def _degradation(baseline: float, perturbed: float) -> float:
    """Share of the objective lost, in ``[0, 1]``.

    Scores are in minimisation space and are usually negative, so this works on magnitudes: how
    much of the baseline's edge survived the nudge. An infeasible perturbation loses all of it.
    """
    if perturbed == INFEASIBLE:
        return 1.0
    if baseline >= 0:
        # The baseline had no edge to lose, so there is nothing to degrade.
        return 0.0
    lost = (perturbed - baseline) / abs(baseline)
    return float(min(max(lost, 0.0), 1.0))
