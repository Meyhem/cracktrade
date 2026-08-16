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
from cracktrade.backtest.variants import VariantPair, expand_variants
from cracktrade.indicators import compute_indicators
from cracktrade.log import get_logger
from cracktrade.signals import EvaluatedSignal, prepare_signal

if TYPE_CHECKING:
    import vectorbt as vbt

    from cracktrade.config import ExitVariant, Strategy
    from cracktrade.data import MarketData
    from cracktrade.indicators import IndicatorNamespace

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VariantSimulation:
    """One entry/exit variant pair, simulated.

    Attributes:
        pair: which variants produced this run.
        portfolio: the vectorbt portfolio. Internal to the backtest layer.
        entries: the entry series handed to the simulation.
        exits: the exit series handed to the simulation, before the holding rules that the
            signal function applies to it during the run.
        entry_signal: the evaluated entry expression, carrying its definedness record.
        exit_signal: the evaluated exit expression, or ``None`` when the variant exits only by
            stops or holding period.
        stops: the resolved stop configuration.
        warmup: bars suppressed at the head of every signal in this run.
    """

    pair: VariantPair
    portfolio: vbt.Portfolio
    entries: pd.Series
    exits: pd.Series
    entry_signal: EvaluatedSignal
    exit_signal: EvaluatedSignal | None
    stops: StopConfiguration
    warmup: int

    @property
    def label(self) -> str:
        """How this simulation is named in output."""
        return self.pair.label


def run_variants(
    strategy: Strategy,
    data: MarketData,
    *,
    seed: int = 0,
) -> tuple[VariantSimulation, ...]:
    """Simulate every entry/exit variant pair over ``data``.

    Indicators are computed once and shared: they depend only on the price history, not on
    which variant is being simulated. Each signal expression is likewise evaluated once and
    reused across every pair that references it.

    Raises:
        BacktestError: the history is not daily bars.
    """
    require_daily_bars(data)
    namespace = compute_indicators(strategy, data)
    pairs = expand_variants(strategy)

    logger.info(
        "simulating %d variant pair(s) over %d bars, warm-up %d",
        len(pairs),
        len(data),
        namespace.warmup,
    )

    entry_signals = {
        entry.name: prepare_signal(entry.signal, namespace) for entry in strategy.entry_variants
    }
    exit_signals = {exit_.name: _exit_signal(exit_, namespace) for exit_ in strategy.exit_variants}

    return tuple(
        _run_pair(
            pair=pair,
            data=data,
            strategy=strategy,
            entry_signal=entry_signals[pair.entry.name],
            exit_signal=exit_signals[pair.exit.name],
            warmup=namespace.warmup,
            seed=seed,
        )
        for pair in pairs
    )


def _exit_signal(variant: ExitVariant, namespace: IndicatorNamespace) -> EvaluatedSignal | None:
    """Evaluate an exit variant's signal, if it has one."""
    if variant.signal is None:
        return None
    return prepare_signal(variant.signal, namespace)


def _run_pair(
    *,
    pair: VariantPair,
    data: MarketData,
    strategy: Strategy,
    entry_signal: EvaluatedSignal,
    exit_signal: EvaluatedSignal | None,
    warmup: int,
    seed: int,
) -> VariantSimulation:
    """Simulate one entry/exit pair."""
    entries = entry_signal.series
    exits = (
        exit_signal.series
        if exit_signal is not None
        else pd.Series(False, index=data.index, dtype=bool)
    )
    stops = build_stops(pair.exit, data)

    portfolio = simulate(
        data=data,
        entries=entries,
        exits=exits,
        stops=stops,
        execution=strategy.execution,
        sizing=strategy.position_sizing,
        holding=holding_bounds(pair.exit),
        seed=seed,
    )

    return VariantSimulation(
        pair=pair,
        portfolio=portfolio,
        entries=entries,
        exits=exits,
        entry_signal=entry_signal,
        exit_signal=exit_signal,
        stops=stops,
        warmup=warmup,
    )
