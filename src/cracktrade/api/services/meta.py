"""Static facts the client needs before it can render anything.

Everything here is read from the engine rather than restated. The trade floor, the significance
bar and the instability threshold are the same constants the verdict is computed from, so a UI
that says "16 of 20 needed" and an engine that suppressed at 20 cannot drift apart. Restating
them in TypeScript would guarantee that they eventually do.
"""

from __future__ import annotations

from dataclasses import dataclass

from cracktrade import __version__
from cracktrade.config import Interval
from cracktrade.domain import INSTABILITY_THRESHOLD, MIN_TRADES_TO_JUDGE, SIGNIFICANCE
from cracktrade.evolution import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_SEGMENTS,
    GaSettings,
    library_warmup,
)
from cracktrade.evolution.protocol import MIN_SEGMENT_SESSIONS
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.describe import IndicatorDescription, describe_catalogue
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, OBJECTIVES
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme

#: Exit fields in stop-priority order (spec section 3.7), each with its place in the stop
#: priority chain and whether it exists only on daily bars. ``stop_priority`` is what lets the
#: editor say which stop shadows which without hard-coding the chain a second time;
#: ``daily_only`` is what lets it stop offering a field the engine would refuse -- the ``_days``
#: holding bounds are rejected outright on an intraday strategy (spec section 3.3), where the
#: ``_bars`` spelling is the only one that means anything.
EXIT_FIELDS: tuple[tuple[str, int | None, bool], ...] = (
    ("signal", None, False),
    ("atr_stop_multiplier", 1, False),
    ("trailing_stop_pct", 2, False),
    ("stop_loss_pct", 3, False),
    ("take_profit_pct", None, False),
    ("min_holding_days", None, True),
    ("max_holding_days", None, True),
    ("min_holding_bars", None, False),
    ("max_holding_bars", None, False),
)

#: The "what this cannot tell you" banner. Kept here because they are engine limits, not
#: copywriting: each one is a residual bias spec section 2.7 records as outside engine control.
#: The engine's own search defaults, read rather than restated.
_GA = GaSettings()

#: Calendar days per trading session, near enough to turn a provider reach in days into a
#: session count. The exchange decides the true figure and it varies by a few days a year; this
#: only has to be right enough to separate "nowhere near enough" from "comfortably enough",
#: which is the only distinction :func:`_evolvable` draws.
_DAYS_PER_SESSION = 7 / 5


def _evolvable(interval: Interval) -> bool:
    """Whether an evolution could be divided at all at this interval.

    An evolution needs ``DEFAULT_SEGMENTS`` segments and a holdout, each at least
    ``MIN_SEGMENT_SESSIONS`` sessions (spec section 12.12), behind the block library's warm-up.
    Against the floor of three this is true of every interval the engine offers, so the field is
    currently ``True`` throughout and no client filters anything out.

    It is kept, and kept computed rather than listed, precisely because that is a property of
    today's floor and not of the design: raise the floor or the segment count and the shortest
    intervals drop out again on their own, rather than leaving a stale list behind for a client
    to offer a run that cannot start.

    The warm-up is deliberately not subtracted here. Its size in *sessions* depends on the
    exchange -- two hundred bars is twelve Xetra sessions at 30m and fifteen New York ones -- and
    a client hint has no ticker to ask. A range too narrow once the warm-up is taken out is
    refused by :func:`~cracktrade.evolution.protocol.split_for_evolution` with the arithmetic
    named, which is the right place for a check that needs the data to be exact.
    """
    if interval.max_lookback is None:
        return True
    needed = MIN_SEGMENT_SESSIONS * (DEFAULT_SEGMENTS + 1)
    return interval.max_lookback.days / _DAYS_PER_SESSION >= needed


LIMITS: tuple[str, ...] = (
    "Ticker selection is hindsight - you chose the symbol knowing its history.",
    "Prices are retroactively adjusted; the same backtest run months apart uses different data.",
    "Numbers written inside a signal expression are not optimizable.",
    "One ticker per strategy. Long only.",
)


@dataclass(frozen=True, slots=True)
class ExitField:
    """One exit field, where it sits in the stop priority chain, and where it is accepted."""

    name: str
    stop_priority: int | None
    daily_only: bool


@dataclass(frozen=True, slots=True)
class IntervalOption:
    """One bar width the engine accepts, and the one fact that constrains choosing it.

    ``max_lookback_days`` is the provider's reach, not a preference: 15-minute and 30-minute
    bars are served for about 55 days and no date range wider than that can be fetched at all
    (spec section 4.2). A client that has this can refuse the range in the form, where the user
    can still fix it, rather than letting the engine refuse it after the launch.

    ``evolvable`` answers a question only the engine can: whether the deepest history this
    interval can reach is enough to cut into the segments an evolution selects on
    (spec section 12.12). It is false at 15m and 30m, where the answer is not close.
    """

    value: str
    intraday: bool
    max_lookback_days: int | None
    evolvable: bool


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
    #: Evolution only. ``population * generations`` is the search budget, and the trial count
    #: the deflated Sharpe divides by -- so a client showing this is showing the cost in
    #: credibility, not only in minutes.
    population: int | None = None
    generations: int | None = None
    segments: int | None = None
    holdout_fraction: float | None = None


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
    evolve_defaults: RunDefaults
    #: How many bars of warm-up the block library can demand. A chassis whose date range is
    #: not comfortably longer than this cannot be evolved in, and the client says so before
    #: the launch rather than after the failure.
    evolution_warmup_bars: int
    indicators: tuple[IndicatorDescription, ...]
    exit_fields: tuple[ExitField, ...]
    #: Bar widths, in the enum's own order. Read from the engine rather than restated, for the
    #: reason this module exists: a client offering an interval the engine does not accept, or
    #: omitting one it does, is a drift no test on either side would catch.
    intervals: tuple[IntervalOption, ...]
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
        evolve_defaults=RunDefaults(
            objective=DEFAULT_OBJECTIVE,
            # Evolution has no epochs: the genome carries structure and parameters together and
            # is searched in one level, so the budget is population x generations (spec 16.4).
            epochs=0,
            cache=True,
            min_trades=DEFAULT_TRADE_FLOOR.minimum,
            min_trades_per_year=DEFAULT_TRADE_FLOOR.per_year,
            population=_GA.population,
            generations=_GA.generations,
            segments=DEFAULT_SEGMENTS,
            holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
        ),
        evolution_warmup_bars=library_warmup(),
        indicators=describe_catalogue(),
        exit_fields=tuple(
            ExitField(name=name, stop_priority=rank, daily_only=daily_only)
            for name, rank, daily_only in EXIT_FIELDS
        ),
        intervals=tuple(
            IntervalOption(
                value=interval.value,
                intraday=interval.is_intraday,
                max_lookback_days=(
                    interval.max_lookback.days if interval.max_lookback is not None else None
                ),
                evolvable=_evolvable(interval),
            )
            for interval in Interval
        ),
        limits=LIMITS,
    )
