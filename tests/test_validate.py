"""Phase 8.5: walk-forward folds and the statistics that judge them (spec section 12).

Most of these assert *statistical* properties rather than exact numbers, because that is what
the code is for. The load-bearing one is
:func:`test_the_best_of_many_noise_strategies_is_not_significant`: take the best of two thousand
pure-noise return series -- which is precisely what an optimizer does -- and the deflated Sharpe
must refuse to call it significant. If that ever passes, every other check here is decoration.
"""

from __future__ import annotations

import io
import math
from dataclasses import replace
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from cracktrade.domain import OverfittingProbability, ValidationReport
from cracktrade.errors import OptimizationError
from cracktrade.optimize import discover_parameters
from cracktrade.serialize import to_dict
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from cracktrade.validate import (
    FoldScheme,
    block_bootstrap_interval,
    deflated_sharpe,
    expected_maximum_sharpe,
    per_period_sharpe,
    probability_of_backtest_overfitting,
    walk_forward,
    walk_forward_splits,
)
from tests.test_metrics import trending_market
from tests.test_optimize import strategy_with


def noise(bars: int = 500, seed: int = 0, drift: float = 0.0) -> npt.NDArray[np.float64]:
    return np.random.default_rng(seed).normal(drift, 0.01, bars)


# ------------------------------------------------------- 12.3: the deflated Sharpe ratio


def test_the_best_of_many_noise_strategies_is_not_significant() -> None:
    """The whole reason this phase exists.

    Two thousand pure-noise return series, none with any edge. Pick the best one -- which is
    exactly what a parameter search does -- and its Sharpe looks respectable. Deflated for the
    two thousand trials that produced it, it must not clear the bar.
    """
    trials = 2000
    rng = np.random.default_rng(0)
    paths = rng.normal(0.0, 0.01, size=(trials, 500))
    sharpes = np.array([per_period_sharpe(path) for path in paths])
    best = paths[int(np.argmax(sharpes))]

    result = deflated_sharpe(best, trials=trials, trial_sharpes=sharpes)

    assert result.observed > 0, "the winner looks good before deflation"
    assert not result.is_significant
    assert not result.beats_the_lucky_threshold


def test_a_genuine_edge_found_in_few_trials_is_significant() -> None:
    strong = np.random.default_rng(1).normal(0.0015, 0.008, 1500)

    assert deflated_sharpe(strong, trials=5).is_significant


def test_more_trials_never_raise_the_probability() -> None:
    """Searching harder cannot make a result more trustworthy."""
    returns = np.random.default_rng(2).normal(0.0006, 0.01, 800)

    probabilities = [deflated_sharpe(returns, trials=n).probability for n in (1, 10, 500, 50_000)]

    assert probabilities == sorted(probabilities, reverse=True)


def test_the_luck_threshold_grows_with_the_number_of_trials() -> None:
    assert expected_maximum_sharpe(10, 1.0) < expected_maximum_sharpe(1000, 1.0)


def test_the_luck_threshold_tracks_the_expected_maximum_of_n_normals() -> None:
    """It should sit just below sqrt(2 ln N), which the expected maximum approaches from below."""
    for trials in (100, 1000, 5000):
        asymptote = math.sqrt(2 * math.log(trials))
        threshold = expected_maximum_sharpe(trials, 1.0)
        assert 0.7 * asymptote < threshold < asymptote


def test_a_single_trial_needs_no_deflation() -> None:
    assert expected_maximum_sharpe(1, 1.0) == 0.0


def test_negative_skew_lowers_the_probability() -> None:
    """A strategy with rare large losses is less trustworthy at the same Sharpe."""
    rng = np.random.default_rng(3)
    symmetric = rng.normal(0.0008, 0.01, 1000)
    skewed = symmetric.copy()
    skewed[::100] -= 0.05  # occasional large losses

    plain = deflated_sharpe(symmetric, trials=100)
    tailed = deflated_sharpe(skewed, trials=100)

    assert tailed.probability <= plain.probability


def test_a_flat_series_reports_no_sharpe() -> None:
    assert per_period_sharpe(np.zeros(100)) == 0.0


def test_too_few_observations_is_reported_rather_than_computed() -> None:
    result = deflated_sharpe(np.array([0.01, 0.02]), trials=10)

    assert result.probability == 0.0
    assert result.variance_estimated


