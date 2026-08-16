"""Phase 4: progress reporting and cooperative cancellation.

The property that matters most is the one that sounds trivial: **hooks do not change results.**
A progress callback exists so a caller can draw a bar, and if installing one perturbed the
search -- by consuming randomness, by changing how scipy iterates, by any route at all -- then
the numbers a user watched being computed would not be the numbers they would have got. That is
asserted here rather than reasoned about.

The rest is about stopping honestly. A cancelled run produces nothing, because a search halted
at generation three is not a cheaper search, it is an unfinished one.
"""

from __future__ import annotations

import pytest

from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.data import MarketData
from cracktrade.errors import RunCancelled
from cracktrade.optimize import optimize
from cracktrade.validate import FoldScheme, walk_forward
from tests.test_metrics import trending_market
from tests.test_optimize import strategy_with

EPOCHS = 3
SEED = 20240517


@pytest.fixture(scope="module")
def market() -> MarketData:
    return trending_market(bars=400)


# --------------------------------------------------------------------------- no side effects


def test_progress_hooks_do_not_change_the_result(market: MarketData) -> None:
    """The headline guarantee. Watching a run must not alter it."""
    strategy = strategy_with()
    seen: list[tuple[str, float]] = []

    without = optimize(strategy, market, epochs=EPOCHS, seed=SEED, workers=1)
    with_hooks = optimize(
        strategy,
        market,
        epochs=EPOCHS,
        seed=SEED,
        workers=1,
        control=RunControl(on_progress=lambda stage, pct: seen.append((stage, pct))),
    )

    assert seen, "the hook was never called, so this proves nothing"
    assert with_hooks.optimized_yaml == without.optimized_yaml
    assert with_hooks.test_metrics == without.test_metrics
    assert with_hooks.changes == without.changes


def test_a_stop_callback_that_never_stops_changes_nothing(market: MarketData) -> None:
    """Asking every generation "should I stop?" must not itself alter the search."""
    strategy = strategy_with()
    without = optimize(strategy, market, epochs=EPOCHS, seed=SEED, workers=1)
    with_check = optimize(
        strategy,
        market,
        epochs=EPOCHS,
        seed=SEED,
        workers=1,
        control=RunControl(should_stop=lambda: False),
    )
    assert with_check.optimized_yaml == without.optimized_yaml
    assert with_check.test_metrics == without.test_metrics


# --------------------------------------------------------------------------- progress


def test_progress_is_reported_within_bounds(market: MarketData) -> None:
    seen: list[tuple[str, float]] = []
    optimize(
        strategy_with(),
        market,
        epochs=EPOCHS,
        seed=SEED,
        workers=1,
        control=RunControl(on_progress=lambda stage, pct: seen.append((stage, pct))),
    )
    assert seen
    assert all(0.0 <= percent <= 100.0 for _, percent in seen)
    assert all(stage for stage, _ in seen)


def test_progress_never_goes_backwards_across_folds(market: MarketData) -> None:
    """A bar that restarts once per fold reports six times and means nothing.

    Each fold's search reports 0-100% of *itself*, so the fold loop has to map that into the
    fold's own slice of the run. This is what checks the mapping is right.
    """
    percents: list[float] = []
    walk_forward(
        strategy_with(),
        market,
        folds=2,
        scheme=FoldScheme.ANCHORED,
        epochs=EPOCHS,
        seed=SEED,
        workers=1,
        control=RunControl(on_progress=lambda _stage, pct: percents.append(pct)),
    )
    assert percents
    assert percents == sorted(percents), "progress went backwards"
    assert percents[-1] == pytest.approx(100.0)


# --------------------------------------------------------------------------- cancellation


def test_a_cancelled_search_raises_rather_than_returning_a_partial_result(
    market: MarketData,
) -> None:
    """No result at all is the honest outcome: the search never finished choosing."""
    generations: list[int] = []

    def stop_after_one() -> bool:
        generations.append(len(generations) + 1)
        return len(generations) >= 1

    with pytest.raises(RunCancelled):
        optimize(
            strategy_with(),
            market,
            epochs=50,
            seed=SEED,
            workers=1,
            control=RunControl(should_stop=stop_after_one),
        )

    # Stopped early rather than running the full budget.
    assert len(generations) < 50


def test_a_cancelled_walk_forward_stops_between_folds(market: MarketData) -> None:
    """Cancelling inside a fold would leave a half-searched fold behind."""
    folds_started: list[str] = []

    def record(stage: str, _percent: float) -> None:
        if stage.startswith("fold ") and " - " not in stage:
            folds_started.append(stage)

    with pytest.raises(RunCancelled):
        walk_forward(
            strategy_with(),
            market,
            folds=3,
            scheme=FoldScheme.ANCHORED,
            epochs=EPOCHS,
            seed=SEED,
            workers=1,
            control=RunControl(
                on_progress=record,
                should_stop=lambda: len(folds_started) >= 2,
            ),
        )
    assert len(folds_started) == 2


# --------------------------------------------------------------------------- the control object


def test_the_default_control_does_nothing() -> None:
    """``RunControl()`` is the no-hooks case, so callers never thread ``None`` downwards."""
    assert NO_CONTROL.cancelled is False
    NO_CONTROL.progress("anything", 50.0)
    NO_CONTROL.raise_if_cancelled()


def test_percentages_are_clamped() -> None:
    """A caller's arithmetic must not be able to drive a progress bar past its end."""
    seen: list[float] = []
    control = RunControl(on_progress=lambda _stage, pct: seen.append(pct))
    control.progress("over", 140.0)
    control.progress("under", -20.0)
    assert seen == [100.0, 0.0]


def test_a_raising_progress_hook_is_not_swallowed() -> None:
    """A broken callback is the caller's bug, and hiding it leaves a silent run."""

    def broken(_stage: str, _percent: float) -> None:
        raise ValueError("callback is broken")

    with pytest.raises(ValueError, match="broken"):
        RunControl(on_progress=broken).progress("stage", 1.0)


def test_cancellation_is_checked_each_time_it_is_asked() -> None:
    """The flag is read live, so a cancel arriving mid-run is seen on the next check."""
    answers = iter([False, False, True])
    control = RunControl(should_stop=lambda: next(answers))
    assert control.cancelled is False
    control.raise_if_cancelled()
    with pytest.raises(RunCancelled):
        control.raise_if_cancelled()
