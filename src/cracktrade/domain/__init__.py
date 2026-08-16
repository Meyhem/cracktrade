"""Result value objects.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 8 and 11.

Everything here is a frozen dataclass with no pandas and no vectorbt in its public fields. The
vectorbt column names (``Entry Timestamp``, ``PnL``, ...) are an implementation detail of the
backtest layer, and a CLI or an HTTP interface should never have to know them. This is the
boundary: below it the engine speaks vectorbt, above it everything speaks these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = [
    "MIN_TRADES_TO_JUDGE",
    "BacktestResult",
    "BenchmarkComparison",
    "DataVintage",
    "Metrics",
    "OptimizationResult",
    "ParameterChange",
    "Trade",
    "VariantResult",
    "YearReturn",
]


@dataclass(frozen=True, slots=True)
class Trade:
    """One closed or still-open position."""

    entry_date: date
    exit_date: date | None
    entry_price: float
    exit_price: float | None
    size: float
    pnl: float
    return_pct: float
    fees: float
    holding_days: int
    is_open: bool

    @property
    def is_winner(self) -> bool:
        """Whether the trade made money after costs."""
        return self.pnl > 0


@dataclass(frozen=True, slots=True)
class YearReturn:
    """A single calendar year's return.

    "All the profit came from one year" is the most common way a backtest misleads, and no
    aggregate figure can reveal it (audit finding B11).
    """

    year: int
    return_pct: float


@dataclass(frozen=True, slots=True)
class Metrics:
    """The performance of one equity curve.

    ``exposure_pct`` sits alongside the risk-adjusted figures deliberately. Idle cash earns
    nothing in this engine while ``risk_free_rate`` is charged as the Sharpe hurdle across the
    whole period, so a strategy in the market a quarter of the time is charged a full hurdle on
    all of it. That is the standard excess-return frame, but it is only interpretable next to
    the exposure (audit finding B9).
    """

    total_trades: int
    win_rate_pct: float
    profit_factor: float
    total_pnl: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    exposure_pct: float
    avg_holding_days: float
    best_trade_pnl: float
    worst_trade_pnl: float
    bars: int
    yearly_returns: tuple[YearReturn, ...] = ()
    worst_rolling_12m_pct: float = 0.0

    @property
    def has_enough_trades_to_judge(self) -> bool:
        """Whether the trade count can support any conclusion at all.

        Twenty is not a statistical guarantee, it is a floor below which a point estimate is
        actively misleading. Phase 8.5 replaces this with confidence intervals; until then it
        keeps the CLI from printing a precise-looking CAGR off six trades (audit finding B7).
        """
        return self.total_trades >= MIN_TRADES_TO_JUDGE


#: See :attr:`Metrics.has_enough_trades_to_judge`.
MIN_TRADES_TO_JUDGE = 20


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    """The strategy measured against buying and holding the same ticker.

    The decisive number for "should I invest", and the one the legacy system never produced: it
    compared an optimized strategy against its own unoptimized self, which answers whether the
    optimizer did something, not whether the result is worth owning (audit finding B1).
    """

    benchmark: Metrics
    excess_return_pct: float
    excess_cagr_pct: float
    information_ratio: float

    @property
    def beats_buy_and_hold(self) -> bool:
        """Whether the strategy returned more than simply holding the ticker."""
        return self.excess_return_pct > 0


@dataclass(frozen=True, slots=True)
class DataVintage:
    """Which prices produced a result.

    ``auto_adjust`` retro-adjusts the whole series on every dividend and split, so the same
    backtest run a quarter apart uses different prices. Recording the vintage makes a divergence
    explainable rather than merely alarming (audit finding B10).
    """

    ticker: str
    first_bar: date
    last_bar: date
    bars: int
    filled_bars: int
    fetched_on: date
    frame_digest: str

    @property
    def filled_pct(self) -> float:
        """Share of bars that were forward-filled rather than observed."""
        return 100.0 * self.filled_bars / self.bars if self.bars else 0.0


@dataclass(frozen=True, slots=True)
class VariantResult:
    """One entry/exit variant pair, evaluated."""

    entry_name: str
    exit_name: str
    metrics: Metrics
    trades: tuple[Trade, ...]
    entry_defined_pct: float
    exit_defined_pct: float | None
    active_stop: str | None
    shadowed_stops: tuple[str, ...]

    @property
    def label(self) -> str:
        """How this pair is named in output."""
        return f"{self.entry_name} / {self.exit_name}"


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything one backtest produced."""

    strategy_name: str
    ticker: str
    vintage: DataVintage
    variants: tuple[VariantResult, ...]
    benchmark: BenchmarkComparison
    risk_free_rate: float
    warmup_bars: int

    @property
    def best(self) -> VariantResult:
        """The variant with the highest total PnL.

        Selection on a single metric, over the same data every variant was measured on. That is
        a selection step and it inflates the winner; spec section 12 is what quantifies it.
        """
        return max(self.variants, key=lambda variant: variant.metrics.total_pnl)


@dataclass(frozen=True, slots=True)
class ParameterChange:
    """One parameter the search moved, and the range it was allowed to move within."""

    path: str
    old_value: float
    new_value: float
    low: float
    high: float

    @property
    def moved(self) -> bool:
        """Whether the search settled somewhere other than the configured value."""
        return self.new_value != self.old_value

    @property
    def at_bound(self) -> bool:
        """Whether the optimum sits on the edge of its search range.

        A parameter pinned to its bound usually means the true optimum lies outside it, so the
        range was the binding constraint rather than the data. Worth widening and re-running.
        """
        return self.new_value in (self.low, self.high)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Everything one optimization run produced.

    ``test_metrics`` are the only out-of-sample numbers. ``train_metrics`` are reported beside
    them deliberately: the gap between the two is the cheapest overfitting diagnostic there is,
    and a report that showed only the flattering half would be misleading by omission.
    """

    strategy_name: str
    ticker: str
    objective: str
    optimized_yaml: str
    entry_name: str
    exit_name: str
    test_metrics: Metrics
    train_metrics: Metrics
    baseline_test_metrics: Metrics
    changes: tuple[ParameterChange, ...]
    trades: tuple[Trade, ...]
    train_bars: int
    test_bars: int
    evaluations: int
    failures: int
    infeasible: int
    counts_exact: bool
    trials: int
    budget: int
    seed: int
    elapsed_seconds: float
    convergence_message: str
    most_common_failure: str | None

    @property
    def label(self) -> str:
        """The surviving entry/exit pair."""
        return f"{self.entry_name} / {self.exit_name}"

    @property
    def improvement_pct(self) -> float:
        """Out-of-sample gain over the unoptimized configuration.

        Both sides are measured on the *test* window, so this compares like with like. Legacy
        compared an in-sample optimized figure against an in-sample baseline, which was at least
        consistent but inflated both.
        """
        return self.test_metrics.total_return_pct - self.baseline_test_metrics.total_return_pct

    @property
    def overfitting_gap_pct(self) -> float:
        """How much better the strategy looked in-sample than out.

        A large positive gap is the signature of a curve fit. It is not proof of one -- windows
        differ -- but it is the first thing to look at.
        """
        return self.train_metrics.cagr_pct - self.test_metrics.cagr_pct

    @property
    def parameters_at_bound(self) -> tuple[ParameterChange, ...]:
        """Parameters whose optimum sits on the edge of its search range."""
        return tuple(change for change in self.changes if change.at_bound)