def test_annualised_and_per_period_sharpes_differ_by_the_calendar() -> None:
    """The units trap that made every strategy fail deflation until it was found.

    ``Metrics.sharpe_ratio`` is annualised; the deflation formula wants per-period. Mixing them
    inflates the trial variance by 252 and the luck threshold by about 16.
    """
    returns = np.random.default_rng(4).normal(0.0008, 0.01, 1000)

    per_period = per_period_sharpe(returns)
    annualised = per_period * math.sqrt(TRADING_DAYS_PER_YEAR)

    assert annualised == pytest.approx(per_period * 15.87, rel=0.01)


# ------------------------------------------------------------------------- 12.4: PBO


def test_pure_noise_gives_a_coin_flip_overfitting_probability() -> None:
    """If selection carried no information, the in-sample winner lands anywhere out of sample."""
    matrix = np.random.default_rng(5).normal(0, 1, size=(8, 40))

    result = probability_of_backtest_overfitting(matrix)

    assert 0.3 < result.probability < 0.7
    assert result.combinations == 70


def test_a_genuinely_dominant_configuration_gives_a_low_overfitting_probability() -> None:
    matrix = np.random.default_rng(6).normal(0, 1, size=(8, 40))
    matrix[:, 7] += 3.0

    result = probability_of_backtest_overfitting(matrix)

    assert result.probability < 0.1
    assert result.is_acceptable


def test_a_matrix_too_small_for_cscv_reports_nothing_rather_than_guessing() -> None:
    result = probability_of_backtest_overfitting(np.zeros((2, 2)))

    assert result.combinations == 0
    assert result.probability == 0.0


def test_an_uncomputed_overfitting_probability_does_not_read_as_a_pass() -> None:
    """The placeholder is zero, which is the *best* possible PBO.

    Below four slices there is no way to split them into halves, so nothing is measured and
    ``probability`` stays at its initial zero -- comfortably under the 0.5 bar. Reported as
    acceptable, a three-fold walk-forward would print a green tick for a statistic that never
    ran. Failing closed is the only honest direction, and the report has to be able to say
    which of the two happened.
    """
    uncomputed = probability_of_backtest_overfitting(np.zeros((3, 5)))

    assert uncomputed.combinations == 0
    assert not uncomputed.is_computed
    assert not uncomputed.is_acceptable

    computed = probability_of_backtest_overfitting(np.random.default_rng(6).normal(size=(8, 40)))
    assert computed.is_computed
    assert computed.is_acceptable == (computed.probability < 0.5)


def test_an_uncomputed_overfitting_check_says_so_rather_than_printing_a_number(
    report: ValidationReport,
) -> None:
    """``PBO 0.00`` beside a failed check would read as a very good result that failed."""
    uncomputed = replace(
        report,
        overfitting=OverfittingProbability(probability=0.0, combinations=0, median_logit=0.0),
    )

    check = next(check for check in uncomputed.checks if check.name == "overfitting")

    assert not check.passed
    assert "0.00" not in check.stat
    assert "not computed" in check.stat
    assert "at least four folds" in check.detail
    assert not uncomputed.is_credible


def test_an_overfitting_probability_above_a_half_is_unacceptable() -> None:
    """Above 0.5 the selection procedure is worse than choosing at random."""
    matrix = np.random.default_rng(7).normal(0, 1, size=(6, 20))
    # Invert the relationship: whatever wins in sample loses out of sample.
    matrix[3:] = -matrix[3:]

    result = probability_of_backtest_overfitting(matrix)

    assert result.probability > 0.5
    assert not result.is_acceptable


# ------------------------------------------------------------------- 12.6: bootstrap


def test_the_bootstrap_interval_brackets_its_point_estimate() -> None:
    interval = block_bootstrap_interval(noise(800, seed=8, drift=0.0008), seed=0)

    assert interval.low < interval.point < interval.high
    assert interval.confidence == 0.95


def test_a_wider_confidence_level_gives_a_wider_interval() -> None:
    returns = noise(600, seed=9, drift=0.0005)

    narrow = block_bootstrap_interval(returns, confidence=0.80, seed=0)
    wide = block_bootstrap_interval(returns, confidence=0.99, seed=0)

    assert (wide.high - wide.low) > (narrow.high - narrow.low)


