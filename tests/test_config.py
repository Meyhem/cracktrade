"""Phase 2: the strategy schema.

Covers the spec's section 3 contract, including the defects it fixes: the dict-shaped
``indicators`` section (D14), silently-ignored keys such as ``entry_price``, and string dates
compared lexicographically.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cracktrade.config import (
    PositionSizingType,
    PriceSeries,
    Strategy,
    dump_strategy,
    parse_strategy,
    read_strategy_file,
)
from cracktrade.errors import ConfigError, StrategyValidationError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def minimal() -> dict[str, Any]:
    """The smallest strategy that validates."""
    return {
        "strategy": {"name": "test_strategy"},
        "universe": {
            "ticker": "MSFT",
            "start_date": "2020-01-01",
            "end_date": "2024-01-01",
        },
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.1,
            "commission_pct": 0.05,
        },
        "indicators": [{"name": "sma_long", "type": "sma", "window": 200}],
        "entry": {"signal": "close > sma_long"},
        "exit": {"max_holding_days": 20},
    }


def issues_from(data: dict[str, Any]) -> list[str]:
    """Validate and return the issue list, failing if the strategy was accepted."""
    with pytest.raises(StrategyValidationError) as caught:
        parse_strategy(data)
    return caught.value.issues


# ------------------------------------------------------------------ happy path


@pytest.mark.parametrize("name", ["momentum_breakout.yaml", "rsi_pullback.yaml"])
def test_shipped_examples_validate(name: str) -> None:
    strategy = read_strategy_file(EXAMPLES / name)
    assert strategy.universe.ticker
    assert strategy.entry.signal
    assert strategy.exit.max_holding_days or strategy.exit.signal


def test_parses_into_typed_values() -> None:
    strategy = parse_strategy(minimal())
    assert isinstance(strategy.universe.start_date, date)
    assert strategy.universe.start_date == date(2020, 1, 1)
    assert strategy.indicators[0].source is PriceSeries.CLOSE
    assert strategy.indicators[0].params == {"window": 200}


def test_percentages_convert_to_fractions() -> None:
    execution = parse_strategy(minimal()).execution
    assert execution.commission_fraction == pytest.approx(0.0005)
    assert execution.slippage_fraction == pytest.approx(0.001)


def test_strategy_is_immutable() -> None:
    strategy = parse_strategy(minimal())
    with pytest.raises(ValidationError, match=r"frozen|immutable"):
        strategy.universe.ticker = "AAPL"


def test_end_date_defaults_to_today() -> None:
    data = minimal()
    del data["universe"]["end_date"]
    assert parse_strategy(data).universe.end_date >= date(2025, 1, 1)


# ------------------------------------------------------------------ single ticker


def test_ticker_list_is_rejected() -> None:
    data = minimal()
    data["universe"]["ticker"] = ["MSFT", "AAPL"]
    assert any("ticker" in issue for issue in issues_from(data))


def test_legacy_tickers_key_is_rejected_with_a_suggestion() -> None:
    data = minimal()
    del data["universe"]["ticker"]
    data["universe"]["tickers"] = ["MSFT"]
    joined = " ".join(issues_from(data))
    assert "tickers" in joined
    assert "did you mean 'ticker'" in joined


# ------------------------------------------------------------------ dates


def test_unparseable_date_is_rejected() -> None:
    data = minimal()
    data["universe"]["start_date"] = "01/01/2020"
    assert any("YYYY-MM-DD" in issue for issue in issues_from(data))


def test_start_must_precede_end() -> None:
    data = minimal()
    data["universe"]["start_date"] = "2024-01-01"
    data["universe"]["end_date"] = "2020-01-01"
    assert any("must be before" in issue for issue in issues_from(data))


def test_native_yaml_dates_are_accepted() -> None:
    data = minimal()
    data["universe"]["start_date"] = date(2020, 1, 1)
    assert parse_strategy(data).universe.start_date == date(2020, 1, 1)


# ------------------------------------------------------------------ closed schema


def test_silently_ignored_legacy_keys_are_now_errors() -> None:
    """``entry_price``/``exit_price`` never existed on the model and were swallowed."""
    data = minimal()
    data["execution"]["entry_price"] = "next_open"
    data["execution"]["exit_price"] = "next_open"
    joined = " ".join(issues_from(data))
    assert "entry_price" in joined
    assert "exit_price" in joined


def test_unknown_top_level_section_is_rejected() -> None:
    data = minimal()
    data["watchdog"] = {"notify": ["someone"]}
    assert any("watchdog" in issue for issue in issues_from(data))


def test_strict_types_reject_stringly_typed_numbers() -> None:
    data = minimal()
    data["execution"]["initial_capital"] = "10000.0"
    assert issues_from(data)


def test_integer_capital_is_accepted() -> None:
    data = minimal()
    data["execution"]["initial_capital"] = 10000
    assert parse_strategy(data).execution.initial_capital == pytest.approx(10000.0)


def test_the_old_variant_schema_is_rejected_and_names_what_replaced_it() -> None:
    """Every strategy file written before variants were removed uses the old keys.

    The rejection has to be legible on its own, because the user's first encounter with this
    change is a file that used to work. ``extra="forbid"`` names the unknown key and lists the
    accepted ones, which is where ``entry`` and ``exit`` appear.
    """
    data = minimal()
    del data["entry"]
    del data["exit"]
    data["entry_variants"] = [{"name": "e", "signal": "close > sma_long"}]
    data["exit_variants"] = [{"name": "x", "max_holding_days": 20}]

    issues = issues_from(data)

    assert any("entry_variants" in issue and "unknown field" in issue for issue in issues)
    assert any("exit_variants" in issue and "unknown field" in issue for issue in issues)
    assert any(issue.startswith("entry:") and "required" in issue for issue in issues)
    assert any(issue.startswith("exit:") and "required" in issue for issue in issues)


# ------------------------------------------------------------------ defect D14: list vs dict


def test_mapping_shaped_indicators_are_rejected() -> None:
    data = minimal()
    data["indicators"] = {"some_name": {"type": "sma", "window": 200}}
    assert any("must be a list" in issue for issue in issues_from(data))


# ------------------------------------------------------------------ indicators


def test_indicator_name_may_not_shadow_a_price_series() -> None:
    data = minimal()
    data["indicators"] = [{"name": "close", "type": "sma", "window": 20}]
    assert any("shadow" in issue for issue in issues_from(data))


def test_indicator_name_must_be_usable_in_an_expression() -> None:
    data = minimal()
    data["indicators"] = [{"name": "my sma!", "type": "sma", "window": 20}]
    assert any("identifier" in issue for issue in issues_from(data))


def test_indicator_type_is_normalised_to_lowercase() -> None:
    data = minimal()
    data["indicators"] = [{"name": "sma_long", "type": "SMA", "window": 200}]
    assert parse_strategy(data).indicators[0].type == "sma"


def test_arbitrary_indicator_parameters_are_collected_not_rejected() -> None:
    """The registry, not the schema, decides which parameters an indicator accepts."""
    data = minimal()
    data["indicators"] = [
        {"name": "my_macd", "type": "macd", "fast": 12, "slow": 26, "signal": 9},
    ]
    assert parse_strategy(data).indicators[0].params == {"fast": 12, "slow": 26, "signal": 9}


def test_duplicate_indicator_names_are_rejected() -> None:
    data = minimal()
    data["indicators"] = [
        {"name": "sma_long", "type": "sma", "window": 200},
        {"name": "sma_long", "type": "sma", "window": 50},
    ]
    assert any("duplicate" in issue for issue in issues_from(data))


# ------------------------------------------------------------------ the exit rule


def test_exit_rule_must_offer_a_way_out() -> None:
    data = minimal()
    data["exit"] = {}
    assert any("no way to exit" in issue for issue in issues_from(data))


def test_holding_bounds_must_be_ordered() -> None:
    data = minimal()
    data["exit"] = {"min_holding_days": 10, "max_holding_days": 5}
    assert any("min_holding_days" in issue for issue in issues_from(data))


@pytest.mark.parametrize(
    ("fields", "active", "shadowed"),
    [
        ({"atr_stop_multiplier": 2.0, "trailing_stop_pct": 5.0}, "atr", ("trailing_stop_pct",)),
        ({"trailing_stop_pct": 5.0, "stop_loss_pct": 3.0}, "trailing", ("stop_loss_pct",)),
        ({"stop_loss_pct": 3.0}, "fixed", ()),
        ({"max_holding_days": 5}, None, ()),
    ],
)
def test_stop_priority_chain(
    fields: dict[str, Any], active: str | None, shadowed: tuple[str, ...]
) -> None:
    data = minimal()
    data["exit"] = fields
    rule = parse_strategy(data).exit
    assert rule.active_stop == active
    assert rule.shadowed_stops == shadowed


def test_negative_stop_is_rejected() -> None:
    data = minimal()
    data["exit"] = {"stop_loss_pct": -5.0}
    assert issues_from(data)


# ------------------------------------------------------------------ position sizing


def test_position_sizing_type_is_constrained() -> None:
    data = minimal()
    data["position_sizing"] = {"type": "martingale", "value": 2.0}
    assert issues_from(data)


def test_fixed_pct_cannot_exceed_one_hundred() -> None:
    data = minimal()
    data["position_sizing"] = {"type": "fixed_pct", "value": 150.0}
    assert any("cannot exceed 100" in issue for issue in issues_from(data))


def test_position_sizing_is_optional() -> None:
    assert parse_strategy(minimal()).position_sizing is None


def test_valid_position_sizing() -> None:
    data = minimal()
    data["position_sizing"] = {"type": "fixed_cash", "value": 2000.0}
    sizing = parse_strategy(data).position_sizing
    assert sizing is not None
    assert sizing.type is PositionSizingType.FIXED_CASH


# ------------------------------------------------------------------ required sections


@pytest.mark.parametrize("section", ["strategy", "universe", "execution", "entry", "exit"])
def test_required_sections(section: str) -> None:
    data = minimal()
    del data[section]
    assert any("required" in issue for issue in issues_from(data))


def test_indicators_may_be_omitted() -> None:
    data = minimal()
    del data["indicators"]
    data["entry"] = {"signal": "close > open"}
    assert parse_strategy(data).indicators == ()


# ------------------------------------------------------------------ error reporting


def test_all_problems_are_reported_at_once() -> None:
    data = minimal()
    del data["strategy"]
    data["execution"]["initial_capital"] = -1.0
    data["exit"] = {}
    assert len(issues_from(data)) >= 3


def test_errors_carry_yaml_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(
        "\n".join(
            [
                "strategy:",
                "  name: broken",
                "universe:",
                "  ticker: MSFT",
                "  start_date: 'not-a-date'",
                "  end_date: '2024-01-01'",
                "execution:",
                "  initial_capital: 10000.0",
                "  slippage_pct: 0.1",
                "  commission_pct: 0.05",
                "entry:",
                "  signal: 'close > open'",
                "exit:",
                "  max_holding_days: 20",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(StrategyValidationError) as caught:
        read_strategy_file(path)
    assert any("line 5" in issue for issue in caught.value.issues)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        read_strategy_file(tmp_path / "absent.yaml")


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("strategy: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        read_strategy_file(path)


def test_empty_file_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        read_strategy_file(path)


# ------------------------------------------------------------------ serialisation


@pytest.mark.parametrize("name", ["momentum_breakout.yaml", "rsi_pullback.yaml"])
def test_dump_round_trips(name: str) -> None:
    original = read_strategy_file(EXAMPLES / name)
    reparsed = parse_strategy_yaml(dump_strategy(original))
    assert reparsed == original


def parse_strategy_yaml(text: str) -> Strategy:
    import yaml

    return parse_strategy(yaml.safe_load(text))


def test_dump_flattens_indicator_parameters() -> None:
    text = dump_strategy(parse_strategy(minimal()))
    assert "window: 200" in text
    assert "params" not in text


def test_parsing_does_not_mutate_the_input() -> None:
    data = minimal()
    snapshot = copy.deepcopy(data)
    parse_strategy(data)
    assert data == snapshot
