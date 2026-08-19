"""Phase 9: serialization and the command-line surface.

The property worth defending here is that **stdout carries the requested result and nothing
else**. Diagnostics, progress and verdicts go to stderr. Without that,
``cracktrade backtest s.yaml --format json | jq`` does not work, and a log line landing in the
middle of a JSON document is exactly the kind of failure nobody notices until it is in a
pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from cracktrade.cli.app import _exit_code_for, main
from cracktrade.cli.exit_codes import ExitCode
from cracktrade.cli.output import OutputFormat
from cracktrade.config import Interval
from cracktrade.domain import Metrics
from cracktrade.errors import (
    BacktestError,
    CausalityViolationError,
    ConfigError,
    CracktradeError,
    DataUnavailableError,
    OptimizationError,
)
from cracktrade.serialize import to_dict, to_json, to_yaml
from tests.test_metrics import strategy_with, trending_market


def a_metrics(**overrides: Any) -> Metrics:
    base: dict[str, Any] = {
        "total_trades": 30,
        "win_rate_pct": 50.0,
        "profit_factor": 1.5,
        "total_pnl": 100.0,
        "final_equity": 10_100.0,
        "total_return_pct": 1.0,
        "cagr_pct": 1.0,
        "max_drawdown_pct": -5.0,
        "sharpe_ratio": 0.5,
        "sortino_ratio": 0.7,
        "calmar_ratio": 0.2,
        "exposure_pct": 30.0,
        "avg_holding_bars": 5.0,
        "best_trade_pnl": 50.0,
        "worst_trade_pnl": -20.0,
        "bars": 500,
    }
    base.update(overrides)
    return Metrics(**base)


# --------------------------------------------------------------------- serialization


def test_a_result_serialises_to_parseable_json() -> None:
    from cracktrade.backtest import run_backtest

    result = run_backtest(strategy_with(), trending_market(760))

    parsed = json.loads(to_json(result))

    assert parsed["ticker"] == "TEST"
    assert "metrics" in parsed
    assert "benchmark" in parsed


def test_json_and_yaml_describe_the_same_structure() -> None:
    from cracktrade.backtest import run_backtest

    result = run_backtest(strategy_with(), trending_market(760))

    assert yaml.safe_load(to_yaml(result)) == json.loads(to_json(result))


def test_computed_verdicts_survive_serialization() -> None:
    """A naive field walk would drop exactly the parts a consumer most needs."""
    from cracktrade.backtest import run_backtest

    parsed = to_dict(run_backtest(strategy_with(), trending_market(760)))

    assert "beats_buy_and_hold" in parsed["benchmark"]
    assert "has_enough_trades_to_judge" in parsed["metrics"]
    assert "is_winner" in parsed["trades"][0]


def test_an_infinite_profit_factor_becomes_null_rather_than_invalid_json() -> None:
    """``inf`` is a real result -- no losing trades -- and is not valid JSON."""
    serialised = to_dict(a_metrics(profit_factor=float("inf")))

    assert serialised["profit_factor"] is None
    assert json.loads(json.dumps(serialised))["profit_factor"] is None


def test_a_nan_metric_becomes_null() -> None:
    assert to_dict(a_metrics(sharpe_ratio=float("nan")))["sharpe_ratio"] is None


def test_dates_become_iso_strings() -> None:
    from cracktrade.backtest import run_backtest

    parsed = to_dict(run_backtest(strategy_with(), trending_market(760)))

    assert parsed["vintage"]["first_bar"].count("-") == 2


def test_tuples_become_lists_so_json_and_yaml_agree() -> None:
    from cracktrade.backtest import run_backtest

    parsed = to_dict(run_backtest(strategy_with(), trending_market(760)))

    assert isinstance(parsed["trades"], list)
    assert isinstance(parsed["metrics"]["yearly_returns"], list)


# ------------------------------------------------------------------------ log routing


def test_library_logs_go_to_stderr_not_stdout() -> None:
    """Otherwise a log line lands inside a JSON document and nothing notices until it breaks.

    Asserted on the handler rather than by capturing output, because the handler's console is
    the thing that has to be right; capturing would pass just as well with a lucky test harness.
    """
    from rich.logging import RichHandler

    from cracktrade.log import configure

    configure()
    handlers = logging.getLogger("cracktrade").handlers

    assert handlers
    for handler in handlers:
        assert isinstance(handler, RichHandler)
        assert handler.console.stderr, "diagnostics must not go to stdout"


def test_configure_replaces_handlers_rather_than_stacking_them() -> None:
    from cracktrade.log import configure

    configure()
    configure()

    assert len(logging.getLogger("cracktrade").handlers) == 1


# -------------------------------------------------------------------------- exit codes


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CausalityViolationError("x"), ExitCode.CAUSALITY),
        (ConfigError("x"), ExitCode.CONFIG),
        (DataUnavailableError("x"), ExitCode.DATA),
        (BacktestError("x"), ExitCode.ENGINE),
        (OptimizationError("x"), ExitCode.ENGINE),
        (CracktradeError("x"), ExitCode.INTERNAL),
    ],
)
def test_each_error_class_maps_to_a_distinct_exit_code(
    error: CracktradeError, expected: ExitCode
) -> None:
    """Distinct codes make the CLI scriptable without parsing stderr."""
    assert _exit_code_for(error) == expected


def test_the_exit_codes_are_unique() -> None:
    values = [member.value for member in ExitCode]

    assert len(values) == len(set(values))


def test_a_not_credible_result_has_its_own_exit_code() -> None:
    """It is not an error -- the run succeeded -- so it must not collide with one."""
    assert ExitCode.NOT_CREDIBLE not in {
        ExitCode.OK,
        ExitCode.INTERNAL,
        ExitCode.CONFIG,
        ExitCode.DATA,
        ExitCode.ENGINE,
        ExitCode.CAUSALITY,
    }


# ---------------------------------------------------------------------- the CLI surface


def test_every_command_is_registered() -> None:
    assert main(["--help"]) == 0


@pytest.mark.parametrize("command", ["backtest", "optimize", "walkforward", "evolve"])
def test_result_commands_offer_every_output_format(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([command, "--help"]) == 0

    printed = capsys.readouterr().out
    assert "--format" in printed
    assert "--output" in printed


def test_evolve_offers_the_bar_interval(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["evolve", "--help"]) == 0
    assert "--interval" in capsys.readouterr().out


def test_the_evolve_start_default_is_reachable_at_every_interval() -> None:
    """A fixed twelve-year default would refuse every intraday run that did not pass --start.

    Not a cosmetic default: the parse-time width check (spec 3.3.1) rejects a range wider than
    the interval's provider reach, so the default has to be inside it or the command is unusable
    without an option the help text does not say is mandatory.
    """
    from cracktrade.cli.commands.evolve import DEFAULT_YEARS, _default_start

    today = date(2026, 8, 19)
    assert _default_start(Interval.D1, today) == today - timedelta(days=365 * DEFAULT_YEARS)

    for interval in (Interval.M15, Interval.M30, Interval.H1):
        reach = interval.max_lookback
        assert reach is not None
        assert today - _default_start(interval, today) < reach


def test_validate_accepts_a_good_strategy(tmp_path: Path) -> None:
    strategy_file = tmp_path / "s.yaml"
    strategy_file.write_text(_example_yaml(), encoding="utf-8")

    assert main(["validate", str(strategy_file)]) == ExitCode.OK


def test_validate_rejects_a_broken_strategy(tmp_path: Path) -> None:
    strategy_file = tmp_path / "s.yaml"
    strategy_file.write_text("strategy:\n  name: broken\n", encoding="utf-8")

    assert main(["validate", str(strategy_file)]) == ExitCode.CONFIG


def test_a_missing_strategy_file_is_reported_not_crashed() -> None:
    assert main(["validate", "does-not-exist.yaml"]) != ExitCode.OK


def test_the_version_flag_works(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "cracktrade" in capsys.readouterr().out


def test_every_command_appears_in_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--help"])

    printed = capsys.readouterr().out
    for command in ("validate", "backtest", "optimize", "walkforward", "evolve", "indicators"):
        assert command in printed


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_only_the_table_format_is_for_humans(fmt: OutputFormat) -> None:
    assert fmt.is_machine_readable == (fmt is not OutputFormat.TABLE)


def _example_yaml() -> str:
    return """
strategy:
  name: cli_probe
universe:
  ticker: TEST
  start_date: '2020-01-01'
  end_date: '2024-01-01'
execution:
  initial_capital: 10000.0
  commission_pct: 0.1
  slippage_pct: 0.05
indicators:
  - name: sma_fast
    type: sma
    window: 20
entry:
  signal: close > sma_fast
exit:
  signal: close < sma_fast
"""
