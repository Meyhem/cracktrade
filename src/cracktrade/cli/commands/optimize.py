"""``cracktrade optimize`` -- search a strategy's parameters and report on unseen data."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.cli.output import OutputFormat, emit
from cracktrade.cli.progress import search_progress
from cracktrade.cli.render import render_optimization
from cracktrade.data import FrameCache, YFinanceProvider, load_history
from cracktrade.optimize import DEFAULT_OBJECTIVE, OBJECTIVES
from cracktrade.optimize import optimize as run_optimize
from cracktrade.settings import load_settings
from cracktrade.strategy import load_strategy, required_warmup

console = Console()
err_console = Console(stderr=True)


def optimize(
    strategy_file: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="Path to the strategy YAML file."
        ),
    ],
    epochs: Annotated[
        int, typer.Option("--epochs", min=1, help="Differential-evolution generations.")
    ] = 10,
    objective: Annotated[
        str,
        typer.Option("--objective", help=f"One of: {', '.join(sorted(OBJECTIVES))}."),
    ] = DEFAULT_OBJECTIVE,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", help="How to present the result.")
    ] = OutputFormat.TABLE,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the result here instead of stdout."),
    ] = None,
    strategy_out: Annotated[
        Path | None,
        typer.Option("--strategy-out", help="Write the optimized strategy YAML here."),
    ] = None,
    cache: Annotated[
        bool, typer.Option("--cache/--no-cache", help="Cache downloaded price history.")
    ] = False,
) -> None:
    """Optimize a strategy's parameters, then report on data the search never saw.

    Parameters are fitted on the first 80% of history and the entry/exit variant is chosen there
    too. The remaining 20% is evaluated exactly once, after both choices are final, and is the
    only part reported as out-of-sample.

    A single split is one draw. For a result worth acting on, use `cracktrade walkforward`.
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

    with search_progress(
        total=epochs, label="searching", enabled=fmt is OutputFormat.TABLE
    ) as on_generation:
        result = run_optimize(
            strategy,
            data,
            epochs=epochs,
            seed=settings.seed,
            workers=settings.workers,
            train_fraction=settings.train_fraction,
            objective_name=objective,
            min_test_bars=settings.min_bars_beyond_warmup,
            on_generation=on_generation,
        )

    emit(result, fmt, console=console, render=render_optimization, output=output)

    if strategy_out is not None:
        strategy_out.write_text(result.optimized_yaml, encoding="utf-8")
        err_console.print(f"[green]wrote[/green] {strategy_out}")
    elif fmt is OutputFormat.TABLE:
        console.print()
        typer.echo(result.optimized_yaml, nl=False)
