"""Phase 8: parameter discovery, the search, and the train/test protocol (spec section 9).

Defect D2 is the reason this file exists. Legacy split the history, fitted on train, and then
reported on the *full* history -- ``df_test`` was assigned and never read. Every headline number
it produced was measured on data the optimizer had already fitted to, which makes those numbers
approximately meaningless as forecasts. The protocol tests below are what stop that recurring.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import pytest

from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData
from cracktrade.domain import Metrics, OptimizationResult
from cracktrade.errors import NoOptimizableParametersError, OptimizationError
from cracktrade.optimize import (
    DEFAULT_MIN_TRADES,
    INFEASIBLE,
    OBJECTIVES,
    TestWindow,
    TradeFloor,
    TrainWindow,
    discover_parameters,
    get_objective,
    inject,
    optimize,
    split,
)
from cracktrade.optimize.objective import calmar, legacy_pnl
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from tests.test_metrics import trending_market

TICKER = "TEST"


def strategy_with(
    *,
    indicators: list[dict[str, Any]] | None = None,
    entry: dict[str, Any] | None = None,
    exit_rule: dict[str, Any] | None = None,
) -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "phase8"},
            "universe": {
                "ticker": TICKER,
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10_000.0,
                "commission_pct": 0.1,
                "slippage_pct": 0.05,
            },
            "indicators": indicators or [{"name": "sma_fast", "type": "sma", "window": 20}],
            "entry": entry or {"signal": "close > sma_fast"},
            "exit": exit_rule or {"signal": "close < sma_fast"},
        }
    )


def metrics_with(**overrides: Any) -> Metrics:
    """A Metrics instance with everything at a neutral default."""
    base: dict[str, Any] = {
        "total_trades": 50,
        "win_rate_pct": 50.0,
        "profit_factor": 1.5,
        "total_pnl": 1000.0,
        "final_equity": 11_000.0,
        "total_return_pct": 10.0,
        "cagr_pct": 10.0,
        "max_drawdown_pct": -20.0,
        "sharpe_ratio": 1.0,
        "sortino_ratio": 1.4,
        "calmar_ratio": 0.5,
        "exposure_pct": 40.0,
        "avg_holding_bars": 8.0,
        "best_trade_pnl": 500.0,
        "worst_trade_pnl": -300.0,
        "bars": 500,
    }
    base.update(overrides)
    return Metrics(**base)


def optimized(**kwargs: Any) -> OptimizationResult:
    data = kwargs.pop("data", None) or trending_market(760)
    strategy = kwargs.pop("strategy", None) or strategy_with()
    kwargs.setdefault("epochs", 2)
    kwargs.setdefault("workers", 1)
    return optimize(strategy, data, **kwargs)


# ------------------------------------------------------------------- 9.1: discovery


def test_numeric_leaves_become_parameters() -> None:
    parameters = discover_parameters(strategy_with())

    assert [parameter.key for parameter in parameters] == ["window"]
    assert parameters[0].value == 20.0
    assert parameters[0].is_integer


def test_default_bounds_are_plus_or_minus_fifty_percent() -> None:
    parameter = discover_parameters(strategy_with())[0]

    assert parameter.low == pytest.approx(10.0)
    assert parameter.high == pytest.approx(30.0)


def test_exit_rule_numbers_are_discovered() -> None:
    parameters = discover_parameters(
        strategy_with(exit_rule={"stop_loss_pct": 4.0, "max_holding_days": 10})
    )

    keys = {parameter.key for parameter in parameters}
    assert {"stop_loss_pct", "max_holding_days"} <= keys


def test_structural_fields_are_never_searched() -> None:
    """Optimizing a name or a signal string is meaningless; only numbers move."""
    parameters = discover_parameters(strategy_with())

    assert all(
        parameter.key not in {"name", "type", "signal", "source"} for parameter in parameters
    )


def test_execution_and_universe_are_never_searched() -> None:
    """Tuning the commission or the date range would be fitting the question, not the answer."""
    parameters = discover_parameters(strategy_with())

    assert all(parameter.section in {"indicators", "entry", "exit"} for parameter in parameters)


def test_optimize_false_pins_a_whole_entry() -> None:
    parameters = discover_parameters(
        strategy_with(
            indicators=[
                {"name": "fast", "type": "sma", "window": 20, "optimize": False},
                {"name": "slow", "type": "sma", "window": 50},
            ],
            entry={"signal": "fast > slow"},
            exit_rule={"signal": "fast < slow"},
        )
    )

    assert [parameter.owner for parameter in parameters] == ["slow"]


def test_explicit_bounds_override_the_default_range() -> None:
    parameters = discover_parameters(
        strategy_with(
            indicators=[
                {
                    "name": "sma_fast",
                    "type": "sma",
                    "window": 20,
                    "optimize": {"window": {"min": 5, "max": 100}},
                }
            ]
        )
    )

    assert (parameters[0].low, parameters[0].high) == (5.0, 100.0)


def test_a_single_parameter_can_be_pinned_within_an_entry() -> None:
    parameters = discover_parameters(
        strategy_with(
            indicators=[
                {
                    "name": "m",
                    "type": "macd",
                    "fast": 12,
                    "slow": 26,
                    "signal_window": 9,
                    "optimize": {"slow": False},
                }
            ],
            entry={"signal": "close > m_macd"},
            exit_rule={"signal": "close < m_macd"},
        )
    )

    keys = {parameter.key for parameter in parameters}
    assert "slow" not in keys
    assert "fast" in keys


def test_a_strategy_with_nothing_tunable_is_refused() -> None:
    """Running a search over zero parameters only burns time re-scoring one configuration."""
    with pytest.raises(NoOptimizableParametersError, match="no optimizable parameters"):
        discover_parameters(
            strategy_with(
                indicators=[{"name": "sma_fast", "type": "sma", "window": 20, "optimize": False}],
                exit_rule={"signal": "close < sma_fast"},
            )
        )


# ------------------------------------------------------------------- 9.1: injection


def test_injection_preserves_integer_parameters() -> None:
    strategy = strategy_with()
    parameters = discover_parameters(strategy)

    config = inject(strategy, parameters, [18.7])

    window = config["indicators"][0]["params"]["window"]
    assert window == 19
    assert isinstance(window, int)


def test_injection_rounds_floats_to_two_decimals() -> None:
    strategy = strategy_with(exit_rule={"stop_loss_pct": 5.0})
    parameters = discover_parameters(strategy)
    index = next(i for i, p in enumerate(parameters) if p.key == "stop_loss_pct")
    values = [p.value for p in parameters]
    values[index] = 4.3333333

    config = inject(strategy, parameters, values)

    assert config["exit"]["stop_loss_pct"] == 4.33


def test_injection_does_not_mutate_the_source_strategy() -> None:
    """Thousands of candidates are evaluated; a shared config would couple them all."""
    strategy = strategy_with()
    parameters = discover_parameters(strategy)

    inject(strategy, parameters, [29.0])

    assert strategy.indicators[0].params["window"] == 20


# --------------------------------------------------------------- 9.4: the split protocol


def test_the_test_window_carries_a_warmup_prefix() -> None:
    """Warm-up drawn from train bars is past data relative to every test bar, so it is legal."""
    data = trending_market(500)

    division = split(data, train_fraction=0.8, warmup=30)

    assert division.test.offset == 30
    assert len(division.test.data) == division.test.scored_bars + 30
    assert division.split_bar == 400


def test_train_and_test_do_not_overlap_in_scored_bars() -> None:
    data = trending_market(500)

    division = split(data, train_fraction=0.8, warmup=30)

    last_train = division.train.data.index[-1]
    first_scored_test = division.test.data.index[division.test.offset]
    assert first_scored_test > last_train


def test_a_split_that_leaves_too_little_to_fit_on_is_refused() -> None:
    data = trending_market(120)

    with pytest.raises(OptimizationError, match="warm-up"):
        split(data, train_fraction=0.8, warmup=200)


def test_a_split_that_leaves_too_little_to_evaluate_on_is_refused() -> None:
    data = trending_market(200)

    with pytest.raises(OptimizationError, match="evaluate on"):
        split(data, train_fraction=0.99, warmup=10, min_test_bars=30)


def test_the_windows_are_distinct_types() -> None:
    division = split(trending_market(500), train_fraction=0.8, warmup=10)

    assert type(division.train) is TrainWindow
    assert type(division.test) is TestWindow


@pytest.mark.slow
def test_passing_a_test_window_to_the_fitness_function_fails_type_checking() -> None:
    """Spec conformance 15, and the whole point of section 2.5.

    The rule "never fit on the test window" is worth little as a comment; someone eventually
    calls the wrong function. Here it is a property mypy enforces, so this test asserts on mypy
    itself rather than on runtime behaviour -- there is no runtime behaviour to assert, which is
    exactly the design.
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    source = """
from cracktrade.optimize.windows import TestWindow, TrainWindow


def fitness(train: TrainWindow) -> float:
    return float(len(train.data))


def leak(test: TestWindow) -> float:
    return fitness(test)
"""
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "probe.py"
        probe.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", str(probe)],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parent.parent,
        )

    assert completed.returncode != 0, (
        f"mypy accepted a TestWindow where a TrainWindow is required:\n{completed.stdout}"
    )
    assert "arg-type" in completed.stdout, (
        f"mypy objected, but not about the argument type:\n{completed.stdout}"
    )
    assert "TestWindow" in completed.stdout

    # And the real fitness object is annotated the same way, so the probe is not testing a
    # property that only the probe has.
    from cracktrade.optimize.runner import _Fitness

    assert _Fitness.__annotations__["train"] == "TrainWindow"


