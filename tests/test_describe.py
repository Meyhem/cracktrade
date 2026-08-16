"""Phase 4: describing the catalogue, and warning without refusing.

Both exist because two interfaces need the same answer. The CLI's ``indicators`` command and
the API's metadata endpoint list the same catalogue; the CLI prints a shadowed-stop warning
beside a run and the editor attaches it to a field. Anything either of them had to work out
for itself would be worked out twice, and eventually differently.
"""

from __future__ import annotations

from typing import Any

import pytest

from cracktrade.indicators.catalogue import install
from cracktrade.indicators.describe import describe_catalogue
from cracktrade.strategy import ConfigWarning, build_strategy, strategy_warnings


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


def strategy_dict() -> dict[str, Any]:
    """A minimal valid strategy, as a mapping the API's entry point would receive."""
    return {
        "strategy": {"name": "momentum_v2"},
        "universe": {
            "ticker": "NVDA",
            "start_date": "2018-01-01",
            "end_date": "2025-12-31",
        },
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.1,
            "commission_pct": 0.05,
        },
        "indicators": [{"name": "sma_long", "type": "sma", "window": 200}],
        "entry": {"signal": "close > sma_long"},
        "exit": {"stop_loss_pct": 5.0},
    }


# --------------------------------------------------------------------------- the catalogue


def test_the_catalogue_is_described_in_full() -> None:
    catalogue = describe_catalogue()
    assert len(catalogue) > 50
    assert {item.type for item in catalogue} >= {"sma", "rsi", "atr", "macd"}


def test_a_single_output_indicator_contributes_its_own_name() -> None:
    sma = next(item for item in describe_catalogue() if item.type == "sma")
    assert sma.outputs == ()
    assert sma.namespace_names("sma_long") == ("sma_long",)
    assert [parameter.name for parameter in sma.parameters] == ["window"]
    assert sma.parameters[0].default == 20


def test_a_multi_output_indicator_names_everything_it_contributes() -> None:
    """The editor lists these as available names, so a missing one is an unwritable signal."""
    macd = next(item for item in describe_catalogue() if item.type == "macd")
    assert macd.outputs == ("macd", "macdh", "macds")
    assert macd.namespace_names("my_macd") == ("my_macd_macd", "my_macd_macdh", "my_macd_macds")


def test_multi_input_indicators_report_that_source_does_nothing() -> None:
    """ATR always takes high/low/close, so an editor must not offer a source control for it."""
    atr = next(item for item in describe_catalogue() if item.type == "atr")
    assert atr.uses_source is False
    assert set(atr.inputs) == {"high", "low", "close"}

    sma = next(item for item in describe_catalogue() if item.type == "sma")
    assert sma.uses_source is True


def test_every_indicator_is_renderable() -> None:
    """A blank cell in the catalogue table would be a registry entry nobody can use."""
    for item in describe_catalogue():
        assert item.type
        assert item.description
        assert item.inputs


# --------------------------------------------------------------------------- warnings


def test_a_valid_strategy_with_no_surprises_warns_about_nothing() -> None:
    strategy = build_strategy(strategy_dict())
    assert strategy_warnings(strategy) == ()


def test_a_shadowed_stop_is_warned_about_but_still_loads() -> None:
    """Spec 3.7: the config is valid, and the stop the user wrote will never fire."""
    config = strategy_dict()
    config["exit"] = {
        "atr_stop_multiplier": 2.5,
        "trailing_stop_pct": 8.0,
        "stop_loss_pct": 5.0,
    }
    strategy = build_strategy(config)

    warnings = strategy_warnings(strategy)
    paths = {warning.path for warning in warnings}
    assert paths == {"exit.trailing_stop_pct", "exit.stop_loss_pct"}
    for warning in warnings:
        assert "atr_stop_multiplier" in warning.message


def test_the_winning_stop_is_not_warned_about() -> None:
    config = strategy_dict()
    config["exit"] = {"stop_loss_pct": 5.0}
    assert strategy_warnings(build_strategy(config)) == ()


def test_a_pointless_source_is_warned_about() -> None:
    """Setting source on ATR does nothing, and silently doing nothing is the problem."""
    config = strategy_dict()
    config["indicators"] = [
        {"name": "atr_14", "type": "atr", "window": 14, "source": "close"},
    ]
    config["entry"] = {"signal": "close > atr_14"}
    warnings = strategy_warnings(build_strategy(config))
    assert [warning.path for warning in warnings] == ["indicators.atr_14.source"]


def test_warnings_are_structured_for_placement() -> None:
    """A path plus a message, so the editor can attach it to the field that caused it."""
    config = strategy_dict()
    config["exit"] = {"atr_stop_multiplier": 2.5, "stop_loss_pct": 5.0}
    warning = strategy_warnings(build_strategy(config))[0]
    assert isinstance(warning, ConfigWarning)
    assert warning.path.startswith("exit.")
    assert warning.message.endswith(".")
