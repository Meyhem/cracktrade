"""Running a strategy over a price history.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7. This is the seam Phase 7 turns into
domain result models: everything here still holds a vectorbt ``Portfolio``, which is an
implementation detail and does not appear in the public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from cracktrade.backtest.holding import holding_bounds
from cracktrade.backtest.portfolio import require_daily_bars, simulate
from cracktrade.backtest.stops import StopConfiguration, build_stops
from cracktrade.indicators import compute_indicators
from cracktrade.log import get_logger
from cracktrade.signals import EvaluatedSignal, prepare_signal

if TYPE_CHECKING:
    import vectorbt as vbt

    from cracktrade.config import Strategy
    from cracktrade.data import MarketData

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Simulation:
    """A strategy simulated over one price history.

    Attributes:
        portfolio: the vectorbt portfolio. Internal to the backtest layer.
        entries: the entry series handed to the simulation.
        exits: the exit series handed to the simulation, before the holding rules that the
            signal function applies to it during the run.
        entry_signal: the evaluated entry expression, carrying its definedness record.
        exit_signal: the evaluated exit expression, or ``None`` when the strategy exits only by
            stops or holding period.
        stops: the resolved stop configuration.
        warmup: bars suppressed at the head of every signal in this run.
    """

    portfolio: vbt.Portfolio
    entries: pd.Series
    exits: pd.Series
    entry_signal: EvaluatedSignal
    exit_signal: EvaluatedSignal | None
    stops: StopConfiguration
    warmup: int


def run_simulation(
    strategy: Strategy,
    data: MarketData,
    *,
    seed: int = 0,
) -> Simulation:
    """Simulate ``strategy``'s entry and exit rules over ``data``.

    Raises:
        BacktestError: the history is not daily bars.
    """
    require_daily_bars(data)
    namespace = compute_indicators(strategy, data)

    logger.info("simulating %d bars, warm-up %d", len(data), namespace.warmup)

    entry_signal = prepare_signal(strategy.entry.signal, namespace)
    exit_signal = (
        prepare_signal(strategy.exit.signal, namespace)
        if strategy.exit.signal is not None
        else None
    )

    entries = entry_signal.series
    exits = (
        exit_signal.series
        if exit_signal is not None
        else pd.Series(False, index=data.index, dtype=bool)
    )
    stops = build_stops(strategy.exit, data)

    portfolio = simulate(
        data=data,
        entries=entries,
        exits=exits,
        stops=stops,
        execution=strategy.execution,
        sizing=strategy.position_sizing,
        holding=holding_bounds(strategy.exit),
        seed=seed,
    )

    return Simulation(
        portfolio=portfolio,
        entries=entries,
        exits=exits,
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        stops=stops,
        warmup=namespace.warmup,
    )
