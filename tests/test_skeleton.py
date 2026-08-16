"""Phase 1 gate: the package imports, the CLI runs, and settings load."""

from __future__ import annotations

import subprocess
import sys

from cracktrade import __version__
from cracktrade.cli.app import _exit_code_for
from cracktrade.cli.exit_codes import ExitCode
from cracktrade.errors import (
    CausalityViolationError,
    CracktradeError,
    DataUnavailableError,
    OptimizationError,
    StrategyValidationError,
)
from cracktrade.settings import TRADING_DAYS_PER_YEAR, load_settings


def test_version_is_exposed() -> None:
    assert __version__


def test_cli_reports_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cracktrade.cli.app", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == ExitCode.OK
    assert __version__ in result.stdout


def test_settings_defaults() -> None:
    settings = load_settings()
    assert settings.seed == 0
    assert 0.5 < settings.train_fraction < 1.0
    assert settings.cache_enabled is False, "this project stores nothing unless asked"
    assert TRADING_DAYS_PER_YEAR == 252


def test_every_engine_error_derives_from_the_base() -> None:
    for error in (
        StrategyValidationError(["nope"]),
        DataUnavailableError("nope"),
        CausalityViolationError("nope"),
        OptimizationError("nope"),
    ):
        assert isinstance(error, CracktradeError)


def test_exit_codes_are_distinct_per_failure_kind() -> None:
    assert _exit_code_for(StrategyValidationError(["x"])) is ExitCode.CONFIG
    assert _exit_code_for(DataUnavailableError("x")) is ExitCode.DATA
    assert _exit_code_for(CausalityViolationError("x")) is ExitCode.CAUSALITY
    assert _exit_code_for(OptimizationError("x")) is ExitCode.ENGINE


def test_validation_error_keeps_individual_issues() -> None:
    error = StrategyValidationError(["universe.ticker: required", "execution.fees: negative"])
    assert len(error.issues) == 2
    assert "universe.ticker" in str(error)
