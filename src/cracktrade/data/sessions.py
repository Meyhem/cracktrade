"""Grouping an intraday index into trading sessions.

Normative reference: ``docs/ENGINE_SPEC.md`` section 4.6.

Everything here is a pure function of the index -- no wall clock, no network, no exchange
calendar, no state. That is not tidiness, it is the causality constraint: these functions feed
the forced session-close rule, and anything they returned that was not derivable from the bars
already seen would be look-ahead by another name.

A "session" is one local calendar date. That works because the index carries **exchange-local**
wall-clock time (spec section 4.1), so a Xetra session is 09:00-17:30 on one date and a New York
session is 09:30-16:00 on one date, with no session in either venue crossing midnight. It would
*not* work on a UTC index, where a Sydney or a futures session straddles the date line -- which
is one more reason the index is local, and a reason this module is documented as equity-session
only. Instruments that trade around the clock are out of scope for this pass.

Sessions are deliberately **not** assumed to be a fixed length. Two years of Xetra hourly bars
contain nine-bar days, an eight-bar early close, a seven-bar late open, and five-bar sessions
either side of Christmas, while the LSE runs four-bar half-days on dates Xetra is shut
altogether. Any number of bars per day written as a constant is wrong for some venue on some
date; the counts here are measured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import numpy.typing as npt


def session_ids(index: pd.DatetimeIndex) -> npt.NDArray[np.int64]:
    """A session number per bar, counting from 0, increasing with time.

    Ids rather than dates so that downstream comparisons ("is this bar in a later session than
    that one?") are integer arithmetic on an array the same length as the history.
    """
    if len(index) == 0:
        return np.zeros(0, dtype=np.int64)
    dates = index.normalize().to_numpy()
    changed = np.empty(len(dates), dtype=np.bool_)
    changed[0] = False
    changed[1:] = dates[1:] != dates[:-1]
    return np.cumsum(changed, dtype=np.int64)


def is_session_open(index: pd.DatetimeIndex) -> npt.NDArray[np.bool_]:
    """True on each bar that begins a new session.

    Causal by construction: whether bar ``t`` opens a session depends only on bar ``t - 1``,
    which the engine has already seen. Contrast with "is this the session's *last* bar", which
    cannot be answered at ``t`` without looking at ``t + 1`` -- see
    :mod:`cracktrade.backtest.sessionclose` for how that is handled instead.

    The first bar of the history counts as an open: it is the first bar of whatever session it
    belongs to as far as this history is concerned.
    """
    if len(index) == 0:
        return np.zeros(0, dtype=np.bool_)
    dates = index.normalize().to_numpy()
    opens = np.empty(len(dates), dtype=np.bool_)
    opens[0] = True
    opens[1:] = dates[1:] != dates[:-1]
    return opens


def bars_per_session(index: pd.DatetimeIndex) -> pd.Series:
    """How many bars each session contains, indexed by local session date.

    Includes the final session even if it is partial. Callers that need only *completed*
    sessions -- annualisation, the learned close time -- should drop the last entry themselves,
    or use :func:`median_bars_per_session`, which already does.
    """
    if len(index) == 0:
        return pd.Series(dtype="int64")
    return pd.Series(1, index=index.normalize()).groupby(level=0).sum()


def median_bars_per_session(index: pd.DatetimeIndex) -> int:
    """The typical number of bars in a session, measured from this ticker's own history.

    The number annualisation is built on: a 30-minute Xetra session is 17 bars and a 30-minute
    New York session is 13, so this is a property of the *ticker*, not of the interval, and
    guessing it wrong scales every annualised figure by the ratio of the guess to the truth.

    The median, and computed over completed sessions only. Half-days, early closes and the
    partial session at the end of the history are all real and all shorter than normal; a mean
    would let a handful of them drag the figure down, and including the trailing partial
    session would do so on every single run.
    """
    counts = bars_per_session(index)
    if counts.empty:
        return 0
    completed = counts.iloc[:-1] if len(counts) > 1 else counts
    return max(round(float(completed.median())), 1)


def session_count(index: pd.DatetimeIndex) -> int:
    """How many distinct sessions the history spans.

    The unit thin-history warnings are stated in: "40 sessions of 30-minute bars" tells a
    trader something that "680 bars" does not.
    """
    if len(index) == 0:
        return 0
    return len(bars_per_session(index))
