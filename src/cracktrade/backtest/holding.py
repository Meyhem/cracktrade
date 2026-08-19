"""Holding-period rules, evaluated inside the simulation.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7.2.

The rules must key off **realised positions**, not entry signals: a signal that fires while
already in a position opens nothing, and scheduling a time exit from it refers to a trade that
never happened (defect D8).

Doing that from outside the simulation is circular -- the exits decide when a position closes,
which decides which later entries are realised, which is what the rules are computed from. An
earlier implementation closed the circle by iterating to a fixed point. It was replaced because
the recursion turns out to be a *chain* rather than a contraction: each pass resolves exactly one
more trade, so a history with 200 trades needed 200 full simulations. Correct, and
far too slow to sit inside an optimizer loop.

vectorbt's ``signal_func_nb`` hook removes the circularity instead of iterating around it. The
function is called once per bar *during* the simulation, with ``position_now`` already reflecting
everything that happened earlier, so the holding rules are evaluated against realised positions
by construction, in a single pass.

The function also decides entry and exit exclusively, so the two can never conflict on one bar
and vectorbt's conflict resolution never comes into play.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numba import njit  # type: ignore[import-untyped]
from vectorbt.base.reshape_fns import flex_select_auto_nb

if TYPE_CHECKING:
    import numpy.typing as npt

    from cracktrade.config import ExitRule

#: Sentinel for "the entry bar of the current position is not yet known".
_UNSET = -1


@njit  # type: ignore[untyped-decorator]
def holding_signal_nb(
    c: Any,
    entries: npt.NDArray[np.bool_],
    exits: npt.NDArray[np.bool_],
    entry_bar: npt.NDArray[np.int64],
    minimum: int,
    maximum: int,
) -> tuple[bool, bool, bool, bool]:
    """Decide this bar's signals, given how long the open position has been held.

    Returns vectorbt's ``(long_entry, long_exit, short_entry, short_exit)``. The engine is
    long-only, so the last two are always False.

    ``entry_bar`` is mutable per-column state, reset whenever the position is flat. It is
    written on the first bar at which a position is *observed*, which is the bar after the one
    the entry order filled on -- ``position_now`` reflects the state before this bar's order.

    Stops are not consulted here. They are applied by the simulation independently and always
    fire, including inside the minimum-holding window: a stop-loss disabled for the first N days
    is not a stop-loss (spec section 7.2).
    """
    is_entry = flex_select_auto_nb(entries, c.i, c.col, c.flex_2d)
    is_exit = flex_select_auto_nb(exits, c.i, c.col, c.flex_2d)

    if c.position_now == 0:
        entry_bar[c.col] = _UNSET
        return is_entry, False, False, False

    if entry_bar[c.col] == _UNSET:
        entry_bar[c.col] = c.i - 1

    held = c.i - entry_bar[c.col]

    if maximum > 0 and held >= maximum:
        return False, True, False, False

    if minimum > 0 and held < minimum:
        return False, False, False, False

    return False, is_exit, False, False


def holding_state() -> npt.NDArray[np.int64]:
    """Fresh per-column state for one simulation.

    Allocated per call rather than reused: the array is mutated during simulation, and sharing
    it between runs would leak one run's last position into the next.
    """
    return np.full(1, _UNSET, dtype=np.int64)


def as_signal_column(series: Any) -> npt.NDArray[np.bool_]:
    """A boolean series as the 2-D column array the flexible indexer expects."""
    return np.asarray(series, dtype=np.bool_).reshape(-1, 1)


def holding_bounds(rule: ExitRule) -> tuple[int, int]:
    """``(minimum, maximum)`` holding period in **bars**, with 0 meaning "no bound".

    Bars is what this was always counting; on a daily strategy a bar is a trading day, which is
    why the ``_days`` spelling of these fields was accurate until bars stopped being days.
    """
    return rule.min_holding or 0, rule.max_holding or 0
