"""Phase 4: the indicator registry and namespace construction (spec section 5)."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

import cracktrade.indicators  # noqa: F401 - installs the catalogue
from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData, prepare_frame
from cracktrade.errors import (
    IndicatorError,
    StrategyValidationError,
    UnknownIndicatorError,
)
from cracktrade.indicators import compute_indicators, registry
from cracktrade.indicators.spec import Causality, IndicatorSpec
from cracktrade.strategy import build_strategy
from tests.factories import make_ohlcv

BARS = 400


@pytest.fixture(scope="module")
def data() -> MarketData:
    frame = prepare_frame(make_ohlcv(BARS), ticker="TEST", today=date(2100, 1, 1))
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
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "indicators": indicators,
            "entry": {"signal": signal},
            "exit": {"max_holding_days": 5},
        }
    )


# ------------------------------------------------------------------ registry


def test_catalogue_is_populated() -> None:
    assert registry.count() > 40


def test_every_registered_indicator_is_causal() -> None:
    assert all(spec.causality is Causality.CAUSAL for spec in registry.all_specs())


def test_non_causal_indicators_cannot_be_registered() -> None:
    spec = IndicatorSpec(
        type="crystal_ball",
        params=registry.get("sma").params,
        compute=lambda series, params: series["source"],
        warmup=lambda params: 0,
        description="peeks",
        causality=Causality.FORWARD_PROJECTED,
    )
    with pytest.raises(ValueError, match="only computes values that depend on past bars"):
        registry.register(spec)


def test_unknown_type_suggests_a_real_one() -> None:
    with pytest.raises(UnknownIndicatorError, match="did you mean 'sma'"):
        registry.get("smaa")


def test_unknown_type_without_a_near_match_lists_the_catalogue() -> None:
    with pytest.raises(UnknownIndicatorError, match="cracktrade indicators"):
        registry.get("quantum_flux")


def test_duplicate_registration_is_refused() -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(registry.get("sma"))


# ------------------------------------------------------------------ single output


def test_sma_matches_a_hand_rolled_rolling_mean(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with([{"name": "my_sma", "type": "sma", "window": 5}]), data
    )
    expected = data.frame["Close"].rolling(5).mean()
    pd.testing.assert_series_equal(
        namespace["my_sma"].iloc[-10:],
        expected.iloc[-10:],
        check_names=False,
    )


def test_rolling_max_and_min_are_engine_builtins(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with(
            [
                {"name": "hi", "type": "rolling_max", "window": 10},
                {"name": "lo", "type": "rolling_min", "window": 10},
            ]
        ),
        data,
    )
    close = data.frame["Close"]
    assert namespace["hi"].iloc[-1] == pytest.approx(close.iloc[-10:].max())
    assert namespace["lo"].iloc[-1] == pytest.approx(close.iloc[-10:].min())


def test_source_selects_the_input_series(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with([{"name": "vol_ma", "type": "sma", "source": "volume", "window": 5}]),
        data,
    )
    expected = data.frame["Volume"].rolling(5).mean()
    assert namespace["vol_ma"].iloc[-1] == pytest.approx(expected.iloc[-1])


def test_multi_input_indicators_receive_high_low_close(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with([{"name": "my_atr", "type": "atr", "window": 14}]), data
    )
    assert namespace["my_atr"].iloc[-1] > 0


def test_volume_indicators_receive_volume(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with([{"name": "my_mfi", "type": "mfi", "window": 14}]), data
    )
    assert namespace["my_mfi"].notna().any()


# ------------------------------------------------------------------ multi output


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {"name": "my_macd", "type": "macd", "fast": 12, "slow": 26, "signal": 9},
            ("my_macd_macd", "my_macd_macdh", "my_macd_macds"),
        ),
        (
            {"name": "my_bb", "type": "bbands", "window": 20, "std": 2.0},
            ("my_bb_bbl", "my_bb_bbm", "my_bb_bbu", "my_bb_bbb", "my_bb_bbp"),
        ),
        (
            {"name": "my_stoch", "type": "stoch", "k": 14, "d": 3, "smooth_k": 3},
            ("my_stoch_stochk", "my_stoch_stochd"),
        ),
        (
            {"name": "my_adx", "type": "adx", "window": 14},
            ("my_adx_adx", "my_adx_dmp", "my_adx_dmn"),
        ),
    ],
)
def test_multi_output_fan_out_names(
    data: MarketData, config: dict[str, Any], expected: tuple[str, ...]
) -> None:
    namespace = compute_indicators(strategy_with([config]), data)
    for name in expected:
        assert name in namespace, f"{name} missing from {namespace.indicator_names}"


def test_declared_outputs_match_the_library(data: MarketData) -> None:
    """A pandas_ta upgrade that renames a column must fail here, not inside a user signal."""
    for spec in registry.all_specs():
        if not spec.is_multi_output:
            continue
        namespace = compute_indicators(strategy_with([{"name": "probe", "type": spec.type}]), data)
        for name in spec.output_names("probe"):
            assert name in namespace, f"{spec.type} did not produce {name}"


def test_every_registered_indicator_computes(data: MarketData) -> None:
    for spec in registry.all_specs():
        namespace = compute_indicators(strategy_with([{"name": "probe", "type": spec.type}]), data)
        for name in spec.output_names("probe"):
            assert len(namespace[name]) == len(data)


# ------------------------------------------------------------------ causality


def test_ichimoku_projected_and_lagging_spans_are_not_exposed(data: MarketData) -> None:
    """ISA/ISB are plotted into the future; ICS is the close shifted backwards."""
    namespace = compute_indicators(strategy_with([{"name": "cloud", "type": "ichimoku"}]), data)
    assert "cloud_its" in namespace
    assert "cloud_iks" in namespace
    for forbidden in ("cloud_isa", "cloud_isb", "cloud_ics"):
        assert forbidden not in namespace


def test_warmup_region_is_nan_not_zero(data: MarketData) -> None:
    """Defect D1: zero-filling made ``close >= rolling_max`` true from bar zero."""
    namespace = compute_indicators(
        strategy_with([{"name": "hi", "type": "rolling_max", "window": 50}]), data
    )
    values = namespace["hi"]
    assert values.iloc[:49].isna().all()
    assert not (values.iloc[:49] == 0).any()


def test_namespace_warmup_is_the_longest_indicator(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with(
            [
                {"name": "fast", "type": "sma", "window": 5},
                {"name": "slow", "type": "sma", "window": 200},
            ]
        ),
        data,
    )
    assert namespace.warmup >= 199


def test_warmup_is_never_under_reported(data: MarketData) -> None:
    """The declared warm-up may drift; the measured one is the floor."""
    for spec in registry.all_specs():
        namespace = compute_indicators(strategy_with([{"name": "probe", "type": spec.type}]), data)
        for name in spec.output_names("probe"):
            values = namespace[name]
            defined = values.notna().to_numpy()
            if defined.any():
                first_defined = int(defined.argmax())
                assert namespace.warmup >= first_defined, f"{spec.type}/{name}"


# ------------------------------------------------------------------ namespace shape


def test_raw_price_series_are_available(data: MarketData) -> None:
    namespace = compute_indicators(strategy_with([]), data)
    assert set(namespace.names) == {"open", "high", "low", "close", "volume"}
    assert namespace.warmup == 0


def test_outputs_are_float64_on_the_data_index(data: MarketData) -> None:
    namespace = compute_indicators(
        strategy_with([{"name": "my_sma", "type": "sma", "window": 5}]), data
    )
    values = namespace["my_sma"]
    assert values.dtype == np.float64
    assert values.index.equals(data.index)


def test_colliding_generated_names_are_rejected(data: MarketData) -> None:
    strategy = strategy_with(
        [
            {"name": "x", "type": "macd"},
            {"name": "x_macd", "type": "sma", "window": 5},
        ]
    )
    with pytest.raises(IndicatorError, match="already taken"):
        compute_indicators(strategy, data)


# ------------------------------------------------------------------ validation


def test_unknown_indicator_type_fails_at_load_time() -> None:
    with pytest.raises(StrategyValidationError, match="unknown indicator type"):
        build_strategy(strategy_payload([{"name": "x", "type": "not_an_indicator"}]))


def test_wrong_parameter_name_is_reported_with_the_accepted_set() -> None:
    with pytest.raises(StrategyValidationError, match="Accepted parameters: window"):
        build_strategy(strategy_payload([{"name": "x", "type": "sma", "tenkan": 9}]))


def test_out_of_range_parameter_is_reported() -> None:
    with pytest.raises(StrategyValidationError, match=r"indicators\.x\.window"):
        build_strategy(strategy_payload([{"name": "x", "type": "sma", "window": 0}]))


def test_pointless_source_is_reported() -> None:
    with pytest.raises(StrategyValidationError, match="has no effect"):
        build_strategy(
            strategy_payload([{"name": "x", "type": "atr", "source": "volume", "window": 14}])
        )


def test_a_valid_strategy_passes_semantic_validation() -> None:
    strategy = build_strategy(
        strategy_payload(
            [
                {"name": "sma_long", "type": "sma", "window": 200},
                {"name": "my_bb", "type": "bbands", "window": 20, "std": 2.0},
            ]
        )
    )
    assert len(strategy.indicators) == 2


def strategy_payload(indicators: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "strategy": {"name": "s"},
        "universe": {"ticker": "TEST", "start_date": "2020-01-01", "end_date": "2024-01-01"},
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.0,
            "commission_pct": 0.0,
        },
        "indicators": indicators,
        "entry": {"signal": "close > open"},
        "exit": {"max_holding_days": 5},
    }
