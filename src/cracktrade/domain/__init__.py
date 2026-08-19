"""Result value objects.

Normative reference: ``docs/ENGINE_SPEC.md`` sections 8 and 11.

Everything here is a frozen dataclass with no pandas and no vectorbt in its public fields. The
vectorbt column names (``Entry Timestamp``, ``PnL``, ...) are an implementation detail of the
backtest layer, and a CLI or an HTTP interface should never have to know them. This is the
boundary: below it the engine speaks vectorbt, above it everything speaks these types.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

__all__ = [
    "INFEASIBLE",
    "INSTABILITY_THRESHOLD",
    "MIN_TRADES_TO_JUDGE",
    "SIGNIFICANCE",
    "BacktestResult",
    "BenchmarkComparison",
    "CostScenario",
    "CostSensitivity",
    "DataVintage",
    "DeflatedSharpe",
    "EvolutionResult",
    "FoldResult",
    "HistoryScope",
    "Interval",
    "Metrics",
    "MonthlyReturns",
    "OptimizationResult",
    "OverfittingProbability",
    "ParameterChange",
    "RunSeries",
    "SegmentResult",
    "Series",
    "StabilityPoint",
    "StabilityReport",
    "Trade",
    "ValidationReport",
    "YearReturn",
]


@dataclass(frozen=True, slots=True)
class Series:
    """One dated series. Columnar, because that is how a chart and a CSV both want it.

    ``dates`` holds ``date`` on a daily run and ``datetime`` on an intraday one -- the latter is
    a subclass of the former, so one field covers both and each carries exactly the precision
    its bars have. Dropping the time intraday would stack a whole session on one x-value.
    """

    dates: tuple[date, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.dates) != len(self.values):
            raise ValueError("a series must have one value per date")


@dataclass(frozen=True, slots=True)
class MonthlyReturns:
    """Calendar-month returns, and whether the strategy held anything that month.

    ``in_market`` is the point. A month with no position and a month that happened to end flat
    are different facts, and a heatmap colouring both as zero overstates how consistently a
    strategy was working.
    """

    months: tuple[str, ...]
    values: tuple[float, ...]
    in_market: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class RunSeries:
    """Everything the chart tab draws for one run, or one walk-forward fold.

    Captured while the run executed and never recomputed: prices are retroactively adjusted, so
    a curve rebuilt later would describe different data than the metrics and the vintage block
    recorded beside it (spec section 8.1).
    """

    equity: Series
    benchmark_equity: Series
    drawdown: Series
    close: Series
    monthly_returns: MonthlyReturns
    rolling_12m_return: Series

    #: The bars in this window that were forward-filled rather than observed. Held as the
    #: dates themselves rather than as a per-bar mask: they are usually a handful out of
    #: thousands, and a chart marks them individually.
    #:
    #: ``DataVintage.filled_bars`` counts them; this says *where* they are, which is what a
    #: time-domain chart needs in order to mark them instead of drawing a straight line
    #: through a price that was never traded (spec section 8.1).
    filled: tuple[date, ...] = ()


@dataclass(frozen=True, slots=True)
class Trade:
    """One closed or still-open position.

    ``entry_date`` and ``exit_date`` are a plain ``date`` on a daily run and a ``datetime`` on
    an intraday one. The annotation covers both because ``datetime`` is a subclass of ``date``;
    the distinction is not cosmetic, since two trades in one Xetra session would otherwise
    report the same moment. It reaches a client through the serializer as ``2020-01-01`` or
    ``2026-08-18T17:00:00`` respectively, and the daily shape is byte-identical to what it was.

    ``holding_bars`` counts **bars**, and always did -- it is ``exit_idx - entry_idx``. It was
    called ``holding_days`` while every bar was a day. Reading "avg holding: 5" as five days
    when the bars are half-hours is exactly the authoritative-looking falsehood this engine
    exists to avoid, so the name now says what the number is.
    """

    entry_date: date
    exit_date: date | None
    entry_price: float
    exit_price: float | None
    size: float
    pnl: float
    return_pct: float
    fees: float
    holding_bars: int
    is_open: bool

    @property
    def is_winner(self) -> bool:
        """Whether the trade made money after costs."""
        return self.pnl > 0


@dataclass(frozen=True, slots=True)
class HistoryScope:
    """How much history a result rests on, and whether that is enough to mean anything.

    Built by :func:`cracktrade.history.scope_of`. Spec section 12.11.

    Stated in **sessions**, not bars. Forty Xetra sessions of 30-minute data is about 680 bars,
    which looks like plenty next to a daily backtest of 680 days and is nothing like it: forty
    independent days, most of the trades drawn from a handful of weeks, and every regime the
    strategy will actually meet absent from the sample. "40 sessions of 30m bars" tells a trader
    that; "680 bars" hides it.

    ``limited`` never blocks a run -- the user asked for it and gets it. It exists because a
    number that looks authoritative and is not is the worst thing this engine can produce, and
    nothing else in a result distinguishes forty sessions from four years.

    Attributes:
        interval: the bar width, as the YAML token.
        sessions: distinct trading sessions spanned. Equals the bar count on a daily run.
        bars: bars in the history.
        limited: whether the history is below the threshold for this kind of run.
        note: what is thin, why it matters, and the way out. ``None`` when not limited.
    """

    interval: str
    sessions: int
    bars: int
    limited: bool
    note: str | None = None


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
    avg_holding_bars: float
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

#: Conventional bar for calling a deflated Sharpe significant.
SIGNIFICANCE = 0.95

#: Losing more than this share of the objective to a single 10% nudge marks a result unstable.
INSTABILITY_THRESHOLD = 0.5

#: Score of a candidate that failed or was infeasible.
INFEASIBLE = math.inf


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

    ``first_bar`` and ``last_bar`` stay dates on an intraday run, where the bars themselves carry
    a time. Deliberate: this record exists to identify *which prices*, and ``frame_digest`` is
    what actually answers that -- the dates are a human-readable label on it. The timestamps a
    reader needs bar by bar are on the trades and the series, at full precision.
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
class BacktestResult:
    """Everything one backtest produced.

    A strategy has exactly one entry rule and one exit rule, so this is one simulation and one
    set of numbers. The engine previously crossed E entry variants with X exit variants and
    reported the best of E*X on the same data -- a selection step that inflated the winner and
    that nothing downstream corrected for. Removing it removes the inflation at the source
    rather than measuring it afterwards; see spec section 7.1.
    """

    strategy_name: str
    ticker: str
    vintage: DataVintage
    metrics: Metrics
    trades: tuple[Trade, ...]
    entry_defined_pct: float
    exit_defined_pct: float | None
    active_stop: str | None
    shadowed_stops: tuple[str, ...]
    benchmark: BenchmarkComparison
    risk_free_rate: float
    warmup_bars: int

    #: Per-bar chart series, present only when the caller asked for them. Captured while the
    #: run executed, never recomputed -- see :mod:`cracktrade.backtest.series`.
    series: RunSeries | None = None

    #: Trades that were still open when their session ended, on an intraday run. Always 0 on a
    #: daily one, where holding overnight is the point. Zero is the healthy intraday value; a
    #: non-zero count means the venue closed earlier than the learned time -- a half-day, an
    #: early close, or the first session of the history -- and the position genuinely carried.
    #: Reported rather than suppressed: an engine that promises "never overnight" and quietly
    #: does it anyway is worse than one that says when it happened (spec section 7.6).
    overnight_carries: int = 0

    #: How much history this result rests on, and whether that is enough to mean anything.
    #: Never blocks a run -- the user asked for it and gets it -- but a result computed from
    #: forty Xetra sessions must not look like one computed from four years, and no other field
    #: here distinguishes them (spec section 12.11).
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    history: HistoryScope | None = None


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

    #: Buying and holding the ticker across the *test* window, on the same bars and under the
    #: same cost model. Without it the report answers "did the search improve the strategy"
    #: -- which ``baseline_test_metrics`` already answers -- and never "is the result worth
    #: owning". An optimized 13% on a ticker that returned 40% reads as a success until this
    #: is beside it (spec section 9.5, audit finding B1).
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    benchmark: BenchmarkComparison | None = None

    #: Closed trades a candidate had to produce on the *train* window to be scored at all
    #: (spec section 9.3). Recorded because ``infeasible`` is uninterpretable without it: a run
    #: that rejected most of its candidates was either constrained or badly configured, and the
    #: number that decided which is not derivable from anything else in this result. Resolved
    #: per run from the configured floor and the length of the train window, so it is not a
    #: constant a reader could look up.
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    min_trades_required: int | None = None

    #: Test-window chart series, present only when the caller asked for them.
    series: RunSeries | None = None

    #: How much history this result rests on, and whether that is enough to mean anything.
    #: Never blocks a run -- the user asked for it and gets it -- but a result computed from
    #: forty Xetra sessions must not look like one computed from four years, and no other field
    #: here distinguishes them (spec section 12.11).
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    history: HistoryScope | None = None

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


@dataclass(frozen=True, slots=True)
class FoldResult:
    """One walk-forward fold, optimized and evaluated independently."""

    index: int
    train_bars: int
    test_bars: int
    first_test_bar: date
    last_test_bar: date
    metrics: Metrics
    train_metrics: Metrics
    parameters: dict[str, float]

    #: The fold's own out-of-sample trades. Every fold re-optimizes, so these belong to
    #: :attr:`parameters` and to no other fold's vector: they can be read per fold and must
    #: never be pooled across folds into one distribution (spec section 12.9).
    trades: tuple[Trade, ...] = ()

    @property
    def was_profitable(self) -> bool:
        """Whether this fold made money out of sample."""
        return self.metrics.total_return_pct > 0


@dataclass(frozen=True, slots=True)
class Check:
    """One robustness check, with the numbers behind it and what it means.

    The verdict is a conjunction of these (spec section 12), and this is the only place that
    conjunction is expressed. Interfaces render checks; they never recompute pass or fail from
    the underlying statistics, because a second implementation of the verdict is a second
    verdict, and eventually the two disagree in public.

    Attributes:
        name: stable identifier, safe to branch on. Never shown to a user.
        label: the check's name in words.
        passed: whether the strategy cleared this bar.
        plain: what the check asks, in one sentence, for a reader who does not know the
            statistic. Part of the output rather than documentation: a failure nobody
            understands is a failure nobody acts on.
        stat: the numbers, formatted. Shown whether the check passed or failed.
        detail: one sentence stating the outcome. Failed checks contribute theirs to
            :attr:`ValidationReport.failures`, which is what the verdict banner lists.
    """

    name: str
    label: str
    passed: bool
    plain: str
    stat: str
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """A walk-forward run and the statistics that judge it.

    The point of this object is that no single number in it is the answer. A strategy is
    credible when it was consistently profitable across folds, its Sharpe survives deflation for
    the number of trials that produced it, its selection procedure beats choosing at random, and
    its optimum is a plateau rather than a spike. Passing one of those and failing three is not a
    partial success.
    """

    strategy_name: str
    ticker: str
    objective: str
    scheme: str
    folds: tuple[FoldResult, ...]
    benchmark: Metrics
    optimized_yaml: str
    deflated: DeflatedSharpe
    overfitting: OverfittingProbability
    stability: StabilityReport
    costs: CostSensitivity
    mean_return_interval: Interval
    total_return_interval: Interval
    trials: int
    seed: int
    elapsed_seconds: float

    #: One entry per fold, in fold order, when the caller asked for series. There is
    #: deliberately no combined curve: each fold re-optimizes, so the folds are different
    #: strategies and splicing their equity curves would draw one that was never traded.
    fold_series: tuple[RunSeries, ...] = ()

    #: How much history this result rests on, and whether that is enough to mean anything.
    #: Never blocks a run -- the user asked for it and gets it -- but a result computed from
    #: forty Xetra sessions must not look like one computed from four years, and no other field
    #: here distinguishes them (spec section 12.11).
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    history: HistoryScope | None = None

    @property
    def combined_return_pct(self) -> float:
        """Out-of-sample return compounded across every fold."""
        compounded = 1.0
        for fold in self.folds:
            compounded *= 1.0 + fold.metrics.total_return_pct / 100.0
        return 100.0 * (compounded - 1.0)

    @property
    def beats_buy_and_hold(self) -> bool:
        """Whether the strategy outperformed simply holding the ticker out of sample."""
        return self.combined_return_pct > self.benchmark.total_return_pct

    @property
    def returns(self) -> tuple[float, ...]:
        """Each fold's out-of-sample total return."""
        return tuple(fold.metrics.total_return_pct for fold in self.folds)

    @property
    def profitable_folds(self) -> int:
        """How many folds made money out of sample."""
        return sum(1 for fold in self.folds if fold.was_profitable)

    @property
    def fold_win_rate(self) -> float:
        """Share of folds that made money, in ``[0, 1]``.

        The single most informative number here. A strategy positive in six of six folds and one
        whose entire edge sits in fold three are different objects, and an average cannot tell
        them apart.
        """
        return self.profitable_folds / len(self.folds) if self.folds else 0.0

    @property
    def median_return_pct(self) -> float:
        """Median out-of-sample fold return. Robust to a single spectacular fold."""
        return _median(self.returns)

    @property
    def return_iqr_pct(self) -> float:
        """Interquartile spread of fold returns.

        How much the answer depends on *when* you happened to run it.
        """
        if len(self.returns) < 4:
            return 0.0
        ordered = sorted(self.returns)
        half = len(ordered) // 2
        return _median(tuple(ordered[-half:])) - _median(tuple(ordered[:half]))

    @property
    def total_trades(self) -> int:
        """Closed out-of-sample trades across every fold."""
        return sum(fold.metrics.total_trades for fold in self.folds)

    @property
    def checks(self) -> tuple[Check, ...]:
        """Every robustness check, passed or failed, in the reporting order of spec 12.8.

        The single definition of the verdict. :attr:`failures` and :attr:`is_credible` are both
        derived from this, so there is exactly one place where "did this strategy hold up" is
        decided, and every interface reports the same answer.
        """
        folds = len(self.folds)
        return (
            Check(
                name="benchmark",
                label="Benchmark",
                passed=self.beats_buy_and_hold,
                plain=(
                    "Combined out-of-sample return against simply owning the ticker over the "
                    "same window."
                ),
                stat=(
                    f"{self.combined_return_pct:+.1f}% against "
                    f"{self.benchmark.total_return_pct:+.1f}%"
                ),
                detail=(
                    f"returned {self.combined_return_pct:+.1f}% out of sample against "
                    f"{self.benchmark.total_return_pct:+.1f}% for buy-and-hold"
                ),
            ),
            Check(
                name="fold_results",
                label="Fold results",
                passed=self.fold_win_rate >= 0.5,
                plain=(
                    f"Ran the whole exercise {folds} times on different slices of history. "
                    "Each slice re-optimized from scratch and was judged on data that slice "
                    "never saw."
                ),
                stat=f"profitable in {self.profitable_folds} of {folds} folds",
                detail=f"profitable in only {self.profitable_folds} of {folds} folds",
            ),
            Check(
                name="deflated_sharpe",
                label="Deflated Sharpe",
                passed=self.deflated.is_significant,
                plain=(
                    f"A search of {self.trials} trials produces a good-looking Sharpe from no "
                    "edge at all. This asks whether the observed one beats that luck."
                ),
                stat=f"P={self.deflated.probability:.2f}, bar is {SIGNIFICANCE:.2f}",
                detail=(
                    f"deflated Sharpe P={self.deflated.probability:.2f}, below the "
                    f"{SIGNIFICANCE:.2f} bar for {self.trials} trials"
                ),
            ),
            Check(
                name="overfitting",
                label="Probability of backtest overfitting",
                passed=self.overfitting.is_acceptable,
                plain=(
                    "Above 0.5 the way this result was selected is worse than choosing a "
                    "configuration at random."
                ),
                stat=_overfitting_stat(self.overfitting),
                detail=(
                    f"probability of backtest overfitting {self.overfitting.probability:.2f}, "
                    f"so selection is no better than choosing at random"
                    if self.overfitting.is_computed
                    else (
                        "the probability of backtest overfitting could not be computed: CSCV "
                        "needs at least four folds to split them into halves, and this run has "
                        "fewer. Re-run with more folds rather than reading this as a pass"
                    )
                ),
            ),
            Check(
                name="stability",
                label="Parameter stability",
                passed=self.stability.is_stable,
                plain=(
                    "The winning parameters nudged by 10% and 20%. A real edge sits on a "
                    "plateau; a curve fit sits on a needle."
                ),
                stat=(
                    f"worst 10% nudge costs "
                    f"{100 * self.stability.worst_small_degradation:.0f}% of the objective"
                ),
                detail=(
                    f"a 10% parameter nudge destroys "
                    f"{100 * self.stability.worst_small_degradation:.0f}% of the objective"
                ),
            ),
            Check(
                name="costs",
                label="Cost sensitivity",
                passed=self.costs.survives_double_costs,
                plain=(
                    "The headline recomputed at twice and three times the configured "
                    "commission and slippage. An edge that dies when costs double belongs to "
                    "the broker."
                ),
                stat=self._cost_stat(),
                detail="unprofitable at twice the configured slippage",
            ),
            Check(
                name="intervals",
                label="Confidence intervals",
                passed=self.mean_return_interval.excludes_zero,
                plain=(
                    "Bootstrap intervals for the fold returns. An interval that straddles zero "
                    "is not distinguishable from luck."
                ),
                stat=(
                    f"mean interval {self.mean_return_interval.low:+.1f}% … "
                    f"{self.mean_return_interval.high:+.1f}%"
                ),
                detail=(
                    f"the 95% interval on mean fold return, "
                    f"{self.mean_return_interval.low:+.1f}% to "
                    f"{self.mean_return_interval.high:+.1f}%, straddles zero"
                ),
            ),
            Check(
                name="trade_count",
                label="Out-of-sample trades",
                passed=self.total_trades >= MIN_TRADES_TO_JUDGE,
                plain=(
                    f"Below {MIN_TRADES_TO_JUDGE} closed trades no figure on this screen means "
                    "anything."
                ),
                stat=f"{self.total_trades} of {MIN_TRADES_TO_JUDGE} needed",
                detail=f"only {self.total_trades} out-of-sample trades in total",
            ),
        )

    def _cost_stat(self) -> str:
        multiple = self.costs.break_even_multiple
        if multiple is None:
            return "still profitable at every tested multiple"
        return f"break-even at {multiple:.1f}x costs"

    @property
    def failures(self) -> tuple[str, ...]:
        """Every robustness check the strategy did not pass, in plain words."""
        return tuple(check.detail for check in self.checks if not check.passed)

    @property
    def is_credible(self) -> bool:
        """Whether every robustness check passed.

        Deliberately strict, and deliberately not a score. A number that is 80% trustworthy is
        not something to put money behind, and averaging the checks would let a strong headline
        return paper over a failed overfitting test.
        """
        return all(check.passed for check in self.checks)