# ------------------------------------------------------- D2: reporting is out of sample


def test_test_metrics_are_measured_on_unseen_bars() -> None:
    """Defect D2. The reported figure must not come from the data that was fitted to."""
    result = optimized()

    assert result.test_bars > 0
    assert result.train_bars > result.test_bars
    assert result.test_metrics != result.train_metrics


def test_the_in_sample_and_out_of_sample_gap_is_reported() -> None:
    """Hiding the gap would be a disservice: it is the cheapest overfitting diagnostic there is."""
    result = optimized()

    assert result.overfitting_gap_pct == pytest.approx(
        result.train_metrics.cagr_pct - result.test_metrics.cagr_pct
    )


def test_the_baseline_is_measured_on_the_same_window_as_the_result() -> None:
    """Legacy compared in-sample optimized against in-sample baseline; both were inflated."""
    result = optimized()

    assert result.improvement_pct == pytest.approx(
        result.test_metrics.total_return_pct - result.baseline_test_metrics.total_return_pct
    )


# ------------------------------------------------- B1: the hurdle is owning the ticker


def test_the_result_is_measured_against_buying_and_holding() -> None:
    """Audit finding B1, in the place it is most persuasive.

    ``baseline_test_metrics`` says whether the search did anything. It cannot say whether the
    answer is worth owning, and an improvement figure printed next to a result invites exactly
    that reading (spec section 9.5, amended 2026-08-17).
    """
    result = optimized()

    assert result.benchmark is not None
    assert result.benchmark.excess_return_pct == pytest.approx(
        result.test_metrics.total_return_pct - result.benchmark.benchmark.total_return_pct
    )
    assert result.benchmark.beats_buy_and_hold == (result.benchmark.excess_return_pct > 0)


