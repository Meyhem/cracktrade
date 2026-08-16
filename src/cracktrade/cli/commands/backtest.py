"""``cracktrade backtest`` -- run a strategy over real history and report it."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.backtest import run_backtest
from cracktrade.cli.output import OutputFormat, emit
from cracktrade.cli.render import render_result
from cracktrade.data import FrameCache, YFinanceProvider, load_history
from cracktrade.settings import load_settings
from cracktrade.strategy import load_strategy, required_warmup

console = Console()


def backtest(
    strategy_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to the strategy YAML file.",
        ),
    ],
    fmt: Annotated[
        OutputFormat,
        typer.Option("--format", help="How to present the result."),
    ] = OutputFormat.TABLE,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write machine-readable output here."),
    ] = None,
    cache: Annotated[
        bool,
        typer.Option("--cache/--no-cache", help="Cache downloaded price history on disk."),
    ] = False,
) -> None:
    """Back-test every entry/exit variant of a strategy and print the result.

    The report leads with the comparison against buying and holding the same ticker, because
    that is the number the decision actually turns on.
    """
    settings = load_settings()
    strategy = load_strategy(strategy_file)

    data = load_history(
        strategy,
        YFinanceProvider(),
        cache=FrameCache(settings.cache_dir) if cache else None,
        min_bars=required_warmup(strategy) + settings.min_bars_beyond_warmup,
        max_filled_fraction=settings.max_filled_fraction,
    )

    result = run_backtest(strategy, data, seed=settings.seed)
    emit(result, fmt, console=console, render=render_result, output=output)
