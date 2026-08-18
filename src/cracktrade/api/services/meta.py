"""Static facts the client needs before it can render anything.

Everything here is read from the engine rather than restated. The trade floor, the significance
bar and the instability threshold are the same constants the verdict is computed from, so a UI
that says "16 of 20 needed" and an engine that suppressed at 20 cannot drift apart. Restating
them in TypeScript would guarantee that they eventually do.
"""

from __future__ import annotations

from dataclasses import dataclass

from cracktrade import __version__
from cracktrade.domain import INSTABILITY_THRESHOLD, MIN_TRADES_TO_JUDGE, SIGNIFICANCE
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.describe import IndicatorDescription, describe_catalogue
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, OBJECTIVES
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme

#: Exit fields in stop-priority order (spec section 3.7). ``stop_priority`` is what lets the
#: editor say which stop shadows which without hard-coding the chain a second time.
EXIT_FIELDS: tuple[tuple[str, int | None], ...] = (
    ("signal", None),
    ("atr_stop_multiplier", 1),
    ("trailing_stop_pct", 2),
    ("stop_loss_pct", 3),
    ("take_profit_pct", None),
    ("min_holding_days", None),
    ("max_holding_days", None),
)

#: The "what this cannot tell you" banner. Kept here because they are engine limits, not
#: copywriting: each one is a residual bias spec section 2.7 records as outside engine control.
LIMITS: tuple[str, ...] = (
    "Ticker selection is hindsight - you chose the symbol knowing its history.",
    "Prices are retroactively adjusted; the same backtest run months apart uses different data.",
    "Numbers written inside a signal expression are not optimizable.",
    "One ticker per strategy. Long only.",
)


@dataclass(frozen=True, slots=True)
class ExitField:
    """One exit field, and where it sits in the stop priority chain."""

    name: str
    stop_priority: int | None


@dataclass(frozen=True, slots=True)
class RunDefaults:
    """Launch parameters a client should pre-fill."""

    objective: str
    epochs: int
    folds: int | None = None
    scheme: str | None = None
    cache: bool | None = None
    #: The trade floor, absent for a backtest, which does not search and has no objective to
    #: constrain.
    min_trades: int | None = None
    min_trades_per_year: float | None = None


@dataclass(frozen=True, slots=True)
class Meta:
    """Everything a client needs before its first render."""

    engine_version: str
    trade_floor: int
    significance: float
    instability_threshold: float
    objectives: tuple[str, ...]
    fold_schemes: tuple[str, ...]
    backtest_defaults: RunDefaults
    optimize_defaults: RunDefaults
    walk_forward_defaults: RunDefaults
    indicators: tuple[IndicatorDescription, ...]
    exit_fields: tuple[ExitField, ...]
    limits: tuple[str, ...]


def describe_engine() -> Meta:
    """Assemble the metadata payload from the engine's own constants and registry."""
    install()
    return Meta(
        engine_version=__version__,
        trade_floor=MIN_TRADES_TO_JUDGE,
        significance=SIGNIFICANCE,
        instability_threshold=INSTABILITY_THRESHOLD,
        objectives=tuple(OBJECTIVES),
        fold_schemes=tuple(scheme.value for scheme in FoldScheme),
        backtest_defaults=RunDefaults(objective=DEFAULT_OBJECTIVE, epochs=0),
        optimize_defaults=RunDefaults(
            objective=DEFAULT_OBJECTIVE,
            epochs=10,
            cache=True,
            min_trades=DEFAULT_TRADE_FLOOR.minimum,
            min_trades_per_year=DEFAULT_TRADE_FLOOR.per_year,
        ),
        walk_forward_defaults=RunDefaults(
            objective=DEFAULT_OBJECTIVE,
            epochs=10,
            folds=DEFAULT_FOLDS,
            scheme=FoldScheme.ANCHORED.value,
            min_trades=DEFAULT_TRADE_FLOOR.minimum,
            min_trades_per_year=DEFAULT_TRADE_FLOOR.per_year,
        ),
        indicators=describe_catalogue(),
        exit_fields=tuple(ExitField(name=name, stop_priority=rank) for name, rank in EXIT_FIELDS),
        limits=LIMITS,
    )