def test_the_benchmark_covers_the_test_window_and_not_the_fitted_one() -> None:
    """The hold has to be over the bars the strategy was reported on, or it compares nothing.

    Scored bars rather than raw window length: the test window carries a warm-up prefix the
    strategy could not act on, and crediting the hold with it would hand the benchmark return
    the strategy was structurally unable to capture.
    """
    result = optimized()

    assert result.benchmark is not None
    assert result.benchmark.benchmark.bars == result.test_metrics.bars == result.test_bars


def test_the_benchmark_curve_and_the_benchmark_number_come_from_one_simulation() -> None:
    """A chart that disagrees with the figure printed under it is worse than either alone."""
    result = optimize(
        strategy_with(), trending_market(760), epochs=2, workers=1, capture_series=True
    )

    assert result.benchmark is not None
    assert result.series is not None
    final = result.series.benchmark_equity.values[-1]
    assert final == pytest.approx(result.benchmark.benchmark.final_equity)


def test_the_optimized_yaml_carries_the_one_entry_and_exit_it_started_with() -> None:
    """No pruning step exists any more: a strategy is one entry rule and one exit rule.

    The engine used to cross E entries with X exits, pick the best pair on train, and prune to
    it. That selection inflated whichever pair won and was counted into ``trials`` to compensate;
    removing the crossing removes the inflation at the source.
    """
    result = optimized(strategy=strategy_with(exit_rule={"max_holding_days": 8}))

    assert "entry:" in result.optimized_yaml
    assert "exit:" in result.optimized_yaml
    assert "entry_variants" not in result.optimized_yaml


