"""``cracktrade`` command-line entry point.

Subcommands are registered by their own modules as phases land; this module owns only the
top-level application, global options, and the error boundary that turns library exceptions
into exit codes.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console

from cracktrade import __version__
from cracktrade.cli.commands.backtest import backtest as backtest_command
from cracktrade.cli.commands.indicators import indicators as indicators_command
from cracktrade.cli.commands.optimize import optimize as optimize_command
from cracktrade.cli.commands.validate import validate as validate_command
from cracktrade.cli.commands.validate_strategy import walkforward as walkforward_command
from cracktrade.cli.exit_codes import ExitCode
from cracktrade.errors import (
    BacktestError,
    CausalityViolationError,
    ConfigError,
    CracktradeError,
    DataError,
    OptimizationError,
)
from cracktrade.log import configure

app = typer.Typer(
    name="cracktrade",
    help="Deterministic, look-ahead-free swing-trading backtester and optimizer.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

#: Diagnostics go to stderr so stdout carries only the requested output and stays pipeable.
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cracktrade {__version__}")
        raise typer.Exit(ExitCode.OK)


@app.callback()
def main_callback(
    version: Annotated[  # noqa: ARG001 - consumed by the eager callback
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Debug logging.")] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Warnings and errors only.")] = False,
) -> None:
    """Global options."""
    configure(verbose=verbose, quiet=quiet)


app.command("validate")(validate_command)
app.command("backtest")(backtest_command)
app.command("optimize")(optimize_command)
app.command("walkforward")(walkforward_command)
app.command("indicators")(indicators_command)


def _exit_code_for(error: CracktradeError) -> ExitCode:
    """Map an engine exception onto a process exit status."""
    if isinstance(error, CausalityViolationError):
        return ExitCode.CAUSALITY
    if isinstance(error, ConfigError):
        return ExitCode.CONFIG
    if isinstance(error, DataError):
        return ExitCode.DATA
    if isinstance(error, BacktestError | OptimizationError):
        return ExitCode.ENGINE
    return ExitCode.INTERNAL


def main() -> int:
    """Run the CLI, translating engine errors into exit codes.

    Engine errors are user-facing and are printed without a traceback. Anything else is a bug
    and keeps its traceback, because hiding it would only make the bug harder to report.
    """
    try:
        app()
    except CracktradeError as error:
        code = _exit_code_for(error)
        err_console.print(f"[bold red]error:[/bold red] {error}")
        return int(code)
    except typer.Exit as exit_signal:
        return int(exit_signal.exit_code)
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(main())
