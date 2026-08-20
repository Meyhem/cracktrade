"""Continuous prospecting -- spec section 19.

Section 16 evolves one strategy for one ticker on demand. This package is that search placed on
a loop across many tickers, with a memory, running until it is stopped.

The feature is easy to describe and easy to get catastrophically wrong, because a leaderboard is
a **maximum** and section 12 exists to punish maxima. Two measurements taken before any of it was
designed shape everything here:

* **Compute does not buy better strategies.** The same ticker and seed with the budget scaled
  forty-fold produced no improvement in-sample or out -- and in one of the two tickers the
  largest search came back negative and worse than the smallest. The binding constraint is data,
  not compute, so the package is built for breadth rather than depth.
* **A strategy that looks excellent on its own ticker usually is not one.** Hence
  :mod:`cracktrade.prospect.transfer`, which is the cheapest and most decisive rung of the
  ladder in section 19.3.

The rule that keeps the whole thing honest is in section 19.2 and is worth repeating here,
because it is what a reader of this package will most want to violate: **prospecting produces
candidates, never verdicts, and never publishes a deflated Sharpe.** A week of sweeping would
have to deflate by roughly nine million configurations, which demands an annualised Sharpe near
4.9 -- a correct figure attached to a ranking nobody could act on. ``is_credible`` remains a
property of a strategy that has had its own walk-forward (section 17.2), and nothing here
changes that.
"""

from __future__ import annotations

from cracktrade.prospect.families import (
    DEFAULT_UNIVERSE,
    FAMILIES,
    INVERSE_BUCKET,
    Family,
    family_of,
    is_inverse,
)
from cracktrade.prospect.forward import MIN_FORWARD_BARS, ForwardScore, forward_score
from cracktrade.prospect.session import (
    DAILY_LOOKBACK_DAYS,
    WARMUP_MARGIN,
    SweepHistory,
    SweepParams,
)
from cracktrade.prospect.sweep import (
    PROSPECT_SETTINGS,
    Candidate,
    Rotation,
    prospect_once,
)
from cracktrade.prospect.transfer import (
    TRANSFER_FLOOR,
    DataFor,
    SiblingResult,
    TransferReport,
    retarget,
    transfer_report,
)

__all__ = [
    "DAILY_LOOKBACK_DAYS",
    "DEFAULT_UNIVERSE",
    "FAMILIES",
    "INVERSE_BUCKET",
    "MIN_FORWARD_BARS",
    "PROSPECT_SETTINGS",
    "TRANSFER_FLOOR",
    "WARMUP_MARGIN",
    "Candidate",
    "DataFor",
    "Family",
    "ForwardScore",
    "Rotation",
    "SiblingResult",
    "SweepHistory",
    "SweepParams",
    "TransferReport",
    "family_of",
    "forward_score",
    "is_inverse",
    "prospect_once",
    "retarget",
    "transfer_report",
]
