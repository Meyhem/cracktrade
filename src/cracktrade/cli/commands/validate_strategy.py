"""``cracktrade walkforward`` -- optimize across folds and judge the result."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.cli.exit_codes import ExitCode
from cracktrade.cli.output import OutputFormat, emit
from cracktrade.cli.render import render_validation
from cracktrade.data import FrameCache, YFinanceProvider, load_history
from cracktrade.optimize import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, OBJECTIVES, TradeFloor
from cracktrade.settings import load_settings
from cracktrade.strategy import load_strategy, required_warmup
from cracktrade.validate import DEFAULT_FOLDS, FoldScheme, walk_forward

console = Console()
err_console = Console(stderr=True)


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
    min_trades: Annotated[
        int,
        typer.Option(
            "--min-trades",
            min=0,
            help=(
                "Closed trades a candidate must produce on a fold's train window to be scored "
                "at all. Below it the candidate is rejected, not discounted."
            ),
        ),
    ] = DEFAULT_TRADE_FLOOR.minimum,
    min_trades_per_year: Annotated[
        float,
        typer.Option(
            "--min-trades-per-year",
            min=0.0,
            help=(
                "Additional trade floor per year of train window, so the constraint does not "
                "weaken as folds grow. 0 disables it."
            ),
        ),
    ] = DEFAULT_TRADE_FLOOR.per_year,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", help="How to present the result.")
    ] = OutputFormat.TABLE,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the result here instead of stdout."),
    ] = None,
    strategy_out: Annotated[
        Path | None,
        typer.Option("--strategy-out", help="Write the final fold's strategy YAML here."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero when the result is not credible."),
    ] = False,
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

    With --strict the process exits 6 when the result fails any of those checks, so this can
    gate a pipeline.
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
        trade_floor=TradeFloor(minimum=min_trades, per_year=min_trades_per_year),
        min_test_bars=settings.min_bars_beyond_warmup,
    )

    emit(report, fmt, console=console, render=render_validation, output=output)

    if strategy_out is not None:
        strategy_out.write_text(report.optimized_yaml, encoding="utf-8")
        err_console.print(f"[green]wrote[/green] {strategy_out}")

    if strict and not report.is_credible:
        raise typer.Exit(ExitCode.NOT_CREDIBLE)
