"""Definedness tracking and the grammar rules that are about meaning rather than causality.

The centrepiece is defect D15 (audit finding A1): relying on "a comparison against NaN is
False" is not sufficient, because ``~`` inverts it and because several indicators are undefined
on the majority of their bars by design.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData, prepare_frame
from cracktrade.errors import SignalSyntaxError
from cracktrade.indicators import IndicatorNamespace, compute_indicators
from cracktrade.signals import parse_expression, prepare_signal
from tests.factories import make_ohlcv

BARS = 300


def market_data(bars: int = BARS) -> MarketData:
    frame = prepare_frame(make_ohlcv(bars), ticker="TEST", today=date(2100, 1, 1))
    return MarketData(
        ticker="TEST",
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )


def strategy_with(indicators: list[dict[str, Any]], signal: str = "close > open") -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "s"},
            "universe": {
                "ticker": "TEST",
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10_000.0,
                "commission_pct": 0.1,
                "slippage_pct": 0.05,
            },
            "indicators": indicators,
            "entry_variants": [{"name": "e", "signal": signal}],
            "exit_variants": [{"name": "x", "stop_loss_pct": 5.0}],
        }
    )


def namespace_with(series: dict[str, pd.Series], warmup: int) -> IndicatorNamespace:
    index = next(iter(series.values())).index
    assert isinstance(index, pd.DatetimeIndex)
    return IndicatorNamespace(series=series, warmup=warmup, index=index)


def hand_built_namespace() -> IndicatorNamespace:
    """Ten bars with an interior NaN hole at positions 5 and 6."""
    index = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=10, freq="D").to_numpy())
    gappy = pd.Series([np.nan] * 3 + [50.0, 60.0, np.nan, np.nan, 20.0, 25.0, 70.0], index=index)
    close = pd.Series(np.arange(10, dtype=np.float64) + 100.0, index=index)
    return namespace_with({"close": close, "gappy": gappy}, warmup=3)


# --------------------------------------------------------------- D15: definedness tracking


def test_negation_does_not_turn_undefined_into_a_signal() -> None:
    """Defect D15. ``~(x > c)`` was True on every bar where ``x`` was NaN."""
    namespace = hand_built_namespace()

    signal = prepare_signal("~(gappy > 30)", namespace)

    # Bars 5 and 6 are NaN, so the signals they would produce (at 6 and 7) must be False.
    assert not bool(signal.series.iloc[6])
    assert not bool(signal.series.iloc[7])
    # Bar 7 holds 20, so ~(20 > 30) is genuinely true and lands on bar 8.
    assert bool(signal.series.iloc[8])


def test_a_signal_is_never_true_where_its_inputs_are_undefined() -> None:
    namespace = hand_built_namespace()

    for expression in ("gappy > 30", "~(gappy > 30)", "(gappy < 30) | ~(gappy > 90)"):
        signal = prepare_signal(expression, namespace)
        assert not bool((signal.series & ~signal.defined).any()), expression


def test_arithmetic_propagates_undefinedness() -> None:
    """A NaN anywhere in the arithmetic makes the whole comparison undefined."""
    namespace = hand_built_namespace()

    signal = prepare_signal("~(close - gappy > 1000)", namespace)

    assert not bool(signal.series.iloc[6])
    assert not bool(signal.series.iloc[7])


def test_constants_are_defined_everywhere() -> None:
    """A literal must not drag the mask down; only names carry definedness."""
    namespace = hand_built_namespace()

    signal = prepare_signal("close > 100.5", namespace)

    assert bool(signal.defined.iloc[1:].all())


@pytest.mark.parametrize(
    ("indicator", "output"),
    [("psar", "sar_psarl"), ("supertrend", "st_supertl")],
)
def test_indicators_undefined_by_design_are_reported(indicator: str, output: str) -> None:
    """``psar_psarl`` and ``supertrend_supertl`` hold values only in one trend direction.

    They are NaN on the majority of bars, which is correct behaviour and not an error -- but
    a strategy built on them must know, and must not trade the gaps.
    """
    data = market_data()
    name = output.split("_")[0]
    expression = f"~(close < {output})"
    strategy = strategy_with([{"name": name, "type": indicator}], signal=expression)
    namespace = compute_indicators(strategy, data)

    signal = prepare_signal(expression, namespace)

    assert signal.defined_pct < 100.0, "expected this output to be undefined on some bars"
    assert not bool((signal.series & ~signal.defined).any())


def test_defined_pct_ignores_the_warmup_region() -> None:
    namespace = hand_built_namespace()

    signal = prepare_signal("gappy > 30", namespace)

    # Post-warm-up bars are 3..9; the shift moves the mask forward one, leaving bars 6 and 7
    # undefined out of the seven reported.
    assert 0.0 < signal.defined_pct < 100.0


def test_signal_count_counts_true_bars() -> None:
    namespace = hand_built_namespace()

    signal = prepare_signal("gappy > 30", namespace)

    assert signal.signal_count == int(signal.series.sum())


# ------------------------------------------------------------------- A5: float equality


@pytest.mark.parametrize(
    "expression",
    ["close == open", "close != open", "(close == high) & (low > 0)", "close + 1 == high"],
)
def test_equality_between_two_series_is_rejected(expression: str) -> None:
    with pytest.raises(SignalSyntaxError, match="never true in floating point"):
        parse_expression(expression)


@pytest.mark.parametrize("expression", ["direction == 1", "flag != 0", "1 == direction"])
def test_equality_against_a_literal_is_allowed(expression: str) -> None:
    """Several indicators emit integer-valued flags, where equality is exact and intended."""
    assert parse_expression(expression).names
