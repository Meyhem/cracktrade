"""Assembling a :class:`~cracktrade.domain.BacktestResult`.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 8 and 11. This is the public entry point
to the backtest layer, and the last place a vectorbt object exists.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pandas as pd

from cracktrade.backtest.benchmark import buy_and_hold_portfolio, compare
from cracktrade.backtest.metrics import extract_metrics, extract_trades
from cracktrade.backtest.runner import overnight_carries, run_simulation
from cracktrade.backtest.series import capture
from cracktrade.domain import BacktestResult, DataVintage, Metrics
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from cracktrade.config import Strategy
    from cracktrade.data import MarketData

logger = get_logger(__name__)


def run_backtest(
    strategy: Strategy,
    data: MarketData,
    *,
    seed: int = 0,
    capture_series: bool = False,
) -> BacktestResult:
    """Simulate ``strategy`` over ``data`` and report it.

    ``capture_series`` additionally records the per-bar series a chart is drawn from. Off by
    default: the CLI prints numbers and would only pay to build curves nobody asked for.

    Raises:
        BacktestError: the bars are not the width the strategy declared.
        IndicatorError: an indicator could not be computed.
        SignalError: a signal expression could not be evaluated.
    """
    simulation = run_simulation(strategy, data, seed=seed)
    risk_free = strategy.execution.risk_free_rate
    warmup = simulation.warmup
    calendar = simulation.calendar
    metrics = extract_metrics(simulation.portfolio, risk_free_rate=risk_free, calendar=calendar)

    # The benchmark starts where the strategy could first have acted: one bar after warm-up, the
    # earliest a shifted signal can land. Starting it at bar zero would credit it with return the
    # strategy was structurally unable to capture.
    benchmark_portfolio = buy_and_hold_portfolio(
        data,
        strategy.execution,
        strategy.position_sizing,
        start_bar=warmup + 1,
        calendar=calendar,
        seed=seed,
    )
    benchmark_metrics = extract_metrics(
        benchmark_portfolio, risk_free_rate=risk_free, calendar=calendar
    )

    result = BacktestResult(
        strategy_name=strategy.strategy.name,
        ticker=data.ticker,
        vintage=vintage_of(data),
        metrics=metrics,
        trades=extract_trades(simulation.portfolio, data.index, intraday=data.interval.is_intraday),
        overnight_carries=overnight_carries(simulation, data),
        entry_defined_pct=simulation.entry_signal.defined_pct,
        exit_defined_pct=simulation.exit_signal.defined_pct if simulation.exit_signal else None,
        active_stop=simulation.stops.active_stop,
        shadowed_stops=simulation.stops.shadowed_stops,
        benchmark=compare(
            metrics,
            benchmark_metrics,
            strategy_returns=simulation.portfolio.returns(),
            benchmark_returns=benchmark_portfolio.returns(),
            calendar=calendar,
        ),
        risk_free_rate=risk_free,
        warmup_bars=warmup,
        series=(
            capture(portfolio=simulation.portfolio, benchmark=benchmark_portfolio, data=data)
            if capture_series
            else None
        ),
    )

    _log_verdict(result, benchmark_metrics)
    return result


def vintage_of(data: MarketData) -> DataVintage:
    """Record which prices this result was computed from."""
    return DataVintage(
        ticker=data.ticker,
        first_bar=data.effective_start,
        last_bar=data.effective_end,
        bars=len(data),
        filled_bars=data.filled_bars,
        fetched_on=datetime.now(UTC).date(),
        frame_digest=frame_digest(data.frame),
    )


def frame_digest(frame: pd.DataFrame) -> str:
    """A short stable digest of the price frame.

    Two runs reporting different numbers from the "same" data is the confusing case; comparing
    digests settles instantly whether the prices moved underneath.
    """
    payload = pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()
    return hashlib.sha256(payload).hexdigest()[:16]


def _log_verdict(result: BacktestResult, benchmark: Metrics) -> None:
    """Say plainly whether the strategy beat simply holding the ticker."""
    verdict = "beats" if result.benchmark.beats_buy_and_hold else "loses to"
    logger.info(
        "%s on %s: returned %.1f%%, which %s buy-and-hold at %.1f%%",
        result.strategy_name,
        result.ticker,
        result.metrics.total_return_pct,
        verdict,
        benchmark.total_return_pct,
    )
    if not result.metrics.has_enough_trades_to_judge:
        logger.warning(
            "%s produced only %d closed trade(s); that is too few to draw a conclusion from",
            result.strategy_name,
            result.metrics.total_trades,
        )
