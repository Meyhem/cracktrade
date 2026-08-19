"""Phase 1: the ``universe.interval`` field and bar-denominated holding periods.

The theme running through these tests is backwards compatibility. Every strategy written before
intraday support existed is a daily strategy that omits ``interval``, and must keep parsing --
and producing -- exactly what it did. The intraday rules are additive.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from cracktrade.backtest.holding import holding_bounds
from cracktrade.config import Interval, dump_strategy, parse_strategy
from cracktrade.errors import StrategyValidationError
from cracktrade.strategy import build_strategy, strategy_warnings


def daily() -> dict[str, Any]:
    """A minimal daily strategy, in the shape files written before this feature use."""
    return {
        "strategy": {"name": "daily_strategy"},
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


def intraday(interval: str = "30m", **universe: Any) -> dict[str, Any]:
    """The same strategy declared on intraday bars, with a reachable range."""
    data = daily()
    data["strategy"]["name"] = "intraday_strategy"
    data["universe"] = {
        "ticker": "SAP.DE",
        "start_date": "2026-07-01",
        "end_date": "2026-08-18",
        "interval": interval,
        **universe,
    }
    data["indicators"] = [{"name": "sma_long", "type": "sma", "window": 20}]
    data["exit"] = {"max_holding_bars": 20}
    return data


def issues_from(data: dict[str, Any]) -> list[str]:
    with pytest.raises(StrategyValidationError) as caught:
        parse_strategy(data)
    return caught.value.issues


# ----------------------------------------------------------------- the enum


def test_pandas_aliases_are_the_ones_pandas_actually_accepts() -> None:
    """``30m`` is not a pandas offset alias, and ``T``/``H`` are deprecated in pandas 2.x.

    The mapping lives on the enum and nowhere else. A wrong alias here does not raise -- it
    silently changes what every time-based metric is annualised against.
    """
    import pandas as pd

    for interval in Interval:
        assert pd.Timedelta(interval.pandas_freq) == pd.Timedelta(interval.bar_timedelta)


def test_only_daily_is_not_intraday() -> None:
    assert not Interval.D1.is_intraday
    assert all(interval.is_intraday for interval in Interval if interval is not Interval.D1)


def test_thirty_minutes_inherits_the_fifteen_minute_limit() -> None:
    """The provider resamples 30m from 15m, so it gets the shorter window, not its own."""
    hourly, half_hourly = Interval.H1.max_lookback, Interval.M30.max_lookback
    assert half_hourly is not None
    assert hourly is not None
    assert half_hourly == Interval.M15.max_lookback
    assert hourly > half_hourly
    assert Interval.D1.max_lookback is None


# ------------------------------------------------------- backwards compatibility


def test_omitting_interval_means_daily() -> None:
    assert parse_strategy(daily()).universe.interval is Interval.D1


def test_daily_strategies_round_trip_unchanged() -> None:
    """A file that never mentioned an interval re-parses to the same strategy after a dump.

    ``dump_strategy`` writes the normalised full form, defaults included, so the dump gains an
    explicit ``interval: 1d`` exactly as it already carries ``risk_free_rate`` and an
    indicator's ``source``. What matters is that the round trip is a fixed point.
    """
    original = daily()
    assert "interval: 1d" in dump_strategy(parse_strategy(original))
    assert parse_strategy(original) == parse_strategy(dump_and_reparse(original))


def dump_and_reparse(data: dict[str, Any]) -> dict[str, Any]:
    import yaml

    parsed = yaml.safe_load(dump_strategy(parse_strategy(data)))
    assert isinstance(parsed, dict)
    return parsed


def test_holding_days_still_reach_the_engine_as_bars() -> None:
    """On daily bars the two words mean the same thing, and the engine always counted bars."""
    strategy = parse_strategy(daily())
    assert holding_bounds(strategy.exit) == (0, 20)


# ------------------------------------------------------------- interval parsing


@pytest.mark.parametrize("token", ["15m", "30m", "1h", "1d"])
def test_every_interval_token_parses(token: str) -> None:
    strategy = parse_strategy(intraday(token))
    assert strategy.universe.interval.value == token


def test_an_unknown_interval_lists_the_accepted_ones() -> None:
    issues = issues_from(intraday("5m"))
    assert any("15m" in issue and "1d" in issue for issue in issues)


# --------------------------------------------------------------- range validation


def test_a_multi_year_range_is_refused_at_thirty_minutes() -> None:
    """The mistake that actually happens: switching an existing daily strategy to intraday."""
    data = intraday("30m", start_date="2020-01-01", end_date="2024-01-01")
    issues = issues_from(data)
    assert any("55 days" in issue and "1h" in issue for issue in issues)


def test_the_span_check_is_on_width_not_on_age() -> None:
    """A narrow range far in the past still parses.

    Deliberate: the provider's window slides forward daily, so validating against "now" would
    make a stored strategy stop parsing on a timer -- taking the detail view of every run it
    ever produced with it. Width is time-independent; reachability is a warning plus a loud
    refusal at fetch time.
    """
    data = intraday("30m", start_date="2021-01-01", end_date="2021-02-01")
    strategy = parse_strategy(data)
    assert strategy.universe.interval is Interval.M30


def test_an_hourly_strategy_may_span_two_years() -> None:
    data = intraday("1h", start_date="2025-01-01", end_date="2026-08-18")
    assert parse_strategy(data).universe.interval is Interval.H1


def test_a_daily_strategy_has_no_span_limit() -> None:
    data = daily()
    data["universe"]["start_date"] = "1990-01-01"
    assert parse_strategy(data).universe.start_date == date(1990, 1, 1)


def test_an_aged_range_warns_rather_than_failing() -> None:
    """The warning is what the editor shows before the user waits for a failed download."""
    data = intraday("30m", start_date="2021-01-01", end_date="2021-02-01")
    warnings = strategy_warnings(build_strategy(data))
    aged = [warning for warning in warnings if warning.path == "universe.start_date"]
    assert len(aged) == 1
    assert "2021-01-01" in aged[0].message


def test_a_current_intraday_range_does_not_warn() -> None:
    today = datetime.now(UTC).date()
    data = intraday(
        "30m",
        start_date=(today - timedelta(days=20)).isoformat(),
        end_date=(today - timedelta(days=1)).isoformat(),
    )
    warnings = strategy_warnings(build_strategy(data))
    assert not [warning for warning in warnings if warning.path == "universe.start_date"]


def test_a_daily_range_never_warns_however_old() -> None:
    data = daily()
    data["universe"]["start_date"] = "1990-01-01"
    warnings = strategy_warnings(build_strategy(data))
    assert not [warning for warning in warnings if warning.path == "universe.start_date"]


# ------------------------------------------------------------- holding periods


def test_holding_days_are_refused_on_intraday_bars() -> None:
    """Reinterpreting "5 days" as five 30-minute bars would turn a week into two hours."""
    data = intraday("30m")
    data["exit"] = {"max_holding_days": 5}
    issues = issues_from(data)
    assert any("max_holding_days" in issue and "max_holding_bars" in issue for issue in issues)


def test_holding_bars_are_accepted_on_daily_bars_too() -> None:
    """The bar-denominated spelling is the general one; it is not intraday-only."""
    data = daily()
    data["exit"] = {"max_holding_bars": 20}
    strategy = parse_strategy(data)
    assert holding_bounds(strategy.exit) == (0, 20)


def test_declaring_a_bound_both_ways_is_refused() -> None:
    data = daily()
    data["exit"] = {"max_holding_days": 20, "max_holding_bars": 20}
    issues = issues_from(data)
    assert any("max_holding_days" in issue and "max_holding_bars" in issue for issue in issues)


def test_mixed_spellings_across_different_bounds_are_allowed() -> None:
    """Only the *same* bound declared twice is ambiguous."""
    data = daily()
    data["exit"] = {"min_holding_days": 2, "max_holding_bars": 20}
    assert holding_bounds(parse_strategy(data).exit) == (2, 20)


def test_bar_bounds_must_be_ordered() -> None:
    data = intraday("30m")
    data["exit"] = {"min_holding_bars": 10, "max_holding_bars": 5}
    issues = issues_from(data)
    assert any("min_holding_bars" in issue for issue in issues)


def test_max_holding_bars_counts_as_an_exit_mechanism() -> None:
    """Otherwise an intraday strategy with only a bar limit would be told it cannot exit."""
    data = intraday("30m")
    data["exit"] = {"max_holding_bars": 6}
    assert parse_strategy(data).exit.max_holding == 6


def test_an_exit_with_only_holding_days_on_intraday_is_still_refused() -> None:
    """Two rules could fire here; the strategy must be rejected either way."""
    data = intraday("30m")
    data["exit"] = {"max_holding_days": 6}
    assert issues_from(data)


def test_holding_bounds_are_rejected_below_one() -> None:
    data = intraday("30m")
    data["exit"] = {"max_holding_bars": 0}
    with pytest.raises((ValidationError, StrategyValidationError)):
        parse_strategy(data)


# --------------------------------------------------------------- optimizer reach


def test_holding_bars_are_discoverable_by_the_optimizer() -> None:
    """The ``_bars`` fields are ordinary numeric leaves, like the ``_days`` ones they replace."""
    from cracktrade.optimize.discovery import discover_parameters

    data = intraday("30m")
    data["exit"] = {"min_holding_bars": 4, "max_holding_bars": 20}
    paths = {parameter.path for parameter in discover_parameters(parse_strategy(data))}
    assert "exit.min_holding_bars" in paths
    assert "exit.max_holding_bars" in paths


def test_the_optimizer_cannot_move_the_interval() -> None:
    """Optimizing the bar width would be fitting the question, not the answer."""
    from cracktrade.optimize.discovery import discover_parameters

    parameters = discover_parameters(parse_strategy(intraday("30m")))
    assert not any(parameter.key == "interval" for parameter in parameters)


def test_injecting_parameters_preserves_the_interval() -> None:
    from cracktrade.optimize.discovery import discover_parameters, inject

    strategy = parse_strategy(intraday("30m"))
    parameters = discover_parameters(strategy)
    config = inject(strategy, parameters, [parameter.value for parameter in parameters])
    assert parse_strategy(copy.deepcopy(config)).universe.interval is Interval.M30
