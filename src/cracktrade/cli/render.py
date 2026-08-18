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
        ValidationReport,
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
    _render_verdict(result, console)
    _render_warnings(result, console)
    console.print()
    _render_metrics(result.metrics, result.benchmark.benchmark, console)
    console.print()
    _render_yearly(result.metrics, console)
    _render_vintage(result, console)


def _render_verdict(result: BacktestResult, console: Console) -> None:
    comparison = result.benchmark
    beat = comparison.beats_buy_and_hold
    colour = "green" if beat else "red"
    verdict = "BEATS" if beat else "LOSES TO"

    console.print(
        f"[bold]{result.strategy_name}[/bold] on [bold]{result.ticker}[/bold] "
        f"({result.vintage.first_bar} to {result.vintage.last_bar})"
    )
    console.print(
        f"  returned [bold]{result.metrics.total_return_pct:+.1f}%[/bold] and "
        f"[bold {colour}]{verdict}[/bold {colour}] buy-and-hold at "
        f"{comparison.benchmark.total_return_pct:+.1f}% "
        f"([{colour}]{comparison.excess_return_pct:+.1f} pp[/{colour}])"
    )


def _render_warnings(result: BacktestResult, console: Console) -> None:
    """Everything that should stop a reader trusting the numbers above."""
    if not result.metrics.has_enough_trades_to_judge:
        console.print(
            f"  [yellow]! only {result.metrics.total_trades} closed trade(s) — too few to "
            f"support a conclusion[/yellow]"
        )
    if result.entry_defined_pct < _DEFINEDNESS_WARNING:
        console.print(
            f"  [yellow]! the entry condition was undefined on "
            f"{100 - result.entry_defined_pct:.0f}% of bars[/yellow]"
        )
    if result.exit_defined_pct is not None and result.exit_defined_pct < _DEFINEDNESS_WARNING:
        console.print(
            f"  [yellow]! the exit condition was undefined on "
            f"{100 - result.exit_defined_pct:.0f}% of bars[/yellow]"
        )
    if result.shadowed_stops:
        console.print(
            f"  [yellow]! {', '.join(result.shadowed_stops)} set but inactive; "
            f"{result.active_stop} wins the stop priority chain[/yellow]"
        )
    if result.vintage.filled_bars:
        console.print(
            f"  [yellow]! {result.vintage.filled_bars} bar(s) "
            f"({result.vintage.filled_pct:.1f}%) were forward-filled, not observed[/yellow]"
        )


def _render_metrics(strategy: Metrics, benchmark: Metrics, console: Console) -> None:
    table = Table(title="Performance", title_justify="left", header_style="bold")
    table.add_column("")
    table.add_column("Strategy", justify="right")
    table.add_column("Buy & hold", justify="right")

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


