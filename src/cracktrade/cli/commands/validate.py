"""``cracktrade validate`` -- check a strategy file without running anything."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.config import dump_strategy
from cracktrade.strategy import load_strategy

console = Console()


def validate(
    strategy_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to the strategy YAML file.",
        ),
    ],
    show: Annotated[
        bool,
        typer.Option("--show", help="Print the normalised strategy after validating."),
    ] = False,
) -> None:
    """Validate a strategy file and report any problems.

    Exits non-zero when the strategy is invalid, so this is usable as a pre-commit or CI check.
    """
    strategy = load_strategy(strategy_file)

    if show:
        # Normalised YAML goes to stdout so it can be piped; status goes to stderr.
        typer.echo(dump_strategy(strategy), nl=False)

    console.print(
        f"[green]ok[/green] {strategy.strategy.name}: "
        f"{strategy.universe.ticker}, "
        f"{strategy.universe.start_date} to {strategy.universe.end_date}, "
        f"{len(strategy.indicators)} indicator(s), "
        f"{len(strategy.entry_variants)}x{len(strategy.exit_variants)} variant combinations",
        style=None,
    )