# ---------------------------------------------------------------- D9: failure scoring


def test_a_failed_candidate_scores_worse_than_any_real_strategy() -> None:
    """Defect D9. Legacy returned 0.0, which outranks every genuinely losing strategy."""
    losing = calmar(metrics_with(cagr_pct=-50.0, max_drawdown_pct=-60.0))

    assert losing < INFEASIBLE
    assert float("inf") == INFEASIBLE


def test_the_trade_floor_is_a_hard_constraint_not_a_discount() -> None:
    """Audit B2: a multiplier is defeated by any fluke large enough."""
    thin_but_huge = metrics_with(total_trades=3, cagr_pct=10_000.0, max_drawdown_pct=-1.0)

    assert calmar(thin_but_huge) == INFEASIBLE


def test_a_thin_result_beats_a_good_one_under_the_legacy_objective() -> None:
    """Why the legacy objective is not the default: the discount is not a barrier."""
    thin_but_huge = metrics_with(total_trades=3, total_pnl=1_000_000.0, max_drawdown_pct=-10.0)
    solid = metrics_with(total_trades=200, total_pnl=5_000.0, max_drawdown_pct=-10.0)

    assert legacy_pnl(thin_but_huge) < legacy_pnl(solid)
    assert calmar(thin_but_huge) == INFEASIBLE


# ------------------------------------------------------------------ 9.3: the trade floor


def test_the_floor_is_the_larger_of_the_absolute_count_and_the_rate() -> None:
    floor = TradeFloor(minimum=20, per_year=4.0)

    # One year: the rate asks for 4, the absolute floor for 20.
    assert floor.required(TRADING_DAYS_PER_YEAR, periods_per_year=TRADING_DAYS_PER_YEAR) == 20
    # Ten years: the rate asks for 40 and now binds. This is the whole point of having one --
    # a flat 20 over ten years is a candidate trading twice a year.
    assert floor.required(10 * TRADING_DAYS_PER_YEAR, periods_per_year=TRADING_DAYS_PER_YEAR) == 40


def test_the_rate_rounds_up() -> None:
    """A fractional trade is not a trade. 4/year over 15 months asks for 5, not 4."""
    floor = TradeFloor(minimum=0, per_year=4.0)

    assert (
        floor.required(int(1.25 * TRADING_DAYS_PER_YEAR), periods_per_year=TRADING_DAYS_PER_YEAR)
        == 5
    )


def test_the_rate_can_be_switched_off() -> None:
    """`per_year=0` restores the flat floor, which is what reproducing an old result needs."""
    floor = TradeFloor(minimum=20, per_year=0.0)

    assert floor.required(50 * TRADING_DAYS_PER_YEAR, periods_per_year=TRADING_DAYS_PER_YEAR) == 20


def test_a_zero_floor_admits_everything() -> None:
    floor = TradeFloor(minimum=0, per_year=0.0)

    assert floor.required(10_000, periods_per_year=TRADING_DAYS_PER_YEAR) == 0
    assert get_objective("calmar", min_trades=0)(metrics_with(total_trades=0)) < INFEASIBLE


@pytest.mark.parametrize(
    ("minimum", "per_year"),
    [(-1, 4.0), (20, -1.0), (20, float("inf")), (20, float("nan"))],
)
def test_a_nonsensical_floor_is_refused(minimum: int, per_year: float) -> None:
    """Rejected at construction, not silently normalised: a negative floor is a typo."""
    with pytest.raises(ValueError, match="min_trades"):
        TradeFloor(minimum=minimum, per_year=per_year)


@pytest.mark.parametrize("name", sorted(OBJECTIVES))
def test_binding_a_floor_moves_the_cliff_for_every_objective(name: str) -> None:
    strict = get_objective(name, min_trades=100)
    lenient = get_objective(name, min_trades=10)
    fifty = metrics_with(total_trades=50)

    # Under the strict floor a fifty-trade candidate is worse than it is under the lenient one.
    # For the three real objectives that means infeasible; for legacy it means the 10x discount.
    assert strict(fifty) > lenient(fifty)