def test_block_resampling_is_wider_than_shuffling_bar_by_bar() -> None:
    """Blocks preserve serial dependence; single bars destroy it and understate uncertainty."""
    rng = np.random.default_rng(10)
    serial = np.zeros(600)
    for index in range(1, 600):
        # Strongly autocorrelated, as a series of held positions is.
        serial[index] = 0.85 * serial[index - 1] + rng.normal(0, 0.004)

    blocked = block_bootstrap_interval(serial, block_size=40, seed=0)
    single = block_bootstrap_interval(serial, block_size=1, seed=0)

    assert (blocked.high - blocked.low) > (single.high - single.low)


def test_the_bootstrap_is_reproducible() -> None:
    returns = noise(400, seed=11)

    assert block_bootstrap_interval(returns, seed=3) == block_bootstrap_interval(returns, seed=3)


def test_a_degenerate_series_yields_an_empty_interval() -> None:
    interval = block_bootstrap_interval(np.array([0.01]), seed=0)

    assert (interval.low, interval.point, interval.high) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------- 12.1: folds


def test_folds_are_contiguous_and_ordered() -> None:
    splits = walk_forward_splits(trending_market(1200), warmup=20, folds=4, min_test_bars=50)

    assert len(splits) == 4
    for earlier, later in pairwise(splits):
        assert later.train.data.index[-1] > earlier.train.data.index[-1]


def test_each_fold_trains_only_on_bars_before_its_test_window() -> None:
    """The property that makes a fold's number out-of-sample at all."""
    splits = walk_forward_splits(trending_market(1200), warmup=20, folds=4, min_test_bars=50)

    for division in splits:
        last_train = division.train.data.index[-1]
        first_scored = division.test.data.index[division.test.offset]
        assert first_scored > last_train


def test_an_anchored_scheme_grows_the_training_window() -> None:
    splits = walk_forward_splits(
        trending_market(1200), warmup=20, folds=4, scheme=FoldScheme.ANCHORED, min_test_bars=50
    )

    sizes = [len(division.train.data) for division in splits]
    assert sizes == sorted(sizes)
    assert len(set(sizes)) > 1


def test_a_rolling_scheme_keeps_the_training_window_the_same_size() -> None:
    splits = walk_forward_splits(
        trending_market(1200), warmup=20, folds=4, scheme=FoldScheme.ROLLING, min_test_bars=50
    )

    sizes = {len(division.train.data) for division in splits}
    assert len(sizes) == 1


def test_the_last_fold_absorbs_the_remainder_so_no_bars_are_discarded() -> None:
    data = trending_market(1003)

    splits = walk_forward_splits(data, warmup=20, folds=3, min_test_bars=50)

    assert splits[-1].test.data.index[-1] == data.index[-1]


def test_too_many_folds_for_the_history_is_refused() -> None:
    with pytest.raises(OptimizationError, match="per test segment"):
        walk_forward_splits(trending_market(300), warmup=20, folds=20, min_test_bars=30)


def test_a_warmup_longer_than_the_initial_training_window_is_refused() -> None:
    with pytest.raises(OptimizationError, match="warm-up"):
        walk_forward_splits(trending_market(400), warmup=300, folds=2, min_test_bars=30)


# --------------------------------------------------------------- end-to-end walk-forward


@pytest.fixture(scope="module")
def report() -> ValidationReport:
    return walk_forward(
        strategy_with(),
        trending_market(1400),
        folds=4,
        epochs=2,
        seed=0,
        workers=1,
        min_test_bars=60,
    )


def test_a_walk_forward_run_reports_every_fold(report: ValidationReport) -> None:
    assert len(report.folds) == 4
    assert all(fold.test_bars > 0 for fold in report.folds)
    assert [fold.index for fold in report.folds] == [0, 1, 2, 3]


def test_fold_dispersion_is_reported_not_just_an_average(report: ValidationReport) -> None:
    """A strategy positive in every fold and one carried by a single fold must look different."""
    assert len(report.returns) == 4
    assert report.return_iqr_pct >= 0
    assert 0.0 <= report.fold_win_rate <= 1.0


def test_the_report_carries_every_robustness_statistic(report: ValidationReport) -> None:
    assert report.deflated.trials > 0
    assert report.overfitting.combinations > 0
    assert report.stability.points
    assert len(report.costs.scenarios) == 3
    assert report.mean_return_interval.confidence == 0.95


