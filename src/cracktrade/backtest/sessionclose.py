"""Closing intraday positions before the market shuts.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7.6.

An intraday strategy never holds overnight. The trader is executing manually through a retail
broker; a position left open across a close carries a gap risk the backtest cannot model and the
trader did not agree to. So exits are forced at the end of each session, and entries are
suppressed on the bar the forced exit lands on -- an entry there would fill at that same bar's
open and then be closed immediately, or worse, carry into the next session.

**The causality trap this module exists to avoid.** "The last bar of the session" naively means
"no later bar exists on this date", which is a fact about the *future* of bar ``t``. Computing it
that way is look-ahead, and the truncation-equivalence harness catches it immediately: truncate a
history mid-session and every truncation point becomes a phantom session close, so the same bar
gets a different decision depending on how much history follows it. That is the definition of the
bug the harness is there to find.

The rule is therefore built from two layers, both pure functions of the past:

1. **A learned close time.** For bar ``t``, the expected close is the latest bar-open time seen
   in the most recent completed sessions -- completed meaning "whose date is strictly earlier
   than ``t``'s", which is knowable at ``t``. A bar landing on that time is the session's close.
2. **A safety net at the next open.** If a position survives a session boundary anyway, it is
   closed on the first bar of the new session -- and "the previous bar has a different date" is
   again a fact about the past. The trade record then shows the overnight carry instead of
   hiding it, and the run reports how many times it happened.

The second layer is not a formality. Two years of Xetra hourly bars contain an early close, a
late open and two five-bar December sessions, and the LSE trades four-bar half-days on dates
Xetra is shut altogether. The learned close is *wrong* several times a year, by construction:
taking the latest close among recent sessions means a half-day never matches it. That is the
honest outcome -- the engine says a position was carried overnight, because one was -- rather
than a guess dressed up as a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

from cracktrade.data.sessions import is_session_open, session_ids

if TYPE_CHECKING:
    import numpy.typing as npt

#: How many completed sessions the close time is learned from. Small enough to follow a venue
#: that changes its hours, large enough that one anomalous session does not move it. Taking the
#: *latest* close across the window rather than the most recent one is what makes a half-day
#: inside the window harmless: it lowers no expectation, so the following normal session is
#: still recognised.
LEARNING_SESSIONS: Final = 5


@dataclass(frozen=True, slots=True)
class SessionRules:
    """The per-bar masks that keep an intraday strategy flat overnight.

    Attributes:
        forced_exits: True on bars where a position must be closed regardless of the
            strategy's own exit rules -- the learned session close, plus the safety net on
            each session's first bar.
        suppressed_entries: True on bars where an entry must not be taken -- the session's
            close bar, where an entry would fill with no opportunity to close before the
            session ends, and every bar of a session whose close cannot yet be predicted.
    """

    forced_exits: npt.NDArray[np.bool_]
    suppressed_entries: npt.NDArray[np.bool_]


def session_rules(index: pd.DatetimeIndex) -> SessionRules:
    """Compute the forced-exit and entry-suppression masks for an intraday history.

    Every value at position ``t`` depends only on positions ``0..t``. Truncating the index and
    recomputing yields a prefix of the same arrays, bit for bit, which is what the causality
    harness asserts.
    """
    if len(index) == 0:
        empty = np.zeros(0, dtype=np.bool_)
        return SessionRules(forced_exits=empty, suppressed_entries=empty)

    closes, unlearned = _at_learned_close(index)
    opens = is_session_open(index)

    return SessionRules(
        # A position open at a session's first bar has already been carried overnight; closing
        # it there is the last honest thing that can be done about it.
        forced_exits=closes | opens,
        # The close bar, plus every bar of a session whose end cannot yet be predicted.
        # Entering on a session's *first* bar is otherwise exactly what an intraday strategy is
        # supposed to do.
        suppressed_entries=closes | unlearned,
    )


def _at_learned_close(
    index: pd.DatetimeIndex,
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.bool_]]:
    """Bars matching the learned close, and bars in a session with nothing learned yet.

    The first session of a history has no earlier session to learn from, so its close cannot be
    predicted. **No position is opened there at all.** The alternative -- letting the strategy
    trade a session the engine cannot promise to close -- would hand every intraday run a
    guaranteed overnight carry on bar one, which is both a real breach of the trader's stated
    constraint and, worse, noise: a counter that is never zero cannot signal anything. Refusing
    to enter is one session of warm-up, bounded and of a kind the engine already has for
    indicators.
    """
    sessions = session_ids(index)
    minutes = _minute_of_day(index)

    session_count = int(sessions[-1]) + 1
    # The latest bar-open time seen in each session. np.maximum.at accumulates per session in
    # one pass, which matters because this runs inside the optimizer's inner loop.
    session_close = np.zeros(session_count, dtype=np.int64)
    np.maximum.at(session_close, sessions, minutes)

    # The latest close among the LEARNING_SESSIONS sessions before each one. Built by walking
    # sessions in order, so nothing reads forward.
    expected = np.full(session_count, -1, dtype=np.int64)
    for session in range(1, session_count):
        window = session_close[max(0, session - LEARNING_SESSIONS) : session]
        expected[session] = int(window.max())

    per_bar = expected[sessions]
    matched: npt.NDArray[np.bool_] = (per_bar >= 0) & (minutes == per_bar)
    unlearned: npt.NDArray[np.bool_] = per_bar < 0
    return matched, unlearned


def _minute_of_day(index: pd.DatetimeIndex) -> npt.NDArray[np.int64]:
    """Each bar's opening time as minutes past local midnight.

    Derived by subtracting the normalised index from itself rather than by reading ``.time``,
    which would build a Python object per bar. Local by construction, because the index is
    exchange-local (spec section 4.1) -- the same session reads the same minute in March and in
    November, which is the property the learned close depends on.
    """
    offsets = index.to_numpy() - index.normalize().to_numpy()
    return (offsets.astype("timedelta64[m]")).astype(np.int64)


def count_overnight_carries(
    index: pd.DatetimeIndex,
    entry_positions: npt.NDArray[np.int64],
    exit_positions: npt.NDArray[np.int64],
) -> int:
    """How many trades were still open when their session ended.

    Zero is the healthy value and the one an intraday run should report. A non-zero count is
    not an error and is never suppressed: it means the venue closed earlier than the learned
    time -- a half-day, an early close, or the first session of the history -- and the trade
    genuinely carried. Reporting it is the difference between a backtest that models the
    trader's stated constraint and one that quietly pretends to.
    """
    if len(index) == 0 or entry_positions.size == 0:
        return 0
    sessions = session_ids(index)
    last = len(index) - 1
    entries = np.clip(entry_positions, 0, last)
    exits = np.clip(exit_positions, 0, last)
    return int(np.count_nonzero(sessions[entries] != sessions[exits]))
