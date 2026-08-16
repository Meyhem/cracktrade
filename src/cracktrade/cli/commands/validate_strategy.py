"""``cracktrade walkforward`` -- optimize across folds and judge the result."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.cli.render import render_validation
from cracktrade.data import FrameCache, YFinanceProvider, load_history
from cracktrade.optimize import DEFAULT_OBJECTIVE, OBJECTIVES
from cracktrade.settings import load_settings
from cracktrade.strategy import load_strategy, required_warmup
from cracktrade.validate import DEFAULT_FOLDS, FoldScheme, walk_forward

console = Console()


def walkforward(
    strategy_file: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="Path to the strategy YAML file."
        ),
    ],
    folds: Annotated[
        int, typer.Option("--folds", min=1, help="Walk-forward folds.")
    ] = DEFAULT_FOLDS,
    scheme: Annotated[
        FoldScheme, typer.Option("--scheme", help="Anchored or rolling training window.")
    ] = FoldScheme.ANCHORED,
    epochs: Annotated[
        int, typer.Option("--epochs", min=1, help="Search generations per fold.")
    ] = 10,
    objective: Annotated[
        str, typer.Option("--objective", help=f"One of: {', '.join(sorted(OBJECTIVES))}.")
    ] = DEFAULT_OBJECTIVE,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the final fold's strategy YAML here."),
    ] = None,
    cache: Annotated[
        bool, typer.Option("--cache/--no-cache", help="Cache downloaded price history.")
    ] = False,
) -> None:
    """Optimize across successive walk-forward folds and judge whether the result is credible.

    Each fold searches on its own training window and is evaluated once on the window that
    follows it. What comes back is a distribution rather than a single number, plus the
    statistics that say whether the distribution is distinguishable from luck: a Sharpe deflated
    for the number of trials, the probability of backtest overfitting, parameter stability, and
    sensitivity to costs.
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

    report = walk_forward(
        strategy,
        data,
        folds=folds,
        scheme=scheme,
        epochs=epochs,
        seed=settings.seed,
        workers=settings.workers,
        objective_name=objective,
        min_test_bars=settings.min_bars_beyond_warmup,
    )

    render_validation(report, console)

    if output is not None:
        output.write_text(report.optimized_yaml, encoding="utf-8")
        console.print(f"\n[green]wrote[/green] {output}")