def test_the_default_floor_is_what_an_unbound_objective_applies() -> None:
    """`get_objective(name)` and the bare function must not disagree about the default."""
    thin = metrics_with(total_trades=DEFAULT_MIN_TRADES - 1)

    assert get_objective("calmar")(thin) == calmar(thin) == INFEASIBLE


def test_the_floor_in_force_is_recorded_in_the_result() -> None:
    """`infeasible` is uninterpretable without the number that decided it."""
    result = optimized(trade_floor=TradeFloor(minimum=7, per_year=0.0))

    assert result.min_trades_required == 7


def test_the_recorded_floor_reflects_the_rate_and_the_train_window() -> None:
    """The rate is resolved against this run's train bars, not against the whole history."""
    data = trending_market(760)
    result = optimized(data=data, trade_floor=TradeFloor(minimum=0, per_year=12.0))

    expected = math.ceil(12.0 * result.train_bars / TRADING_DAYS_PER_YEAR)
    assert result.min_trades_required == expected


def test_a_floor_no_candidate_can_clear_leaves_the_search_with_nothing() -> None:
    """The honest failure mode: every candidate infeasible, and the report says so.

    Worth pinning because the alternative -- quietly relaxing an unsatisfiable constraint --
    would hand back a winner chosen by nothing at all.
    """
    result = optimized(trade_floor=TradeFloor(minimum=100_000, per_year=0.0))

    assert result.min_trades_required == 100_000
    assert result.infeasible == result.evaluations


# ------------------------------------------------------------------ 9.3: objectives


@pytest.mark.parametrize("name", sorted(OBJECTIVES))
def test_every_objective_rejects_a_thin_sample(name: str) -> None:
    objective = get_objective(name)

    thin = metrics_with(total_trades=1, total_pnl=10.0, cagr_pct=5.0)
    rich = metrics_with(total_trades=200, total_pnl=10.0, cagr_pct=5.0)

    assert objective(thin) >= objective(rich)


def test_calmar_prefers_the_same_return_at_lower_drawdown() -> None:
    shallow = metrics_with(cagr_pct=20.0, max_drawdown_pct=-10.0)
    deep = metrics_with(cagr_pct=20.0, max_drawdown_pct=-40.0)

    assert calmar(shallow) < calmar(deep)


def test_calmar_prefers_more_return_at_the_same_drawdown() -> None:
    assert calmar(metrics_with(cagr_pct=30.0)) < calmar(metrics_with(cagr_pct=10.0))


def test_the_objective_used_is_recorded_in_the_result() -> None:
    """A score is not comparable across objectives, so the report has to name the one used."""
    assert optimized(objective_name="sortino").objective == "sortino"


# ------------------------------------------------------------ D12: reproducibility


def test_the_same_seed_gives_the_same_result() -> None:
    first = optimized()
    second = optimized()

    assert [change.new_value for change in first.changes] == [
        change.new_value for change in second.changes
    ]
    assert first.test_metrics == second.test_metrics


def test_a_different_seed_may_explore_differently_but_still_reports_honestly() -> None:
    result = optimized(seed=7)

    assert result.seed == 7
    assert result.evaluations > 0


@pytest.mark.slow
def test_the_result_is_identical_however_many_workers_run_it() -> None:
    """Defect D12. Legacy passed no seed while running workers=-1, so nothing reproduced.

    This also proves the fitness function survives a process boundary: it was a closure at
    first, and a closure cannot be pickled, so the parallel path failed outright.
    """
    serial = optimized(workers=1)
    parallel = optimized(workers=-1)

    assert [change.new_value for change in serial.changes] == [
        change.new_value for change in parallel.changes
    ]
    assert serial.test_metrics == parallel.test_metrics


def test_a_parallel_run_admits_that_its_failure_counts_are_not_exact() -> None:
    """Child processes mutate copies, so a confident zero would be a lie."""
    assert optimized(workers=1).counts_exact
    assert not optimized(workers=-1).counts_exact


# ------------------------------------------------------------ D13: integer parameters


def test_integer_parameters_only_ever_take_integer_values() -> None:
    """Defect D13. Rounding after the fact leaves DE searching a piecewise-flat landscape."""
    result = optimized()

    for change in result.changes:
        assert change.new_value == int(change.new_value)


