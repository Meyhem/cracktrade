"""Walk-forward fold generation.

Normative reference: ``docs/ENGINE_SPEC.md`` section 12.1.

A single contiguous test window is a single draw. Whether it happens to contain a bull run or
2022 dominates the result, and neither outcome carries information about the strategy. It is
also a one-shot resource that the intended workflow destroys: the user reads the number, adjusts
the strategy, re-runs, and the "out-of-sample" label quietly becomes false.

Folds turn one number into a distribution. A strategy positive in six of six folds and one whose
entire edge sits in fold three are different objects, and no aggregate can tell them apart.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from cracktrade.errors import OptimizationError
from cracktrade.optimize.windows import Split, TestWindow, TrainWindow, effective_min_test_bars

if TYPE_CHECKING:
    from cracktrade.data import MarketData

#: Folds generated when the caller does not say.
DEFAULT_FOLDS = 6


class FoldScheme(StrEnum):
    """How the training window moves between folds."""

    #: Train always starts at the first bar and grows. Uses all available history, and assumes
    #: old regimes stay informative.
    ANCHORED = "anchored"
    #: Train is a fixed-length window that slides forward. Adapts to regime change, at the cost
    #: of discarding history.
    ROLLING = "rolling"


def walk_forward_splits(
    data: MarketData,
    *,
    warmup: int,
    folds: int = DEFAULT_FOLDS,
    scheme: FoldScheme = FoldScheme.ANCHORED,
    train_fraction: float = 0.5,
    min_test_bars: int = 30,
) -> tuple[Split, ...]:
    """Divide ``data`` into ``folds`` successive train/test pairs.

    The first ``train_fraction`` of history seeds the initial training window; what remains is
    cut into ``folds`` equal test segments, each preceded by everything before it.

    Args:
        data: the full history.
        warmup: bars the widest candidate strategy needs. Each test window is extended backwards
            by this much so it can trade from its first scored bar.
        folds: how many train/test pairs to produce.
        scheme: anchored or rolling training window.
        train_fraction: share of history reserved to seed the first training window.
        min_test_bars: refuse a division whose test segments are smaller than this.

    Raises:
        OptimizationError: the history cannot support the requested division.
    """
    if folds < 1:
        msg = f"folds must be at least 1, got {folds}"
        raise OptimizationError(msg)

    bars = len(data)
    initial_train = int(bars * train_fraction)
    remaining = bars - initial_train
    segment = remaining // folds
    min_test_bars = effective_min_test_bars(data, min_test_bars)

    if segment < min_test_bars:
        # Naming the interval matters here: "30 bars per fold" reads as adequate until the
        # reader remembers a bar is half an hour, at which point it is under two sessions.
        msg = (
            f"{bars} {data.interval.value} bars split into {folds} fold(s) leaves {segment} "
            f"bars per test segment, below the {min_test_bars} required. Use fewer folds, "
            f"widen the date range, or lower the train fraction"
        )
        raise OptimizationError(msg)

    if initial_train <= warmup:
        msg = (
            f"the initial training window is {initial_train} bars, which is not more than the "
            f"{warmup}-bar indicator warm-up. Widen the date range or use shorter indicator "
            f"windows"
        )
        raise OptimizationError(msg)

    splits: list[Split] = []
    for fold in range(folds):
        test_start = initial_train + fold * segment
        # The final fold absorbs the remainder, so no bars are silently discarded.
        test_end = bars if fold == folds - 1 else test_start + segment

        train_start = 0 if scheme is FoldScheme.ANCHORED else max(0, test_start - initial_train)
        if test_start - train_start <= warmup:
            msg = (
                f"fold {fold + 1} has {test_start - train_start} training bars, not more than "
                f"the {warmup}-bar warm-up. Use fewer folds or a longer history"
            )
            raise OptimizationError(msg)

        splits.append(
            Split(
                train=TrainWindow(data=data.slice(train_start, test_start)),
                test=TestWindow(data=data.slice(test_start - warmup, test_end), offset=warmup),
            )
        )

    return tuple(splits)
