"""The one place in the engine where a series is shifted in time.

Normative reference: ``docs/ENGINE_SPEC.md`` section 2.3. The legacy engine applied ``.shift``
in five separate places, each an independent opportunity for the sign to be wrong. Here there
is exactly one, it refuses negative periods, and a repository test asserts that no other module
calls ``.shift`` at all.

A negative shift pulls a future value onto an earlier bar. There is no legitimate use of one in
a backtest engine, so it raises rather than warning.
"""

from __future__ import annotations

import pandas as pd

from cracktrade.errors import CausalityViolationError


def causal_shift[SeriesOrFrame: (pd.Series, pd.DataFrame)](
    obj: SeriesOrFrame, periods: int
) -> SeriesOrFrame:
    """Move values ``periods`` bars forward in time.

    ``causal_shift(s, 1)`` puts the value observed at the close of bar *D* onto bar *D+1*,
    which is what makes next-open execution honest: the decision is taken with information
    available at *D*, and acted on at the open of *D+1*.

    Args:
        obj: the series or frame to shift.
        periods: how many bars forward. Must not be negative.

    Raises:
        CausalityViolationError: ``periods`` is negative, which would move a future value into
            the past.
    """
    if periods < 0:
        msg = (
            f"refusing to shift by {periods}: a negative shift moves a future value onto an "
            f"earlier bar, which is look-ahead bias"
        )
        raise CausalityViolationError(msg)
    return obj.shift(periods)
