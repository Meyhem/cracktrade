"""What the optimizer is trying to maximise.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.3.

The default was changed from the legacy objective after the audit (finding B2). Legacy scored

    fitness = pnl * (1 +/- max_drawdown),  with  pnl *= 0.1  when trades < 5

which has four independent problems. Raw PnL is scale-dependent and outlier-dominated, so one
lucky trade outranks a hundred consistent ones. The small-sample penalty is a *multiplier*, so
any fluke bigger than ten times defeats it. The drawdown term scales a typical -20% drawdown by
0.8, which barely disciplines risk at all. And the maximum is taken across variants inside every
evaluation, so the in-sample optimum is the best of ``evaluations x entries x exits`` draws.

The default here is Calmar -- annualised return over maximum drawdown -- with the trade-count
floor as a **hard feasibility constraint** rather than a discount. A three-trade result carries
no information no matter how large its PnL, so it is rejected outright.

Scores are returned in scipy's *minimisation* space: lower is better, and ``+inf`` means
infeasible or failed.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cracktrade.domain import Metrics

#: Returned for a candidate that failed or is infeasible. Strictly worse than any real score.
#:
#: **[FIX]** Legacy returned ``0.0`` here (defect D9). Since a genuinely losing strategy scores
#: negative, an *invalid* configuration outranked a merely bad one, and the search was actively
#: drawn toward regions of the parameter space that do not even parse.
INFEASIBLE: Final = math.inf

#: Closed trades a candidate must produce to be considered at all.
DEFAULT_MIN_TRADES: Final = 20


class Objective(Protocol):
    """Scores one candidate's train-window metrics. Lower is better."""

    def __call__(self, metrics: Metrics, /) -> float: ...


def calmar(metrics: Metrics, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """Annualised return over maximum drawdown. The default.

    Risk-adjusted, scale-free, and expressed in the units an investor actually feels: return per
    unit of the worst peak-to-trough loss they would have had to sit through.
    """
    if metrics.total_trades < min_trades:
        return INFEASIBLE
    drawdown = abs(metrics.max_drawdown_pct)
    if drawdown == 0.0:
        # No drawdown at all over a real sample means almost no exposure. Rank it by return so
        # the search is not attracted to strategies that avoid risk by refusing to trade.
        return -metrics.cagr_pct
    return -(metrics.cagr_pct / drawdown)


def sortino(metrics: Metrics, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """Downside-deviation-adjusted return. Smoother for the search than Calmar."""
    if metrics.total_trades < min_trades:
        return INFEASIBLE
    return -metrics.sortino_ratio


def sharpe(metrics: Metrics, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """Excess return per unit of total volatility."""
    if metrics.total_trades < min_trades:
        return INFEASIBLE
    return -metrics.sharpe_ratio


def legacy_pnl(metrics: Metrics, *, min_trades: int = 5) -> float:
    """The legacy objective, retained for comparison and explicitly not the default.

    Kept so that a user can reproduce an old result and see for themselves how differently it
    ranks. See the module docstring for why it is not recommended.
    """
    pnl = metrics.total_pnl
    if metrics.total_trades < min_trades:
        pnl *= 0.1
    drawdown = metrics.max_drawdown_pct / 100.0
    fitness = pnl * (1 + drawdown) if pnl > 0 else pnl * (1 - drawdown)
    return -fitness


#: Selectable objectives. The name is recorded in the result, because a score is not comparable
#: across objectives and a report that omits which one produced it is not interpretable.
OBJECTIVES: Final[Mapping[str, Objective]] = {
    "calmar": calmar,
    "sortino": sortino,
    "sharpe": sharpe,
    "legacy_pnl": legacy_pnl,
}

DEFAULT_OBJECTIVE: Final = "calmar"


def get_objective(name: str) -> Objective:
    """Look up an objective by name.

    Raises:
        KeyError: no such objective.
    """
    return OBJECTIVES[name]
