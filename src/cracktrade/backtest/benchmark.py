"""Buying and holding the ticker, as the hurdle every strategy has to clear.

Normative reference: ``docs/ENGINE_SPEC.md`` section 8, audit finding B1.

Without this the report answers "did the optimizer improve the strategy" rather than "is this
worth owning". A strategy returning 13% on a ticker that returned 40% would read as a success.

The benchmark is deliberately built through the *same* simulation path as the strategy, with the
same commission, the same slippage, and the same next-open fill, so the comparison is like with
like rather than against a frictionless ideal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cracktrade.backtest.calendar import Calendar
from cracktrade.backtest.portfolio import simulate
from cracktrade.backtest.stops import StopConfiguration
from cracktrade.domain import BenchmarkComparison, Metrics

if TYPE_CHECKING:
    import vectorbt as vbt

    from cracktrade.config import ExecutionConfig, PositionSizing
    from cracktrade.data import MarketData


def buy_and_hold_portfolio(
    data: MarketData,
    execution: ExecutionConfig,
    sizing: PositionSizing | None,
    *,
    start_bar: int,
    calendar: Calendar,
    seed: int = 0,
) -> vbt.Portfolio:
    """Simulate buying at ``start_bar`` and holding to the end of the history.

    ``start_bar`` is the first bar on which the strategy could itself have traded -- the bar
    after warm-up ends. Starting the benchmark earlier would hand it return the strategy was
    structurally unable to capture, which flatters the strategy by comparison.

    No session rules are applied. Buy-and-hold *is* an overnight position; forcing it flat each
    afternoon would make it a different strategy, and a much worse benchmark. The comparison
    stays honest because both sides run through the same simulation with the same costs -- and
    the point of the hurdle is precisely that an intraday strategy has to earn its constraint.
    """
    index = data.index
    entries = pd.Series(False, index=index, dtype=bool)
    if start_bar < len(index):
        entries.iloc[start_bar] = True

    return simulate(
        data=data,
        entries=entries,
        exits=pd.Series(False, index=index, dtype=bool),
        stops=StopConfiguration(
            sl_stop=np.nan,
            sl_trail=False,
            tp_stop=np.nan,
            active_stop=None,
            shadowed_stops=(),
        ),
        execution=execution,
        sizing=sizing,
        calendar=calendar,
        seed=seed,
    )


def compare(
    strategy_metrics: Metrics,
    benchmark_metrics: Metrics,
    *,
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    calendar: Calendar,
) -> BenchmarkComparison:
    """Measure a strategy against the benchmark."""
    return BenchmarkComparison(
        benchmark=benchmark_metrics,
        excess_return_pct=strategy_metrics.total_return_pct - benchmark_metrics.total_return_pct,
        excess_cagr_pct=strategy_metrics.cagr_pct - benchmark_metrics.cagr_pct,
        information_ratio=_information_ratio(strategy_returns, benchmark_returns, calendar),
    )


def _information_ratio(strategy: pd.Series, benchmark: pd.Series, calendar: Calendar) -> float:
    """Mean active return over the standard deviation of active return, annualised.

    Computed here rather than through vectorbt so that the annualisation uses the run's own
    calendar, with no dependence on a global default. It previously read the period count by
    splitting the ``"252 days"`` string apart, which stopped being a number of periods the
    moment a period was not a day.
    """
    active = (strategy - benchmark).dropna()
    if active.empty:
        return 0.0
    spread = float(active.std(ddof=1))
    if spread == 0.0 or np.isnan(spread):
        return 0.0
    return float(active.mean()) / spread * float(np.sqrt(calendar.periods_per_year))