def test_a_random_walk_strategy_is_not_credible(report: ValidationReport) -> None:
    """Synthetic data has no edge to find, so the checks must say so."""
    assert not report.is_credible
    assert report.failures


def test_every_failure_is_stated_in_plain_words(report: ValidationReport) -> None:
    for failure in report.failures:
        assert failure
        assert not failure.startswith("_")


def test_the_trial_count_spans_every_fold(report: ValidationReport) -> None:
    """Deflation must account for the whole search, not one fold's share of it."""
    assert report.trials >= 4


def test_cost_sensitivity_degrades_monotonically(report: ValidationReport) -> None:
    """More friction cannot make a long-only strategy more profitable."""
    returns = [scenario.metrics.total_return_pct for scenario in report.costs.scenarios]

    assert returns == sorted(returns, reverse=True)


def test_the_benchmark_covers_the_same_out_of_sample_span(report: ValidationReport) -> None:
    assert report.benchmark.exposure_pct > 90.0
    assert isinstance(report.beats_buy_and_hold, bool)


# ------------------------------------------------------------- 12.9: per-fold trades


def test_each_fold_reports_the_trades_it_actually_took(report: ValidationReport) -> None:
    """Already computed, previously discarded: the runner evaluates each fold as a backtest."""
    assert any(fold.trades for fold in report.folds)
    for fold in report.folds:
        assert len(fold.trades) >= fold.metrics.total_trades


def test_a_folds_trades_fall_inside_its_own_test_window(report: ValidationReport) -> None:
    """The trades belong to that fold's parameter vector, and to the window it was scored on."""
    for fold in report.folds:
        for trade in fold.trades:
            assert fold.first_test_bar <= trade.entry_date <= fold.last_test_bar


def test_the_folds_partition_their_trades_rather_than_sharing_them(
    report: ValidationReport,
) -> None:
    """No trade appears twice.

    The windows are contiguous and non-overlapping by construction (section 12.1), so a trade
    turning up in two folds would mean a fold was scored on bars another fold also claimed.
    That is what would make pooling look harmless, and it is the assumption section 12.9
    forbids relying on.
    """
    entries = [trade.entry_date for fold in report.folds for trade in fold.trades]

    assert len(entries) == len(set(entries))


def test_the_total_trade_count_stays_a_count_not_a_pooled_sample(
    report: ValidationReport,
) -> None:
    """``total_trades`` sums the folds' *closed* counts and claims nothing more.

    It says how much evidence the exercise produced. It is not a sample from one strategy, and
    the per-fold lists are longer than it whenever a fold ended holding a position -- an open
    trade is drawn but never counted (spec section 12.9).
    """
    assert report.total_trades == sum(fold.metrics.total_trades for fold in report.folds)
    assert report.total_trades <= sum(len(fold.trades) for fold in report.folds)


def test_the_walk_forward_report_renders(report: ValidationReport) -> None:
    from rich.console import Console

    from cracktrade.cli.render import render_validation

    console = Console(file=io.StringIO(), width=110, force_terminal=False)
    render_validation(report, console)
    output = console.file.getvalue()  # type: ignore[attr-defined]

    assert "walk-forward folds" in output
    assert "Robustness" in output
    assert "Cost sensitivity" in output
    assert ("CREDIBLE" in output) or ("NOT CREDIBLE" in output)


# ------------------------------------------------------------------ 12.5: stability


def test_stability_perturbs_every_parameter(report: ValidationReport) -> None:
    from cracktrade.validate.stability import DEFAULT_PERTURBATIONS

    parameters = discover_parameters(strategy_with())

    assert len(report.stability.points) == len(parameters) * len(DEFAULT_PERTURBATIONS)


def test_an_integer_parameter_is_actually_moved_by_a_small_nudge() -> None:
    """A 10% nudge to a 3-day window rounds back to 3, reporting stability never tested."""
    from cracktrade.validate.stability import _round_away

    assert _round_away(3.0, 3.3) == 4.0
    assert _round_away(3.0, 2.7) == 2.0
    assert _round_away(20.0, 22.0) == 22.0


