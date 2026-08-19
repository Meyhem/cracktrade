"""Train and test windows, kept apart by the type system.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 2.5 and 9.4.

This is the correctness centrepiece of the optimizer, and the defect it fixes is the worst one in
the register. Legacy computed a train/test split, fit on train, and then reported on the **full
history** (defect D2). ``df_test`` was assigned and never read. Every headline number the system
produced was measured on data the optimizer had already fitted to.

A comment saying "do not score on test" is not a control; someone will eventually call the wrong
function. So the two windows are *distinct types*. The fitness function's signature accepts a
:class:`TrainWindow` and nothing else, so handing it a :class:`TestWindow` fails mypy rather than
silently producing an inflated number. Neither type has a public constructor that takes raw
data -- both come only from :func:`split`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cracktrade.data.sessions import median_bars_per_session
from cracktrade.errors import OptimizationError

if TYPE_CHECKING:
    from cracktrade.data import MarketData

#: Sessions a test window must span at minimum, on intraday data. Thirty bars is a sensible
#: floor when a bar is a day; at 30 minutes it is 1.8 Xetra sessions, which is not a sample of
#: anything -- an overnight gap either falls inside it or does not, and that alone decides the
#: result. Five sessions is still small; it is the point below which the window stops being a
#: window and becomes an anecdote.
MIN_TEST_SESSIONS = 5


def effective_min_test_bars(data: MarketData, requested: int) -> int:
    """Raise a bar-denominated floor to a session-aware one on intraday data.

    Bar counts carry over from daily to intraday arithmetically and not at all in meaning. The
    caller's figure is kept as a lower bound rather than replaced, so a caller asking for more
    still gets more.
    """
    if not data.interval.is_intraday:
        return requested
    return max(requested, MIN_TEST_SESSIONS * median_bars_per_session(data.index))


@dataclass(frozen=True, slots=True)
class TrainWindow:
    """History the optimizer is allowed to fit to.

    Attributes:
        data: the bars, starting at the beginning of the history.
        offset: always 0. Present so both window types share a shape.
    """

    data: MarketData
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TestWindow:
    """History reserved for the single, final evaluation.

    Attributes:
        data: the test bars **preceded by** enough train bars to warm up the indicators. Those
            bars are past data relative to every test bar, so using them is correct rather than
            leakage -- an indicator has to see history to have a value at all.
        offset: how many leading bars are warm-up. Metrics are computed from here onward, so the
            prefix contributes indicator state and nothing else.
    """

    #: Not a pytest test class. The name is right for the domain and wrong for pytest's
    #: collector, which matches on the "Test" prefix; this opts out.
    __test__ = False

    data: MarketData
    offset: int

    @property
    def scored_bars(self) -> int:
        """Bars the reported metrics actually cover."""
        return len(self.data) - self.offset


@dataclass(frozen=True, slots=True)
class Split:
    """One train/test division of a history."""

    train: TrainWindow
    test: TestWindow

    @property
    def split_bar(self) -> int:
        """Index in the original history where the test region begins."""
        return len(self.train.data)


def split(
    data: MarketData,
    *,
    train_fraction: float,
    warmup: int,
    min_test_bars: int = 30,
) -> Split:
    """Divide ``data`` into a contiguous train window and a test window.

    The division is by position and never shuffled: shuffling time series data would let the
    optimizer see the future in a way no amount of type discipline could catch.

    Args:
        data: the full history.
        train_fraction: share of bars the optimizer may fit to.
        warmup: bars the strategy's indicators need. The test window is extended backwards by
            this much so that it can trade from its first scored bar.
        min_test_bars: refuse a split that leaves less than this to evaluate on. Raised to a
            session-aware floor on intraday data -- see :func:`effective_min_test_bars`.

    Raises:
        OptimizationError: the history cannot support the requested split.
    """
    bars = len(data)
    split_bar = int(bars * train_fraction)
    min_test_bars = effective_min_test_bars(data, min_test_bars)

    if split_bar <= warmup:
        msg = (
            f"a {train_fraction:.0%} train split of {bars} bars leaves {split_bar} bars to fit "
            f"on, which is not more than the {warmup}-bar indicator warm-up. Widen the date "
            f"range or use shorter indicator windows"
        )
        raise OptimizationError(msg)

    if bars - split_bar < min_test_bars:
        msg = (
            f"a {train_fraction:.0%} train split of {bars} {data.interval.value} bars leaves "
            f"only {bars - split_bar} bars to evaluate on, below the {min_test_bars} required "
            f"({MIN_TEST_SESSIONS} sessions). A test window this small cannot support a "
            f"conclusion; widen the date range"
            if data.interval.is_intraday
            else (
                f"a {train_fraction:.0%} train split of {bars} bars leaves only "
                f"{bars - split_bar} bars to evaluate on, below the {min_test_bars} required. "
                f"A test window this small cannot support a conclusion; widen the date range"
            )
        )
        raise OptimizationError(msg)

    return Split(
        train=TrainWindow(data=data.head(split_bar)),
        test=TestWindow(data=data.slice(split_bar - warmup, bars), offset=warmup),
    )
