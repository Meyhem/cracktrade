"""Validation and robustness: walk-forward folds, deflation, stability, and cost sensitivity.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.

Sections 2 through 11 make *look-ahead* bias inexpressible. This package addresses **selection**
bias, which is the failure mode that actually costs money in a strategy optimizer: search
thousands of configurations and an impressive result appears whether or not any edge exists.
"""

from __future__ import annotations

from cracktrade.validate.costs import DEFAULT_MULTIPLES, cost_sensitivity
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme, walk_forward_splits
from cracktrade.validate.runner import walk_forward
from cracktrade.validate.stability import stability_surface
from cracktrade.validate.statistics import (
    block_bootstrap_interval,
    deflated_sharpe,
    expected_maximum_sharpe,
    per_period_sharpe,
    probability_of_backtest_overfitting,
)

__all__ = [
    "DEFAULT_FOLDS",
    "DEFAULT_MULTIPLES",
    "FoldScheme",
    "block_bootstrap_interval",
    "cost_sensitivity",
    "deflated_sharpe",
    "expected_maximum_sharpe",
    "per_period_sharpe",
    "probability_of_backtest_overfitting",
    "stability_surface",
    "walk_forward",
    "walk_forward_splits",
]
