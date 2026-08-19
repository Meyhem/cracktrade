"""Running a strategy over a price history.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7. This is the seam Phase 7 turns into
domain result models: everything here still holds a vectorbt ``Portfolio``, which is an
implementation detail and does not appear in the public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cracktrade.backtest.calendar import Calendar
from cracktrade.backtest.holding import holding_bounds
from cracktrade.backtest.portfolio import require_interval, simulate
from cracktrade.backtest.sessionclose import SessionRules, session_rules
from cracktrade.backtest.stops import StopConfiguration, build_stops
from cracktrade.errors import BacktestError, CausalityViolationError
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
        calendar: the annualisation basis measured for this history.
        sessions: the intraday session rules applied, or ``None`` on a daily run.
    """

    portfolio: vbt.Portfolio
    entries: pd.Series
    exits: pd.Series
    entry_signal: EvaluatedSignal
    exit_signal: EvaluatedSignal | None
    stops: StopConfiguration
    warmup: int
    calendar: Calendar
    sessions: SessionRules | None = None


def run_simulation(
    strategy: Strategy,
    data: MarketData,
    *,
    seed: int = 0,
    scored_from: int = 0,
) -> Simulation:
    """Simulate ``strategy``'s entry and exit rules over ``data``.

    ``scored_from`` is the first bar whose result will be reported. Every window that carries a
    warm-up prefix passes its offset here, and no position may be opened before it. See
    :func:`_suppress_prefix_entries` for why that is a correctness requirement rather than
    tidiness.

    Raises:
        BacktestError: the bars are not the width the strategy declared, or an intraday
            minimum holding period could never be satisfied within a session.
        CausalityViolationError: ``scored_from`` is negative.
    """
    require_interval(data)
    calendar = Calendar.of(data)
    namespace = compute_indicators(strategy, data)

    logger.info(
        "simulating %d bars at %s, warm-up %d, %.0f periods/year",
        len(data),
        data.interval.value,
        namespace.warmup,
        calendar.periods_per_year,
    )

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

    sessions = session_rules(data.index) if data.interval.is_intraday else None
    if sessions is not None:
        _require_holding_fits_a_session(strategy, calendar)
        # Suppressing the entry here, rather than inside the signal function, keeps the
        # decision where every other signal-shaping decision already lives.
        entries = entries & ~pd.Series(sessions.suppressed_entries, index=data.index)

    entries = _suppress_prefix_entries(entries, scored_from)

    portfolio = simulate(
        data=data,
        entries=entries,
        exits=exits,
        stops=stops,
        execution=strategy.execution,
        sizing=strategy.position_sizing,
        holding=holding_bounds(strategy.exit),
        forced_exits=None if sessions is None else sessions.forced_exits,
        calendar=calendar,
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
        calendar=calendar,
        sessions=sessions,
    )


def _suppress_prefix_entries(entries: pd.Series, scored_from: int) -> pd.Series:
    """Forbid opening a position before the first bar a window reports on.

    A scored window is preceded by a warm-up prefix whose bars exist only to give the indicators
    history (spec section 9.4). Nothing stops a signal from firing inside that prefix, and until
    this existed nothing did: the prefix is sized for the *worst-case* candidate in the search
    space, so a candidate needing less warm-up than the widest one could legitimately enter
    there.

    The result was a report that disagreed with itself. A position opened in the prefix and
    still held at the first scored bar contributes its P&L to every returns-based figure --
    Sharpe, CAGR, total return, drawdown, exposure, all computed from ``offset`` onward -- while
    being filtered out of the trade list and the trade count, which key off the entry bar. So a
    window could report a return earned by a trade the report did not contain, and the
    buy-and-hold benchmark, which starts exactly at ``offset``, was compared against a strategy
    that had been allowed an earlier fill.

    Neither direction is safe. A prefix entry that ran into a rally flatters the strategy and one
    that ran into a drawdown punishes it, so this was noise injected into the one number the
    whole optimization protocol exists to keep clean.

    Entering *at* ``scored_from`` is allowed and fills at that bar's open, which is where the
    benchmark buys too.

    Raises:
        CausalityViolationError: ``scored_from`` is negative.
    """
    if scored_from < 0:
        msg = f"refusing to score from bar {scored_from}: a window cannot begin before its data"
        raise CausalityViolationError(msg)
    if not scored_from:
        return entries

    # Copied rather than written in place: this series belongs to the EvaluatedSignal that
    # Simulation reports, and mutating it would rewrite the record of what the signal said.
    suppressed = entries.copy()
    suppressed.iloc[:scored_from] = False
    return suppressed


def _require_holding_fits_a_session(strategy: Strategy, calendar: Calendar) -> None:
    """Refuse a minimum holding period no intraday position could ever reach.

    The forced session close overrides the minimum-holding rule -- it has to, or a position
    would be carried overnight by a strategy that merely asked to hold for a while. But that
    means a minimum at or above the session length can never be satisfied: every position is
    closed before reaching it, so the strategy being simulated is not the one the user wrote.
    Saying so here is better than returning a plausible set of numbers produced under a rule
    that silently did nothing.

    The session length is the measured one, so the message can name it: a bound that is fine on
    a 17-bar Xetra session is not fine on a 13-bar New York one.
    """
    minimum, _ = holding_bounds(strategy.exit)
    if minimum and minimum >= calendar.bars_per_session:
        msg = (
            f"min_holding_bars is {minimum}, but a session of {strategy.universe.ticker} holds "
            f"about {calendar.bars_per_session} {strategy.universe.interval.value} bars. An "
            f"intraday position is closed at the session's end regardless of the minimum, so "
            f"no position could ever reach it -- lower the minimum below "
            f"{calendar.bars_per_session}, or use a longer interval"
        )
        raise BacktestError(msg)


def overnight_carries(simulation: Simulation, data: MarketData) -> int:
    """How many of this run's trades were still open when their session ended.

    Zero on a healthy intraday run and on every daily run. See spec section 7.6: a non-zero
    count is reported, never suppressed.
    """
    if simulation.sessions is None:
        return 0

    from cracktrade.backtest.sessionclose import count_overnight_carries

    records = simulation.portfolio.trades.records
    if records.empty:
        return 0
    return count_overnight_carries(
        data.index,
        records["entry_idx"].to_numpy().astype(np.int64),
        records["exit_idx"].to_numpy().astype(np.int64),
    )
