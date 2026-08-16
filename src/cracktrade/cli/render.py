"""Rendering a :class:`~cracktrade.domain.BacktestResult` for a terminal.

The ordering is deliberate and follows spec section 12.8: **the verdict comes first**. A report
that opens with a large profit figure and buries the benchmark underneath is how a backtest
persuades someone to act on a result that never beat simply holding the ticker.

Rendering lives in the CLI, not the engine. The engine returns value objects; this module is one
consumer of them and a future HTTP interface is another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from cracktrade.domain import (
        BacktestResult,
        Metrics,
        OptimizationResult,
        VariantResult,
    )

#: Below this, a signal is undefined often enough that the strategy under test is not the one
#: its author believes they wrote.
_DEFINEDNESS_WARNING = 95.0

#: In-sample CAGR exceeding out-of-sample by more than this many points reads as a curve fit.
_OVERFIT_GAP_WARNING = 10.0

#: Share of failed candidates above which the search was not really searching.
_FAILURE_WARNING_PCT = 20.0


def render_result(result: BacktestResult, console: Console) -> None:
    """Print a full backtest report, verdict first."""
    best = result.best

    _render_verdict(result, console)
    _render_warnings(result, console)
    console.print()
    _render_metrics(best, result.benchmark.benchmark, console)
    console.print()
    _render_yearly(best, console)
    _render_variants(result, console)
    _render_vintage(result, console)


def _render_verdict(result: BacktestResult, console: Console) -> None:
    best = result.best
    comparison = result.benchmark
    beat = comparison.beats_buy_and_hold
    colour = "green" if beat else "red"
    verdict = "BEATS" if beat else "LOSES TO"

    console.print(
        f"[bold]{result.strategy_name}[/bold] on [bold]{result.ticker}[/bold] "
        f"({result.vintage.first_bar} to {result.vintage.last_bar})"
    )
    console.print(
        f"  best variant [bold]{best.label}[/bold] returned "
        f"[bold]{best.metrics.total_return_pct:+.1f}%[/bold] and "
        f"[bold {colour}]{verdict}[/bold {colour}] buy-and-hold at "
        f"{comparison.benchmark.total_return_pct:+.1f}% "
        f"([{colour}]{comparison.excess_return_pct:+.1f} pp[/{colour}])"
    )


def _render_warnings(result: BacktestResult, console: Console) -> None:
    """Everything that should stop a reader trusting the numbers above."""
    best = result.best

    if not best.metrics.has_enough_trades_to_judge:
        console.print(
            f"  [yellow]! only {best.metrics.total_trades} closed trade(s) — too few to "
            f"support a conclusion[/yellow]"
        )
    if best.entry_defined_pct < _DEFINEDNESS_WARNING:
        console.print(
            f"  [yellow]! the entry condition was undefined on "
            f"{100 - best.entry_defined_pct:.0f}% of bars[/yellow]"
        )
    if best.exit_defined_pct is not None and best.exit_defined_pct < _DEFINEDNESS_WARNING:
        console.print(
            f"  [yellow]! the exit condition was undefined on "
            f"{100 - best.exit_defined_pct:.0f}% of bars[/yellow]"
        )
    if best.shadowed_stops:
        console.print(
            f"  [yellow]! {', '.join(best.shadowed_stops)} set but inactive; "
            f"{best.active_stop} wins the stop priority chain[/yellow]"
        )
    if result.vintage.filled_bars:
        console.print(
            f"  [yellow]! {result.vintage.filled_bars} bar(s) "
            f"({result.vintage.filled_pct:.1f}%) were forward-filled, not observed[/yellow]"
        )


def _render_metrics(best: VariantResult, benchmark: Metrics, console: Console) -> None:
    table = Table(title="Performance", title_justify="left", header_style="bold")
    table.add_column("")
    table.add_column("Strategy", justify="right")
    table.add_column("Buy & hold", justify="right")

    strategy = best.metrics
    rows: list[tuple[str, str, str]] = [
        (
            "Total return",
            f"{strategy.total_return_pct:+.2f}%",
            f"{benchmark.total_return_pct:+.2f}%",
        ),
        ("CAGR", f"{strategy.cagr_pct:+.2f}%", f"{benchmark.cagr_pct:+.2f}%"),
        ("Max drawdown", f"{strategy.max_drawdown_pct:.2f}%", f"{benchmark.max_drawdown_pct:.2f}%"),
        (
            "Worst 12 months",
            f"{strategy.worst_rolling_12m_pct:+.2f}%",
            f"{benchmark.worst_rolling_12m_pct:+.2f}%",
        ),
        ("Sharpe", f"{strategy.sharpe_ratio:.2f}", f"{benchmark.sharpe_ratio:.2f}"),
        ("Sortino", f"{strategy.sortino_ratio:.2f}", f"{benchmark.sortino_ratio:.2f}"),
        ("Calmar", f"{strategy.calmar_ratio:.2f}", f"{benchmark.calmar_ratio:.2f}"),
        ("Exposure", f"{strategy.exposure_pct:.1f}%", f"{benchmark.exposure_pct:.1f}%"),
        ("Final equity", f"{strategy.final_equity:,.2f}", f"{benchmark.final_equity:,.2f}"),
        ("Trades", f"{strategy.total_trades}", f"{benchmark.total_trades}"),
        ("Win rate", f"{strategy.win_rate_pct:.1f}%", "—"),
        ("Profit factor", _ratio(strategy.profit_factor), "—"),
        ("Avg holding days", f"{strategy.avg_holding_days:.1f}", "—"),
        (
            "Best / worst trade",
            f"{strategy.best_trade_pnl:+,.2f} / {strategy.worst_trade_pnl:+,.2f}",
            "—",
        ),
    ]
    for label, left, right in rows:
        table.add_row(label, left, right)
    console.print(table)


def _render_yearly(best: VariantResult, console: Console) -> None:
    """Per-year returns, because an aggregate hides where the profit actually came from."""
    if not best.metrics.yearly_returns:
        return
    table = Table(title="Return by year", title_justify="left", header_style="bold")
    table.add_column("Year")
    table.add_column("Return", justify="right")
    for year in best.metrics.yearly_returns:
        colour = "green" if year.return_pct >= 0 else "red"
        table.add_row(str(year.year), f"[{colour}]{year.return_pct:+.2f}%[/{colour}]")
    console.print(table)


def _render_variants(result: BacktestResult, console: Console) -> None:
    if len(result.variants) < 2:
        return
    console.print()
    table = Table(title="All variants", title_justify="left", header_style="bold")
    table.add_column("Variant")
    table.add_column("Return", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Trades", justify="right")

    best_label = result.best.label
    for variant in sorted(result.variants, key=lambda v: v.metrics.total_pnl, reverse=True):
        marker = " *" if variant.label == best_label else ""
        table.add_row(
            f"{variant.label}{marker}",
            f"{variant.metrics.total_return_pct:+.2f}%",
            f"{variant.metrics.max_drawdown_pct:.2f}%",
            f"{variant.metrics.sharpe_ratio:.2f}",
            str(variant.metrics.total_trades),
        )
    console.print(table)


def _render_vintage(result: BacktestResult, console: Console) -> None:
    """What the result was computed from, so a later divergence is explainable."""
    vintage = result.vintage
    console.print()
    console.print(
        f"[dim]{vintage.bars} bars, warm-up {result.warmup_bars}, "
        f"risk-free {100 * result.risk_free_rate:.2f}%/yr, "
        f"data {vintage.frame_digest} fetched {vintage.fetched_on}[/dim]"
    )
    console.print(
        "[dim]Sharpe and Sortino charge the risk-free rate across the whole period while idle "
        "cash earns nothing; read them next to exposure.[/dim]"
    )


def _ratio(value: float) -> str:
    """Format a ratio that may legitimately be infinite."""
    return "∞" if value == float("inf") else f"{value:.2f}"


def render_optimization(result: OptimizationResult, console: Console) -> None:
    """Print an optimization report.

    Out-of-sample numbers lead. The in-sample figures appear beside them rather than instead of
    them, because the gap between the two is the cheapest overfitting diagnostic there is and a
    report showing only the flattering half would mislead by omission.
    """
    console.print(
        f"[bold]{result.strategy_name}[/bold] on [bold]{result.ticker}[/bold] — "
        f"optimized on {result.train_bars} bars, reported on {result.test_bars} unseen bars"
    )
    console.print(f"  surviving variant [bold]{result.label}[/bold], objective {result.objective}")

    improvement = result.improvement_pct
    colour = "green" if improvement > 0 else "red"
    console.print(
        f"  out-of-sample return [bold]{result.test_metrics.total_return_pct:+.2f}%[/bold] "
        f"vs [bold]{result.baseline_test_metrics.total_return_pct:+.2f}%[/bold] unoptimized "
        f"([{colour}]{improvement:+.2f} pp[/{colour}])"
    )

    _render_optimizer_warnings(result, console)
    console.print()

    table = Table(title="Out-of-sample vs in-sample", title_justify="left", header_style="bold")
    table.add_column("")
    table.add_column("Test (unseen)", justify="right")
    table.add_column("Train (fitted)", justify="right")
    test, train = result.test_metrics, result.train_metrics
    for label, left, right in [
        ("Total return", f"{test.total_return_pct:+.2f}%", f"{train.total_return_pct:+.2f}%"),
        ("CAGR", f"{test.cagr_pct:+.2f}%", f"{train.cagr_pct:+.2f}%"),
        ("Max drawdown", f"{test.max_drawdown_pct:.2f}%", f"{train.max_drawdown_pct:.2f}%"),
        ("Sharpe", f"{test.sharpe_ratio:.2f}", f"{train.sharpe_ratio:.2f}"),
        ("Calmar", f"{test.calmar_ratio:.2f}", f"{train.calmar_ratio:.2f}"),
        ("Trades", str(test.total_trades), str(train.total_trades)),
    ]:
        table.add_row(label, left, right)
    console.print(table)

    _render_changes(result, console)
    _render_diagnostics(result, console)


def _render_optimizer_warnings(result: OptimizationResult, console: Console) -> None:
    if not result.test_metrics.has_enough_trades_to_judge:
        console.print(
            f"  [yellow]! only {result.test_metrics.total_trades} out-of-sample trade(s) — "
            f"too few to support a conclusion[/yellow]"
        )
    if result.overfitting_gap_pct > _OVERFIT_GAP_WARNING:
        console.print(
            f"  [yellow]! in-sample CAGR exceeds out-of-sample by "
            f"{result.overfitting_gap_pct:.1f} pp — the hallmark of a curve fit[/yellow]"
        )
    for change in result.parameters_at_bound:
        console.print(
            f"  [yellow]! {change.path} settled on its search bound "
            f"({change.new_value:g}); the range was the binding constraint, not the data"
            f"[/yellow]"
        )
    if result.counts_exact and result.failures and result.evaluations:
        share = 100.0 * result.failures / result.evaluations
        if share > _FAILURE_WARNING_PCT:
            console.print(
                f"  [yellow]! {share:.0f}% of candidates failed "
                f"(most often {result.most_common_failure}); the search explored far less than "
                f"it appears to[/yellow]"
            )


def _render_changes(result: OptimizationResult, console: Console) -> None:
    if not result.changes:
        return
    table = Table(title="Parameters", title_justify="left", header_style="bold")
    table.add_column("Path")
    table.add_column("From", justify="right")
    table.add_column("To", justify="right")
    table.add_column("Range", justify="right")
    for change in result.changes:
        arrow = f"{change.new_value:g}"
        if change.at_bound:
            arrow = f"[yellow]{arrow}[/yellow]"
        table.add_row(
            change.path,
            f"{change.old_value:g}",
            arrow,
            f"[{change.low:g}, {change.high:g}]",
        )
    console.print(table)


def _render_diagnostics(result: OptimizationResult, console: Console) -> None:
    console.print()
    counts = (
        f"{result.failures} failed, {result.infeasible} infeasible"
        if result.counts_exact
        else "failure counts unavailable (parallel run)"
    )
    console.print(
        f"[dim]{result.evaluations} evaluations ({counts}), {result.trials} configurations "
        f"scored in total, seed {result.seed}, {result.elapsed_seconds:.1f}s — "
        f"{result.convergence_message}[/dim]"
    )
    console.print(
        "[dim]One split, evaluated once. The maximum over many trials is inflated even with no "
        "edge; walk-forward folds and a deflated Sharpe land in the next phase.[/dim]"
    )
