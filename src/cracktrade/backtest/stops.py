"""Turning the exit rule's stop fields into the arrays vectorbt takes.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 3.7 and 7.4.

Two rules here are defect fixes rather than translation:

* **NaN means "no stop", zero does not.** Verified against the installed vectorbt 1.0.0: an
  ``sl_stop`` of ``0.0`` closes the position on the entry bar, because a zero *distance* is a
  stop sitting exactly at the entry price. The legacy engine ended its ATR calculation with
  ``.fillna(0)`` and therefore stopped out every trade opened during warm-up (defect D7).
* **The ATR stop goes through the indicator registry**, like every other indicator, rather than
  calling ``pandas_ta`` directly and bypassing warm-up handling (defect D6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

from cracktrade.indicators import registry
from cracktrade.indicators.compute import registry_installed
from cracktrade.signals.alignment import causal_shift

if TYPE_CHECKING:
    from cracktrade.config import ExitRule
    from cracktrade.data import MarketData

#: The ATR window is fixed. Legacy hardcoded it (`src/execution/portfolio.py:21`) and the spec
#: keeps it fixed rather than inventing a configuration surface the strategy format never had.
ATR_WINDOW: Final = 14


@dataclass(frozen=True, slots=True)
class StopConfiguration:
    """The stop arguments for one exit rule.

    Attributes:
        sl_stop: stop distance as a fraction. A scalar for percentage stops, a per-bar series
            for ATR stops, and NaN wherever no stop applies.
        sl_trail: whether the stop trails the peak since entry.
        tp_stop: take-profit distance as a fraction, or NaN for none.
        active_stop: which stop type won the priority chain, for reporting.
        shadowed_stops: stop fields that were set but lost the chain, so have no effect.
    """

    sl_stop: pd.Series | float
    sl_trail: bool
    tp_stop: float
    active_stop: str | None
    shadowed_stops: tuple[str, ...]

    @property
    def has_stop(self) -> bool:
        """Whether any stop-loss is active on at least one bar."""
        if isinstance(self.sl_stop, pd.Series):
            return bool(self.sl_stop.notna().any())
        return not np.isnan(self.sl_stop)


def build_stops(rule: ExitRule, data: MarketData) -> StopConfiguration:
    """Resolve ``rule``'s stop fields into vectorbt arguments.

    Exactly one stop type is active, per the section 3.7 priority chain: ATR, then trailing,
    then fixed. The others are reported as shadowed rather than silently dropped.
    """
    active = rule.active_stop

    if active == "atr":
        assert rule.atr_stop_multiplier is not None  # guaranteed by active_stop
        sl_stop: pd.Series | float = atr_stop_series(data, rule.atr_stop_multiplier)
        sl_trail = False
    elif active == "trailing":
        assert rule.trailing_stop_pct is not None
        sl_stop = rule.trailing_stop_pct / 100.0
        sl_trail = True
    elif active == "fixed":
        assert rule.stop_loss_pct is not None
        sl_stop = rule.stop_loss_pct / 100.0
        sl_trail = False
    else:
        sl_stop = np.nan
        sl_trail = False

    take_profit = np.nan if rule.take_profit_pct is None else rule.take_profit_pct / 100.0

    return StopConfiguration(
        sl_stop=sl_stop,
        sl_trail=sl_trail,
        tp_stop=take_profit,
        active_stop=active,
        shadowed_stops=rule.shadowed_stops,
    )


def atr_stop_series(data: MarketData, multiplier: float) -> pd.Series:
    """The ATR stop distance, as a fraction of price, for each bar.

    ``ATR(14) * multiplier / Close``, shifted one bar forward. The shift is the same next-open
    rule as everywhere else: the stop distance applied on bar *t* is built from bar *t-1*'s
    close and ATR, both of which are known before *t* opens.

    The warm-up region stays NaN, which vectorbt reads as "no stop" -- not as a zero-distance
    stop at the entry price (defect D7).
    """
    registry_installed()
    spec = registry.get("atr")
    params = spec.params.model_validate({"window": ATR_WINDOW})

    frame = data.frame
    inputs = {
        "open": frame["Open"],
        "high": frame["High"],
        "low": frame["Low"],
        "close": frame["Close"],
        "volume": frame["Volume"],
        "source": frame["Close"],
    }
    computed = spec.compute(inputs, params)

    if computed is None:
        # Not enough history for ATR at all: no stop on any bar.
        return pd.Series(np.nan, index=data.index, dtype=np.float64)

    assert isinstance(computed, pd.Series)  # atr is single-output
    atr = pd.Series(np.asarray(computed, dtype=np.float64), index=data.index)
    return causal_shift(atr * multiplier / frame["Close"], 1)
