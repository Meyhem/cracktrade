"""Saying out loud when a result rests on too little history.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.11.

Yahoo serves 15-minute and 30-minute bars for the last 60 calendar days only. Roughly 40 Xetra
sessions of 30-minute bars is about 680 bars, which *looks* like plenty next to a daily backtest
of 680 days and is nothing like it: it is forty independent days, most of a strategy's trades
drawn from a handful of weeks, and every regime it will ever meet absent from the sample.

Nothing here blocks a run. The user asked for it and gets it. But the project's first design
constraint is that a number which looks authoritative and is not is the worst possible output,
so the run carries a flag that says how thin its evidence was, and the flag is stated in
sessions rather than bars -- "40 sessions of 30-minute bars" tells a trader something that "680
bars" does not.

The flag is on for effectively every walk-forward at 15m or 30m. That is not a calibration
failure; it is true, and the note carries the honest alternative: 1h reaches back about two
years.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from cracktrade.data.sessions import session_count
from cracktrade.domain import HistoryScope

if TYPE_CHECKING:
    from cracktrade.data import MarketData

#: Sessions below which a single-window result (backtest, optimize) is flagged. A quarter's
#: worth of trading: enough that a monthly pattern appears more than once.
SINGLE_WINDOW_SESSIONS: Final = 60

#: Sessions below which a multi-window result (walk-forward, evolution) is flagged. Twice the
#: single-window bar, because splitting thin history into folds makes each fold thinner still --
#: six folds of 120 sessions is twenty sessions per fold, already marginal.
MULTI_WINDOW_SESSIONS: Final = 120


def scope_of(data: MarketData, *, multi_window: bool = False) -> HistoryScope:
    """Measure a history and decide whether to flag it.

    Args:
        data: the history the run used.
        multi_window: True for walk-forward and evolution, which cut the history into folds or
            segments and so need more of it to say anything.
    """
    interval = data.interval
    sessions = len(data) if not interval.is_intraday else session_count(data.index)
    threshold = MULTI_WINDOW_SESSIONS if multi_window else SINGLE_WINDOW_SESSIONS

    if sessions >= threshold:
        return HistoryScope(
            interval=interval.value, sessions=sessions, bars=len(data), limited=False
        )

    return HistoryScope(
        interval=interval.value,
        sessions=sessions,
        bars=len(data),
        limited=True,
        note=_note(data, sessions, threshold, multi_window=multi_window),
    )


def _note(data: MarketData, sessions: int, threshold: int, *, multi_window: bool) -> str:
    """One sentence: what is thin, why it matters here, and the way out."""
    interval = data.interval
    kind = "a walk-forward or evolution run" if multi_window else "a backtest or optimization"
    unit = "trading day" if not interval.is_intraday else f"session of {interval.value} bars"

    note = (
        f"This result covers {sessions} {unit}{'' if sessions == 1 else 's'} "
        f"({len(data)} bars), below the {threshold} that {kind} needs before its numbers mean "
        f"much. Treat every figure here as a description of one short stretch of market, not "
        f"as an estimate of what the strategy does."
    )

    limit = interval.max_lookback
    if limit is not None and limit.days < 100:
        note += (
            f" {interval.value} bars are only served for about {limit.days} days, so a longer "
            f"range is not available at this interval -- 1h reaches back roughly two years."
        )
    elif limit is not None:
        note += " Widening the date range is the fix; 1d has no limit."
    else:
        note += " Widen the date range."
    return note
