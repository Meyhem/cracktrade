"""The third rung: scoring a candidate on bars that arrived after it was found.

Normative reference: ``docs/ENGINE_SPEC.md`` section 19.3.

This is the only rung whose evidence the search could not have influenced, and it is therefore
the only one that answers the question a leaderboard is actually asked. The first two rungs --
the holdout and the sibling transfer -- are both computed from history the search had access to
in some form. These bars did not exist when the candidate was chosen.

Two refusals, both deliberate, and both preferring "not yet" to a number:

* **A window shorter than :data:`MIN_FORWARD_BARS` is not scored at all.** A Sharpe from nine
  bars is noise wearing a statistic's clothes, and it would sort above a real one.
* **A window without a full warm-up prefix is not scored either.** The indicators would be
  cold, the strategy would trade differently than it does, and the resulting figure would be a
  measurement of the truncation rather than of the candidate.

And one figure withheld rather than the whole score: a Sharpe over a window the candidate never
traded is ``+Infinity``, not a number, and it would sort and read as the best result on the
board. The window is still reported -- the return and the trade count are honest measurements
of a strategy that sat out -- but the ratio is ``None``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from cracktrade.backtest import extract_metrics, run_simulation
from cracktrade.config import Strategy
from cracktrade.data import MarketData
from cracktrade.log import get_logger
from cracktrade.strategy import required_warmup

logger = get_logger(__name__)

#: Fewest bars worth reporting a forward result over.
#:
#: Not a statistical threshold -- there is no bar count at which a Sharpe becomes trustworthy --
#: but a floor beneath which the figure is certainly meaningless. Section 19.3's point is that
#: forward evidence accrues slowly; the leaderboard says "not yet" until there is some, rather
#: than ranking on the first week's noise.
MIN_FORWARD_BARS = 30


@dataclass(frozen=True, slots=True)
class ForwardScore:
    """A candidate measured on bars it was never shown.

    Attributes:
        first_bar: first scored bar, strictly after the candidate's ``last_bar_seen``.
        last_bar: last scored bar.
        bars: how many bars the figures cover, so a result from thirty-one is visible as such.
        return_pct: total return over the window.
        sharpe: annualised Sharpe over the window, or ``None`` where it is undefined --
            a window without trades has no return variance to divide by. Distinct from a
            Sharpe of zero, and distinct again from the absence of a score.
        trades: closed trades in the window.
    """

    first_bar: date
    last_bar: date
    bars: int
    return_pct: float
    sharpe: float | None
    trades: int


def forward_score(
    strategy: Strategy, data: MarketData, *, since: date, warmup: int | None = None
) -> ForwardScore | None:
    """Score ``strategy`` on the bars of ``data`` strictly after ``since``.

    ``None`` when there is not yet anything honest to report -- too few new bars, or not enough
    history before them to warm the indicators. A caller records nothing in that case; it does
    not record a zero.

    The warm-up prefix is included in the simulation and excluded from the scoring, which is the
    same arrangement the optimizer's test window uses. ``scored_from`` also forbids a position
    opening inside the prefix, so the window cannot report a return earned by a trade its own
    trade count does not contain.

    Args:
        strategy: the candidate, exactly as the sweep left it.
        data: history reaching past ``since``. Bars up to and including it are warm-up only.
        since: the last bar the search could see.
        warmup: bars the indicators need. Derived from the strategy when omitted.

    Returns:
        The score, or ``None`` if the window cannot support one.
    """
    needed = required_warmup(strategy) if warmup is None else warmup
    index = data.index

    # By calendar date in the index's own timezone, which is where ``last_bar_seen`` came
    # from. Comparing against a timestamp instead would keep every intraday bar of ``since``
    # itself on the scored side -- bars the search had already seen.
    first = int(index.normalize().searchsorted(pd.Timestamp(since, tz=index.tz), side="right"))
    scored = len(data) - first
    if scored < MIN_FORWARD_BARS:
        logger.debug(
            "%s: %d bars since %s, fewer than the %d needed to report anything",
            data.ticker,
            scored,
            since,
            MIN_FORWARD_BARS,
        )
        return None
    if first < needed:
        # The candidate's own history is the warm-up, so this means the supplied window starts
        # too close to the discovery date. Reporting anyway would measure the truncation.
        logger.debug(
            "%s: only %d bars before %s, short of the %d this strategy needs to warm up",
            data.ticker,
            first,
            since,
            needed,
        )
        return None

    window = data.slice(first - needed, len(data))
    simulation = run_simulation(strategy, window, scored_from=needed)
    metrics = extract_metrics(
        simulation.portfolio,
        risk_free_rate=strategy.execution.risk_free_rate,
        calendar=simulation.calendar,
        offset=needed,
    )
    return ForwardScore(
        first_bar=index[first].date(),
        last_bar=index[-1].date(),
        bars=scored,
        return_pct=metrics.total_return_pct,
        sharpe=metrics.sharpe_ratio if math.isfinite(metrics.sharpe_ratio) else None,
        trades=metrics.total_trades,
    )


__all__ = ["MIN_FORWARD_BARS", "ForwardScore", "forward_score"]
