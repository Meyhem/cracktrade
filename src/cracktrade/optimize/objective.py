"""What the optimizer is trying to maximise.

Normative reference: ``docs/ENGINE_SPEC.md`` section 9.3.

The default was changed from the legacy objective after the audit (finding B2). Legacy scored

    fitness = pnl * (1 +/- max_drawdown),  with  pnl *= 0.1  when trades < 5

which has three independent problems. Raw PnL is scale-dependent and outlier-dominated, so one
lucky trade outranks a hundred consistent ones. The small-sample penalty is a *multiplier*, so
any fluke bigger than ten times defeats it. And the drawdown term scales a typical -20% drawdown
by 0.8, which barely disciplines risk at all.

Legacy's fourth problem -- taking the maximum across entry/exit variants inside every evaluation,
making the in-sample optimum the best of ``evaluations x entries x exits`` draws -- is gone by
construction: a strategy now has one entry rule and one exit rule, so an evaluation is one draw.

The default here is Calmar -- annualised return over maximum drawdown -- with the trade-count
floor as a **hard feasibility constraint** rather than a discount. A three-trade result carries
no information no matter how large its PnL, so it is rejected outright.

Scores are returned in scipy's *minimisation* space: lower is better, and ``+inf`` means
infeasible or failed.

**Two-stage construction.** :data:`OBJECTIVES` holds *unbound* scorers, which take the floor as a
keyword. :func:`get_objective` binds one, yielding the single-argument :class:`Objective` the
search and the stability surface call. The split exists because the floor is not a constant: it
is resolved per run from a :class:`TradeFloor` and the length of the train window, and a scorer
that read a module global instead could not be told about either.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import partial
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

#: Closed trades a candidate must produce, regardless of how short the train window is.
DEFAULT_MIN_TRADES: Final = 20

#: Closed trades a candidate must produce *per year of train window*, on top of the absolute
#: floor. One a quarter.
#:
#: **[NEW]** Without a rate the constraint weakens silently as the history grows: 20 trades is a
#: real bar over two years and no bar at all over twenty, where a candidate can clear it by
#: trading twice a year and letting a handful of events decide the whole Calmar. The rate is
#: deliberately mild -- it is a floor on how much of the window the result is evidence *about*,
#: not an opinion about how often a strategy ought to trade.
DEFAULT_MIN_TRADES_PER_YEAR: Final = 4.0


@dataclass(frozen=True, slots=True)
class TradeFloor:
    """How many closed trades a candidate must produce to be scored at all.

    The effective floor is the larger of an absolute count and a per-year rate, so neither a
    very short window nor a very long one can dissolve the constraint. Both parts are
    configurable per run: the engine does not know a strategy's intended trade frequency, and a
    constant that suits a swing strategy is wrong for a position one in both directions.

    Attributes:
        minimum: absolute floor, applied whatever the window length.
        per_year: additional floor per year of train window. Zero disables the rate.
    """

    minimum: int = DEFAULT_MIN_TRADES
    per_year: float = DEFAULT_MIN_TRADES_PER_YEAR

    def __post_init__(self) -> None:
        if self.minimum < 0:
            msg = f"min_trades must not be negative, got {self.minimum}"
            raise ValueError(msg)
        if self.per_year < 0 or not math.isfinite(self.per_year):
            msg = f"min_trades_per_year must be a non-negative number, got {self.per_year}"
            raise ValueError(msg)

    def required(self, train_bars: int, *, periods_per_year: float) -> int:
        """The floor in force for a train window of ``train_bars`` bars.

        Rounded up: a rate of 4/year over a 15-month window asks for 5 trades, not 4.99 of one.

        ``periods_per_year`` comes from the run's calendar, and has no default for the
        section 7.5 reason: left at 252 on 30-minute bars it would read 680 bars as 2.7 years
        and demand a rate-based floor for a window that is actually forty sessions long --
        which would then be blamed on the strategy.
        """
        years = train_bars / periods_per_year
        return max(self.minimum, math.ceil(self.per_year * years))


#: The floor a run gets when the caller does not ask for one.
DEFAULT_TRADE_FLOOR: Final = TradeFloor()


class ScoreFunction(Protocol):
    """An objective before its trade floor is bound. Lower is better."""

    def __call__(self, metrics: Metrics, /, *, min_trades: int) -> float: ...


class Objective(Protocol):
    """Scores one candidate's train-window metrics. Lower is better."""

    def __call__(self, metrics: Metrics, /) -> float: ...


def calmar(metrics: Metrics, /, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
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


def sortino(metrics: Metrics, /, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """Downside-deviation-adjusted return. Smoother for the search than Calmar."""
    if metrics.total_trades < min_trades:
        return INFEASIBLE
    return -metrics.sortino_ratio


def sharpe(metrics: Metrics, /, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """Excess return per unit of total volatility."""
    if metrics.total_trades < min_trades:
        return INFEASIBLE
    return -metrics.sharpe_ratio


def legacy_pnl(metrics: Metrics, /, *, min_trades: int = DEFAULT_MIN_TRADES) -> float:
    """The legacy objective, retained for comparison and explicitly not the default.

    Kept so that a user can reproduce an old result and see for themselves how differently it
    ranks. See the module docstring for why it is not recommended.

    **The floor is the run's, not legacy's hardcoded 5.** One run has one definition of "too few
    trades to believe"; what differs between objectives is the *response* to it, and that
    difference -- a cliff here, a survivable 10x discount there -- is the whole point of the
    comparison. Reproducing an old result exactly therefore also means asking for the old floor,
    ``min_trades=5`` with the per-year rate off.
    """
    pnl = metrics.total_pnl
    if metrics.total_trades < min_trades:
        pnl *= 0.1
    drawdown = metrics.max_drawdown_pct / 100.0
    fitness = pnl * (1 + drawdown) if pnl > 0 else pnl * (1 - drawdown)
    return -fitness


#: Selectable objectives, unbound. The name is recorded in the result, because a score is not
#: comparable across objectives and a report that omits which one produced it is not
#: interpretable. The same goes for the floor, which the result records alongside it.
OBJECTIVES: Final[Mapping[str, ScoreFunction]] = {
    "calmar": calmar,
    "sortino": sortino,
    "sharpe": sharpe,
    "legacy_pnl": legacy_pnl,
}

DEFAULT_OBJECTIVE: Final = "calmar"


def get_objective(name: str, *, min_trades: int = DEFAULT_MIN_TRADES) -> Objective:
    """Look up an objective by name and bind the trade floor in force.

    ``partial`` rather than a closure: the fitness object holding the result has to survive
    pickling to reach a worker process (spec section 9.2), and a closure does not.

    Raises:
        KeyError: no such objective.
    """
    return partial(OBJECTIVES[name], min_trades=min_trades)