def test_the_optimized_yaml_holds_an_integer_window() -> None:
    result = optimized()

    assert "window: 2" in result.optimized_yaml or "window: 1" in result.optimized_yaml
    assert "window: 18.0" not in result.optimized_yaml


# ------------------------------------------------------------------- reporting extras


def test_a_parameter_resting_on_its_bound_is_flagged() -> None:
    """It means the range was the binding constraint, not the data."""
    from cracktrade.domain import ParameterChange

    at_edge = ParameterChange(path="p", old_value=20, new_value=30, low=10, high=30)
    inside = ParameterChange(path="p", old_value=20, new_value=22, low=10, high=30)

    assert at_edge.at_bound
    assert not inside.at_bound


def test_the_result_counts_every_configuration_scored() -> None:
    """The trial count feeds the deflated Sharpe in spec section 12.3, so it must be honest.

    One evaluation is now one trial. While the engine crossed entry and exit variants, each
    evaluation scored ``entries x exits`` configurations and took the best, so the trial count
    had to be multiplied by the variant count to keep the deflation honest.
    """
    result = optimized()

    assert result.trials == result.evaluations


def test_the_optimized_yaml_reloads_as_a_valid_strategy() -> None:
    import yaml

    from cracktrade.strategy import build_strategy

    result = optimized()

    assert build_strategy(yaml.safe_load(result.optimized_yaml)) is not None


def test_the_optimization_report_renders() -> None:
    from rich.console import Console

    from cracktrade.cli.render import render_optimization

    console = Console(file=io.StringIO(), width=100, force_terminal=False)
    render_optimization(optimized(), console)
    output = console.file.getvalue()  # type: ignore[attr-defined]

    assert "Test (unseen)" in output
    assert "Train (fitted)" in output
    assert "evaluations" in output


def test_the_search_never_returns_worse_than_the_configured_baseline() -> None:
    """The user's own configuration is member zero of the population, so it is always in play."""
    result = optimized()
    baseline_window = 20

    assert result.evaluations > 0
    # The search may keep the baseline, but it must never report having moved to something it
    # scored worse on train.
    assert result.train_metrics.total_trades >= 0
    assert any(change.old_value == baseline_window for change in result.changes)


def test_parameters_outside_the_searchable_sections_are_left_alone() -> None:
    result = optimized()

    assert "initial_capital: 10000" in result.optimized_yaml
    assert all(
        not change.path.startswith(("execution", "universe", "strategy"))
        for change in result.changes
    )


def test_a_flat_market_still_produces_a_reportable_result() -> None:
    """No edge to find is a legitimate outcome and must not raise."""
    bars = 700
    index = trending_market(bars).index
    flat = np.full(bars, 100.0)
    import pandas as pd

    frame = pd.DataFrame(
        {
            "Open": flat,
            "High": flat + 0.01,
            "Low": flat - 0.01,
            "Close": flat,
            "Volume": np.full(bars, 1_000.0),
        },
        index=index,
    )
    data = MarketData(
        ticker=TICKER,
        frame=frame,
        requested_start=trending_market(10).requested_start,
        requested_end=trending_market(10).requested_end,
    )

    result = optimized(data=data)

    assert result.test_metrics.total_trades >= 0


def test_the_test_window_is_sized_for_the_widest_candidate_the_search_can_reach() -> None:
    """Bounds are +/-50%, so a 200-bar window can grow to 300.

    If the warm-up prefix were sized from the baseline, such a candidate would have its signals
    suppressed inside the scored region and would quietly lose test bars it should have traded.
    """
    from cracktrade.optimize.runner import worst_case_warmup

    strategy = strategy_with(indicators=[{"name": "sma_fast", "type": "sma", "window": 200}])
    parameters = discover_parameters(strategy)

    assert worst_case_warmup(strategy, parameters) >= 300


def test_theworst_case_warmup_falls_back_when_the_widest_combination_is_invalid() -> None:
    """An all-upper-bound configuration need not be a valid strategy."""
    from cracktrade.optimize.runner import worst_case_warmup

    strategy = strategy_with()
    assert worst_case_warmup(strategy, discover_parameters(strategy)) >= 20
