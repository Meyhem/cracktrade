"""``cracktrade evolve`` -- compose a strategy for a ticker and judge it on unseen history."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cracktrade.cli.exit_codes import ExitCode
from cracktrade.cli.output import OutputFormat, emit
from cracktrade.cli.progress import search_progress
from cracktrade.cli.render import render_evolution
from cracktrade.data import FrameCache, YFinanceProvider, load_history
from cracktrade.evolution import (
    DEFAULT_GENERATIONS,
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_MIN_SEGMENT_BARS,
    DEFAULT_POPULATION,
    DEFAULT_SEGMENTS,
    Chassis,
    GaSettings,
    library_warmup,
)
from cracktrade.evolution import (
    evolve as run_evolve,
)
from cracktrade.optimize import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, OBJECTIVES, TradeFloor
from cracktrade.settings import load_settings

console = Console()
err_console = Console(stderr=True)

#: History requested when the user does not say. The block library reaches back 200 bars, and
#: the run needs four evolution segments plus a holdout on top of that, so a short default would
#: fail the division rather than produce a fast answer.
DEFAULT_YEARS = 12


def evolve(
    ticker: Annotated[str, typer.Argument(help="The symbol to compose a strategy for, e.g. AAPL.")],
    start: Annotated[
        str | None,
        typer.Option("--start", help=f"First bar, YYYY-MM-DD. Defaults to {DEFAULT_YEARS}y ago."),
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="Last bar, YYYY-MM-DD. Defaults to today.")
    ] = None,
    generations: Annotated[
        int, typer.Option("--generations", min=1, help="Generations to evolve.")
    ] = DEFAULT_GENERATIONS,
    population: Annotated[
        int, typer.Option("--population", min=2, help="Genomes per generation.")
    ] = DEFAULT_POPULATION,
    segments: Annotated[
        int,
        typer.Option(
            "--segments",
            min=1,
            help=(
                "Contiguous windows the evolution region is cut into. A genome is scored by its "
                "median segment, so more segments means a harder consistency bar. Below four, "
                "the overfitting probability cannot be computed."
            ),
        ),
    ] = DEFAULT_SEGMENTS,
    holdout: Annotated[
        float,
        typer.Option(
            "--holdout",
            min=0.05,
            max=0.5,
            help="Share of history withheld from evolution entirely and scored once, at the end.",
        ),
    ] = DEFAULT_HOLDOUT_FRACTION,
    objective: Annotated[
        str, typer.Option("--objective", help=f"One of: {', '.join(sorted(OBJECTIVES))}.")
    ] = DEFAULT_OBJECTIVE,
    min_trades: Annotated[
        int,
        typer.Option(
            "--min-trades",
            min=0,
            help=(
                "Closed trades a genome must produce across the whole evolution region to be "
                "scored at all. Below it the genome is rejected, not discounted."
            ),
        ),
    ] = DEFAULT_TRADE_FLOOR.minimum,
    min_trades_per_year: Annotated[
        float,
        typer.Option(
            "--min-trades-per-year",
            min=0.0,
            help="Additional trade floor per year of evolution region. 0 disables it.",
        ),
    ] = DEFAULT_TRADE_FLOOR.per_year,
    capital: Annotated[
        float, typer.Option("--capital", min=1.0, help="Starting equity.")
    ] = 100_000.0,
    slippage: Annotated[
        float, typer.Option("--slippage", min=0.0, help="Per-side slippage, percent.")
    ] = 0.1,
    commission: Annotated[
        float, typer.Option("--commission", min=0.0, help="Per-side commission, percent.")
    ] = 0.1,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", help="How to present the result.")
    ] = OutputFormat.TABLE,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write the result here instead of stdout."),
    ] = None,
    strategy_out: Annotated[
        Path | None,
        typer.Option("--strategy-out", help="Write the evolved strategy YAML here."),
    ] = None,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit non-zero when the result is not credible.")
    ] = False,
    cache: Annotated[
        bool, typer.Option("--cache/--no-cache", help="Cache downloaded price history.")
    ] = False,
) -> None:
    """Compose a trading strategy for one ticker by evolution, then judge it honestly.

    A genetic algorithm assembles conditions from a curated block library, choosing the
    structure of the strategy and its parameters together. Genomes are scored on the median of
    several contiguous segments of history, so a candidate that made all its money in one lucky
    stretch does not win.

    The last slice of history is withheld from the search entirely and evaluated once, on the
    winner. That number is the only out-of-sample one, and it is reported next to the
    buy-and-hold return, a Sharpe deflated for how many distinct strategies were tried, the
    probability of backtest overfitting, parameter stability, and sensitivity to costs.

    Expect most runs to come back NOT CREDIBLE. Searching a large space of strategies produces
    an impressive-looking winner whether or not the ticker holds any edge at all, and the checks
    exist to say which of the two happened. With --strict the process exits 6 when the result
    fails any of them.
    """
    settings = load_settings()
    today = date.today()

    chassis = Chassis(
        name=f"evolved_{ticker.strip().lower()}",
        ticker=ticker,
        start_date=_parse_date(start, "--start", today - timedelta(days=365 * DEFAULT_YEARS)),
        end_date=_parse_date(end, "--end", today),
        initial_capital=capital,
        slippage_pct=slippage,
        commission_pct=commission,
    )

    data = load_history(
        chassis.data_config(),
        YFinanceProvider(),
        cache=FrameCache(settings.cache_dir) if cache else None,
        # Sized from the library rather than from one strategy: any genome the search can reach
        # may need the longest window any block can ask for.
        min_bars=library_warmup() + settings.min_bars_beyond_warmup,
        max_filled_fraction=settings.max_filled_fraction,
    )

    with search_progress(
        total=generations, label="evolving", enabled=fmt is OutputFormat.TABLE
    ) as on_generation:
        result = run_evolve(
            chassis,
            data,
            settings=GaSettings(population=population, generations=generations),
            seed=settings.seed,
            workers=settings.workers,
            objective_name=objective,
            trade_floor=TradeFloor(minimum=min_trades, per_year=min_trades_per_year),
            segments=segments,
            holdout_fraction=holdout,
            min_segment_bars=DEFAULT_MIN_SEGMENT_BARS,
            on_generation=on_generation,
        )

    emit(result, fmt, console=console, render=render_evolution, output=output)

    if strategy_out is not None:
        strategy_out.write_text(result.strategy_yaml, encoding="utf-8")
        err_console.print(f"[green]wrote[/green] {strategy_out}")
    elif fmt is OutputFormat.TABLE:
        console.print()
        typer.echo(result.strategy_yaml, nl=False)

    if strict and not result.is_credible:
        raise typer.Exit(ExitCode.NOT_CREDIBLE)


def _parse_date(value: str | None, option: str, fallback: date) -> date:
    """Read a ``YYYY-MM-DD`` option, or fall back.

    Raises:
        BadParameter: the value is not a date. Typer renders it as a usage error rather than a
            traceback, which is what a mistyped option deserves.
    """
    if value is None:
        return fallback
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        msg = f"{option} must be a date in YYYY-MM-DD format, got {value!r}"
        raise typer.BadParameter(msg) from None