def _overfitting_stat(overfitting: OverfittingProbability) -> str:
    """The PBO line, or a statement that it could not be computed.

    Never ``PBO 0.00`` for a statistic that was never calculated: zero is the best possible
    value, and printing it beside a green tick is precisely the authoritative-looking wrong
    number this engine exists to avoid.
    """
    if not overfitting.is_computed:
        return "not computed - too few slices"
    return f"PBO {overfitting.probability:.2f}"


def _median(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    """The probability that a strategy's true Sharpe ratio exceeds zero.

    Attributes:
        observed: the per-period Sharpe actually measured.
        threshold: the Sharpe a *lucky* search would be expected to produce from no edge at all,
            given the number of trials. This is the bar the observed value has to clear.
        probability: ``P(true Sharpe > 0)`` after deflating for trial count, sample length,
            skew and kurtosis. Below roughly 0.95 the result is not distinguishable from noise.
        trials: how many configurations were scored.
        observations: bars the Sharpe was measured over.
        variance_estimated: whether the cross-trial variance had to be approximated rather than
            observed. A parallel search does not return its per-trial scores.
    """

    observed: float
    threshold: float
    probability: float
    trials: int
    observations: int
    variance_estimated: bool

    @property
    def is_significant(self) -> bool:
        """Whether the result clears the conventional 95% bar."""
        return self.probability >= SIGNIFICANCE

    @property
    def beats_the_lucky_threshold(self) -> bool:
        """Whether the observed Sharpe even exceeds what luck alone would produce."""
        return self.observed > self.threshold


@dataclass(frozen=True, slots=True)
class OverfittingProbability:
    """How often the in-sample best configuration underperforms out of sample.

    Attributes:
        probability: PBO. Above 0.5 the selection procedure is *worse than choosing at random*,
            and the result must not be presented as a recommendation.
        combinations: how many train/test partitions were evaluated.
        median_logit: median of the logit-transformed out-of-sample ranks. Negative means the
            in-sample winner typically lands below the median out of sample.
    """

    probability: float
    combinations: int
    median_logit: float

    @property
    def is_computed(self) -> bool:
        """Whether CSCV had enough slices and configurations to produce anything at all.

        Below four slices there is no way to split them into two halves, so the procedure
        yields no partitions and :attr:`probability` is a placeholder zero rather than a
        measurement.
        """
        return self.combinations > 0

    @property
    def is_acceptable(self) -> bool:
        """Whether the selection procedure beats picking at random.

        An *uncomputed* PBO is not acceptable. The placeholder is zero, which compares below
        the 0.5 bar and would otherwise report a check that never ran as one that passed --
        a three-fold walk-forward or a two-segment evolution would print a green tick for a
        statistic derived from no partitions at all. Failing closed is the only honest
        direction: the report says the check could not be computed, and the verdict does not
        pretend otherwise.
        """
        return self.is_computed and self.probability < 0.5


@dataclass(frozen=True, slots=True)
class Interval:
    """A bootstrap confidence interval."""

    point: float
    low: float
    high: float
    confidence: float

    @property
    def excludes_zero(self) -> bool:
        """Whether the whole interval sits on one side of zero."""
        return (self.low > 0) or (self.high < 0)


@dataclass(frozen=True, slots=True)
class StabilityPoint:
    """One perturbed parameter and what it did to the objective."""

    path: str
    multiplier: float
    value: float
    score: float
    degradation: float

    @property
    def failed(self) -> bool:
        """Whether the perturbed configuration could not be scored at all."""
        return self.score == INFEASIBLE


@dataclass(frozen=True, slots=True)
class StabilityReport:
    """The neighbourhood around an optimum."""

    baseline_score: float
    points: tuple[StabilityPoint, ...]

    @property
    def worst_degradation(self) -> float:
        """The largest share of the objective lost to any single nudge."""
        return max((point.degradation for point in self.points), default=0.0)

    @property
    def worst_small_degradation(self) -> float:
        """The largest loss caused by a *small* (10%) nudge.

        Judged separately: a 20% move is a genuinely different strategy, but a 10% move should
        not be, and a result that cannot survive one is fitted to noise.
        """
        return max(
            (point.degradation for point in self.points if abs(point.multiplier) <= 0.1),
            default=0.0,
        )

    @property
    def is_stable(self) -> bool:
        """Whether the optimum survives its own neighbourhood."""
        return self.worst_small_degradation < INSTABILITY_THRESHOLD

    @property
    def fragile_parameters(self) -> tuple[str, ...]:
        """Parameters whose small perturbation destroys most of the objective."""
        return tuple(
            sorted(
                {
                    point.path
                    for point in self.points
                    if abs(point.multiplier) <= 0.1 and point.degradation >= INSTABILITY_THRESHOLD
                }
            )
        )


@dataclass(frozen=True, slots=True)
class CostScenario:
    """The headline result at one cost level."""

    multiple: float
    slippage_pct: float
    commission_pct: float
    metrics: Metrics


@dataclass(frozen=True, slots=True)
class CostSensitivity:
    """How the result behaves as friction rises."""

    scenarios: tuple[CostScenario, ...]

    @property
    def baseline(self) -> CostScenario:
        """The configured cost level."""
        return self.scenarios[0]

    @property
    def survives_double_costs(self) -> bool:
        """Whether the strategy is still profitable at twice the configured slippage."""
        doubled = self._at(2.0)
        return doubled is not None and doubled.metrics.total_return_pct > 0

    @property
    def break_even_multiple(self) -> float | None:
        """The lowest tested multiple at which the strategy stops making money."""
        for scenario in self.scenarios:
            if scenario.metrics.total_return_pct <= 0:
                return scenario.multiple
        return None

    def _at(self, multiple: float) -> CostScenario | None:
        return next(
            (scenario for scenario in self.scenarios if scenario.multiple == multiple), None
        )


@dataclass(frozen=True, slots=True)
class SegmentResult:
    """The winning strategy measured on one segment of the evolution region.

    In-sample by construction: these are the windows the search selected on. They are reported
    because the spread across them is what the fitness function was actually optimising, and a
    winner whose median segment carried it while two others lost money is a different object
    from one that was steady -- but no number here is evidence about the future, and the report
    labels them accordingly.
    """

    index: int
    first_bar: date
    last_bar: date
    bars: int
    metrics: Metrics

    @property
    def was_profitable(self) -> bool:
        """Whether the winner made money on this segment."""
        return self.metrics.total_return_pct > 0


@dataclass(frozen=True, slots=True)
class EvolutionResult:
    """A strategy composed by evolution, and the evidence for and against it.

    The headline is :attr:`holdout_metrics`, computed once on a stretch of history no genome was
    ever scored on. Everything else in this object exists to stop that headline being read on
    its own: a search that composed structure as well as parameters considered far more distinct
    strategies than a parameter search does, and the maximum of a large enough number of draws
    looks impressive whether or not anything is there.
    """

    strategy_name: str
    ticker: str
    objective: str
    #: The blocks the winner was assembled from, in words. The YAML says what the strategy is;
    #: this says what it was composed of, which is not recoverable from the YAML afterwards.
    composition: str
    blocks: tuple[str, ...]
    strategy_yaml: str
    holdout_metrics: Metrics
    benchmark: BenchmarkComparison
    segments: tuple[SegmentResult, ...]
    trades: tuple[Trade, ...]
    deflated: DeflatedSharpe
    overfitting: OverfittingProbability
    stability: StabilityReport
    costs: CostSensitivity
    mean_return_interval: Interval
    total_return_interval: Interval
    population: int
    generations: int
    #: Genomes handed to the fitness function, repeats included.
    genomes_evaluated: int
    #: Distinct rendered configurations among them. This is the trial count the deflated Sharpe
    #: uses; the two differ because elites survive generations and crossover rediscovers
    #: configurations, and counting those repeats as separate looks would understate the
    #: deflation.
    distinct_configurations: int
    #: Genomes that could not be rendered or simulated. Any value above zero is a defect in the
    #: block library rather than a fact about the market -- see
    #: :meth:`cracktrade.evolution.protocol.Fitness._record_failure`.
    failed_candidates: int
    most_common_failure: str | None
    min_trades_required: int
    evolution_bars: int
    holdout_bars: int
    seed: int
    elapsed_seconds: float

    #: Best fitness after each generation, generation zero first. A trace that flattens early
    #: says the population converged; one still falling at the end says the budget, not the
    #: library, was the binding constraint.
    best_score_by_generation: tuple[float, ...] = ()

    #: Holdout chart series, present only when the caller asked for them.
    series: RunSeries | None = None

    #: How much history this result rests on, and whether that is enough to mean anything.
    #: Never blocks a run -- the user asked for it and gets it -- but a result computed from
    #: forty Xetra sessions must not look like one computed from four years, and no other field
    #: here distinguishes them (spec section 12.11).
    #:
    #: Optional only so that results serialised before this field existed still load. A run
    #: executed today always has one.
    history: HistoryScope | None = None

    @property
    def profitable_segments(self) -> int:
        """How many evolution segments the winner made money on. In-sample."""
        return sum(1 for segment in self.segments if segment.was_profitable)

    @property
    def median_segment_return_pct(self) -> float:
        """Median in-sample segment return, which is close to what fitness selected on."""
        return _median(tuple(segment.metrics.total_return_pct for segment in self.segments))

    @property
    def overfitting_gap_pct(self) -> float:
        """How much better the winner looked where it was selected than where it was judged.

        The in-sample side is the *median* segment rather than the best one, so the gap is not
        inflated by the segment that happened to carry the genome through selection.
        """
        median = _median(tuple(segment.metrics.cagr_pct for segment in self.segments))
        return median - self.holdout_metrics.cagr_pct

    @property
    def checks(self) -> tuple[Check, ...]:
        """Every robustness check, passed or failed, in the reporting order of section 12.8.

        Deliberately the same shape as :attr:`ValidationReport.checks`, and deliberately judged
        only on the holdout: a check computed on the evolution segments would be asking the data
        that chose the winner whether the winner was well chosen. The in-sample segments are
        reported next to these, never mixed into them.
        """
        return (
            Check(
                name="benchmark",
                label="Benchmark",
                passed=self.benchmark.beats_buy_and_hold,
                plain=(
                    "Holdout return against simply owning the ticker over the same window. A "
                    "strategy that does not clear this was not worth composing."
                ),
                stat=(
                    f"{self.holdout_metrics.total_return_pct:+.1f}% against "
                    f"{self.benchmark.benchmark.total_return_pct:+.1f}%"
                ),
                detail=(
                    f"returned {self.holdout_metrics.total_return_pct:+.1f}% on the holdout "
                    f"against {self.benchmark.benchmark.total_return_pct:+.1f}% for buy-and-hold"
                ),
            ),
            Check(
                name="deflated_sharpe",
                label="Deflated Sharpe",
                passed=self.deflated.is_significant,
                plain=(
                    f"Evolution scored {self.distinct_configurations} distinct strategies. That "
                    "many attempts produce a good-looking Sharpe from no edge at all; this asks "
                    "whether the observed one beats that luck."
                ),
                stat=f"P={self.deflated.probability:.2f}, bar is {SIGNIFICANCE:.2f}",
                detail=(
                    f"deflated Sharpe P={self.deflated.probability:.2f}, below the "
                    f"{SIGNIFICANCE:.2f} bar for {self.deflated.trials} distinct strategies"
                ),
            ),
            Check(
                name="overfitting",
                label="Probability of backtest overfitting",
                passed=self.overfitting.is_acceptable,
                plain=(
                    "Above 0.5, picking the best of the final population is worse than picking "
                    "one of them at random."
                ),
                stat=_overfitting_stat(self.overfitting),
                detail=(
                    f"probability of backtest overfitting {self.overfitting.probability:.2f}, "
                    f"so selecting the winner was no better than choosing at random"
                    if self.overfitting.is_computed
                    else (
                        "the probability of backtest overfitting could not be computed: CSCV "
                        "needs at least four segments to split them into halves, and this run "
                        "has fewer. Re-run with more segments rather than reading this as a pass"
                    )
                ),
            ),
            Check(
                name="stability",
                label="Parameter stability",
                passed=self.stability.is_stable,
                plain=(
                    "The winner's indicator parameters nudged by 10% and 20%. A real edge sits "
                    "on a plateau; a curve fit sits on a needle."
                ),
                stat=(
                    f"worst 10% nudge costs "
                    f"{100 * self.stability.worst_small_degradation:.0f}% of the objective"
                ),
                detail=(
                    f"a 10% parameter nudge destroys "
                    f"{100 * self.stability.worst_small_degradation:.0f}% of the objective"
                ),
            ),
            Check(
                name="costs",
                label="Cost sensitivity",
                passed=self.costs.survives_double_costs,
                plain=(
                    "The holdout recomputed at twice and three times the configured commission "
                    "and slippage. An edge that dies when costs double belongs to the broker."
                ),
                stat=self._cost_stat(),
                detail="unprofitable at twice the configured slippage",
            ),
            Check(
                name="intervals",
                label="Confidence intervals",
                passed=self.mean_return_interval.excludes_zero,
                plain=(
                    "Bootstrap interval for the holdout's mean daily return. One that straddles "
                    "zero is not distinguishable from luck."
                ),
                stat=(
                    f"mean interval {100 * self.mean_return_interval.low:+.3f}% … "
                    f"{100 * self.mean_return_interval.high:+.3f}%"
                ),
                detail=(
                    f"the 95% interval on mean daily holdout return, "
                    f"{100 * self.mean_return_interval.low:+.3f}% to "
                    f"{100 * self.mean_return_interval.high:+.3f}%, straddles zero"
                ),
            ),
            Check(
                name="trade_count",
                label="Holdout trades",
                passed=self.holdout_metrics.total_trades >= MIN_TRADES_TO_JUDGE,
                plain=(
                    f"Below {MIN_TRADES_TO_JUDGE} closed trades no figure on this screen means "
                    "anything."
                ),
                stat=f"{self.holdout_metrics.total_trades} of {MIN_TRADES_TO_JUDGE} needed",
                detail=f"only {self.holdout_metrics.total_trades} trades on the holdout",
            ),
        )

    def _cost_stat(self) -> str:
        multiple = self.costs.break_even_multiple
        if multiple is None:
            return "still profitable at every tested multiple"
        return f"break-even at {multiple:.1f}x costs"

    @property
    def failures(self) -> tuple[str, ...]:
        """Every robustness check the evolved strategy did not pass, in plain words."""
        return tuple(check.detail for check in self.checks if not check.passed)

    @property
    def is_credible(self) -> bool:
        """Whether every robustness check passed.

        The same strict conjunction :attr:`ValidationReport.is_credible` applies, and it matters
        more here. A parameter search starts from a strategy someone believed in; evolution
        starts from nothing and returns the best of a large number of guesses, so "most of the
        checks passed" is the expected output of a search with no edge at all.
        """
        return all(check.passed for check in self.checks)