def _render_yearly(strategy: Metrics, console: Console) -> None:
    """Per-year returns, because an aggregate hides where the profit actually came from."""
    if not strategy.yearly_returns:
        return
    table = Table(title="Return by year", title_justify="left", header_style="bold")
    table.add_column("Year")
    table.add_column("Return", justify="right")
    for year in strategy.yearly_returns:
        colour = "green" if year.return_pct >= 0 else "red"
        table.add_row(str(year.year), f"[{colour}]{year.return_pct:+.2f}%[/{colour}]")
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
    console.print(f"  objective {result.objective}")

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
    # The floor is named beside the count it explains. "412 infeasible" reads as a broken
    # search; "412 infeasible (fewer than 48 trades)" reads as the constraint doing its job,
    # and tells the user the one number they would change to loosen it.
    floor = (
        f" (fewer than {result.min_trades_required} trades)"
        if result.min_trades_required is not None
        else ""
    )
    counts = (
        f"{result.failures} failed, {result.infeasible} infeasible{floor}"
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


def render_validation(report: ValidationReport, console: Console) -> None:
    """Print a walk-forward validation report.

    Ordering follows spec section 12.8, and it is the opposite of how a backtest usually gets
    presented. The verdict comes first, then the evidence for and against it, and the headline
    return comes last. A reader who stops after two lines should already know whether the
    result is worth anything.
    """
    _render_validation_verdict(report, console)
    console.print()
    _render_folds(report, console)
    console.print()
    _render_robustness(report, console)
    console.print()
    _render_costs(report, console)
    _render_validation_footer(report, console)


def _render_validation_verdict(report: ValidationReport, console: Console) -> None:
    credible = report.is_credible
    colour = "green" if credible else "red"
    verdict = "CREDIBLE" if credible else "NOT CREDIBLE"

    console.print(
        f"[bold]{report.strategy_name}[/bold] on [bold]{report.ticker}[/bold] — "
        f"{len(report.folds)} {report.scheme} walk-forward folds, objective {report.objective}"
    )
    console.print(f"  [bold {colour}]{verdict}[/bold {colour}]")
    console.print(
        f"  out-of-sample {report.combined_return_pct:+.1f}% vs "
        f"{report.benchmark.total_return_pct:+.1f}% buy-and-hold, "
        f"profitable in {report.profitable_folds}/{len(report.folds)} folds"
    )
    for failure in report.failures:
        console.print(f"  [red]x[/red] {failure}")


def _render_folds(report: ValidationReport, console: Console) -> None:
    table = Table(title="Per-fold, out of sample", title_justify="left", header_style="bold")
    table.add_column("Fold")
    table.add_column("Period")
    table.add_column("Return", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Trades", justify="right")

    for fold in report.folds:
        colour = "green" if fold.was_profitable else "red"
        table.add_row(
            str(fold.index + 1),
            f"{fold.first_test_bar} to {fold.last_test_bar}",
            f"[{colour}]{fold.metrics.total_return_pct:+.2f}%[/{colour}]",
            f"{fold.metrics.max_drawdown_pct:.2f}%",
            str(fold.metrics.total_trades),
        )
    console.print(table)
    console.print(
        f"  median {report.median_return_pct:+.2f}%, interquartile spread "
        f"{report.return_iqr_pct:.2f} pp — how much the answer depends on when you ran it"
    )


def _render_robustness(report: ValidationReport, console: Console) -> None:
    table = Table(title="Robustness", title_justify="left", header_style="bold")
    table.add_column("Check")
    table.add_column("Result", justify="right")
    table.add_column("Verdict", justify="right")

    deflated = report.deflated
    overfitting = report.overfitting
    stability = report.stability
    interval = report.mean_return_interval

    rows = [
        (
            f"Deflated Sharpe ({report.trials} trials)",
            f"P={deflated.probability:.3f}",
            deflated.is_significant,
        ),
        (
            "Sharpe vs luck threshold",
            f"{deflated.observed:.3f} vs {deflated.threshold:.3f}",
            deflated.beats_the_lucky_threshold,
        ),
        (
            f"Overfitting probability ({overfitting.combinations} splits)",
            f"{overfitting.probability:.2f}",
            overfitting.is_acceptable,
        ),
        (
            "Parameter stability (10% nudge)",
            f"-{100 * stability.worst_small_degradation:.0f}%",
            stability.is_stable,
        ),
        (
            "Mean bar return, 95% interval",
            f"[{100 * interval.low:+.3f}%, {100 * interval.high:+.3f}%]",
            interval.excludes_zero and interval.point > 0,
        ),
        ("Fold win rate", f"{100 * report.fold_win_rate:.0f}%", report.fold_win_rate >= 0.5),
    ]
    for label, value, passed in rows:
        mark = "[green]pass[/green]" if passed else "[red]fail[/red]"
        table.add_row(label, value, mark)
    console.print(table)

    if deflated.variance_estimated:
        console.print(
            "  [dim]Trial Sharpes were unavailable (parallel search), so the deflation used the "
            "estimator variance — a weaker, more permissive correction.[/dim]"
        )
    if stability.fragile_parameters:
        console.print(f"  [yellow]fragile: {', '.join(stability.fragile_parameters)}[/yellow]")


def _render_costs(report: ValidationReport, console: Console) -> None:
    table = Table(title="Cost sensitivity", title_justify="left", header_style="bold")
    table.add_column("Slippage")
    table.add_column("Return", justify="right")
    for scenario in report.costs.scenarios:
        colour = "green" if scenario.metrics.total_return_pct > 0 else "red"
        table.add_row(
            f"{scenario.multiple:g}x ({scenario.slippage_pct:.3g}%)",
            f"[{colour}]{scenario.metrics.total_return_pct:+.2f}%[/{colour}]",
        )
    console.print(table)
    if report.costs.break_even_multiple is not None:
        console.print(
            f"  [yellow]! the edge disappears at "
            f"{report.costs.break_even_multiple:g}x slippage[/yellow]"
        )


def _render_validation_footer(report: ValidationReport, console: Console) -> None:
    console.print()
    console.print(
        f"[dim]{report.total_trades} out-of-sample trades, {report.trials} configurations "
        f"scored, seed {report.seed}, {report.elapsed_seconds:.1f}s[/dim]"
    )