def test_degradation_is_bounded_to_a_fraction() -> None:
    from cracktrade.validate.stability import _degradation

    assert _degradation(-10.0, -10.0) == 0.0
    assert _degradation(-10.0, -5.0) == pytest.approx(0.5)
    assert _degradation(-10.0, 100.0) == 1.0
    assert _degradation(-10.0, float("inf")) == 1.0


def test_a_baseline_with_no_edge_cannot_degrade() -> None:
    from cracktrade.validate.stability import _degradation

    assert _degradation(0.0, 5.0) == 0.0


def test_stability_and_credibility_are_reported_separately(report: ValidationReport) -> None:
    """A stable optimum can still be a stable optimum on noise."""
    assert isinstance(report.stability.is_stable, bool)
    assert isinstance(report.is_credible, bool)


def test_a_walk_forward_run_is_reproducible() -> None:
    kwargs: dict[str, Any] = {
        "folds": 3,
        "epochs": 1,
        "seed": 0,
        "workers": 1,
        "min_test_bars": 60,
    }
    data = trending_market(1200)

    first = walk_forward(strategy_with(), data, **kwargs)
    second = walk_forward(strategy_with(), data, **kwargs)

    assert first.returns == second.returns
    assert first.deflated.probability == second.deflated.probability


# ------------------------------------------------------------------ the checks are the verdict


def test_the_verdict_is_the_conjunction_of_its_checks(report: ValidationReport) -> None:
    """One definition, not three.

    ``is_credible`` and ``failures`` are both derived from ``checks``. If they were computed
    separately -- as they were before phase 4 -- a banner could say NOT CREDIBLE while the
    check table showed eight passes, and nobody would know which to believe.
    """
    assert report.is_credible == all(check.passed for check in report.checks)
    assert report.failures == tuple(c.detail for c in report.checks if not c.passed)


def test_every_check_is_reportable(report: ValidationReport) -> None:
    """Each field has a job on screen, so none of them may be blank."""
    for check in report.checks:
        assert check.name and check.name.islower()
        assert check.label
        assert check.plain.endswith(".")
        assert check.stat
        assert check.detail


def test_check_names_are_unique_and_stable(report: ValidationReport) -> None:
    """Interfaces branch on these, so they are an API, not a label."""
    names = [check.name for check in report.checks]
    assert len(names) == len(set(names))
    assert set(names) == {
        "benchmark",
        "fold_results",
        "deflated_sharpe",
        "overfitting",
        "stability",
        "costs",
        "intervals",
        "trade_count",
    }


def test_a_straddling_interval_is_a_failed_check(report: ValidationReport) -> None:
    """Spec 12.6 made explicit: an interval containing zero is not distinguishable from luck."""
    intervals = next(check for check in report.checks if check.name == "intervals")
    assert intervals.passed == report.mean_return_interval.excludes_zero


def test_checks_survive_serialisation(report: ValidationReport) -> None:
    """The UI's check table reads this, so it is contractual output rather than a property."""
    payload = to_dict(report)
    checks = payload["checks"]
    assert len(checks) == len(report.checks)
    assert set(checks[0]) == {"name", "label", "passed", "plain", "stat", "detail"}


def test_the_intervals_check_prints_the_figure_it_measured(report: ValidationReport) -> None:
    """Spec 12.6. The interval is a fraction of a per-bar return, and must be scaled to print.

    Formatted raw at one decimal, as it was, every real interval rendered as ``+0.0% … +0.0%``
    -- a mean bar return is about 0.0003 -- in the CLI, in the stored checks, and in the web
    robustness panel, which renders ``stat`` verbatim. It was also labelled "mean fold return",
    which is a third quantity again.
    """
    intervals = next(check for check in report.checks if check.name == "intervals")

    assert f"{100 * report.mean_return_interval.low:+.3f}%" in intervals.stat
    assert f"{100 * report.mean_return_interval.high:+.3f}%" in intervals.stat
    assert "per-bar" in intervals.plain
    assert "fold return" not in intervals.detail


def test_the_intervals_check_does_not_collapse_to_zero(report: ValidationReport) -> None:
    """The regression itself: a non-degenerate interval must not print as two zeroes."""
    if report.mean_return_interval.low == report.mean_return_interval.high:
        pytest.skip("a degenerate interval legitimately prints as zero")

    intervals = next(check for check in report.checks if check.name == "intervals")
    assert intervals.stat.count("+0.000%") < 2
