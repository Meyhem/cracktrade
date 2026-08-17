"""The serialised shape is an interface contract, so it is pinned rather than assumed.

Two consumers read `to_dict` output: the CLI's `--format json`, and the HTTP API, which stores
it verbatim in `run.result` and hands it back unaltered (spec §15.1). A property quietly
dropped from `_EXPOSED_PROPERTIES` therefore breaks clients silently -- and worse, it breaks
them *historically*, because runs already stored keep the old shape while new ones lose it.

Keys are pinned, not values. Values move with the data and with every legitimate change to the
engine; the set of keys is what a client writes code against. Anything added here is an
additive API change, and anything removed is a breaking one -- this test is the place that
makes the difference visible in review.
"""

from __future__ import annotations

from typing import Any

import pytest

from cracktrade.backtest import run_backtest
from cracktrade.domain import BacktestResult, OptimizationResult, ValidationReport
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import optimize
from cracktrade.serialize import _EXPOSED_PROPERTIES, to_dict
from cracktrade.validate import walk_forward
from tests.test_metrics import trending_market
from tests.test_optimize import strategy_with


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


# Module-scoped: each of these is a real engine run, and pinning a *shape* does not need three
# fresh ones per test. Small budgets throughout -- the numbers are irrelevant here, only the
# keys are, and a slow contract test is a contract test that stops being run.


@pytest.fixture(scope="module")
def backtest_result() -> BacktestResult:
    return run_backtest(strategy_with(), trending_market(400))


@pytest.fixture(scope="module")
def optimize_result() -> OptimizationResult:
    return optimize(strategy_with(), trending_market(760), epochs=2, workers=1, seed=0)


@pytest.fixture(scope="module")
def walk_forward_report() -> ValidationReport:
    return walk_forward(
        strategy_with(),
        trending_market(1400),
        folds=4,
        epochs=2,
        seed=0,
        workers=1,
        min_test_bars=60,
    )


#: Every top-level key of a serialised `BacktestResult`.
BACKTEST_KEYS = frozenset(
    {
        "strategy_name",
        "ticker",
        "metrics",
        "benchmark",
        "trades",
        "vintage",
        "entry_defined_pct",
        "exit_defined_pct",
        "active_stop",
        "shadowed_stops",
        "risk_free_rate",
        "warmup_bars",
        "series",
    }
)

#: Every top-level key of a serialised `OptimizationResult`, derived properties included.
OPTIMIZE_KEYS = frozenset(
    {
        "strategy_name",
        "ticker",
        "objective",
        "optimized_yaml",
        "test_metrics",
        "train_metrics",
        "baseline_test_metrics",
        "changes",
        "trades",
        "train_bars",
        "test_bars",
        "evaluations",
        "failures",
        "most_common_failure",
        "infeasible",
        "counts_exact",
        "trials",
        "budget",
        "seed",
        "elapsed_seconds",
        "convergence_message",
        "improvement_pct",
        "overfitting_gap_pct",
        "parameters_at_bound",
        "series",
    }
)

#: Every top-level key of a serialised `ValidationReport`. `checks`, `failures` and
#: `is_credible` are the verdict; a client must never have to reconstruct them.
WALK_FORWARD_KEYS = frozenset(
    {
        "strategy_name",
        "ticker",
        "objective",
        "scheme",
        "folds",
        "benchmark",
        "optimized_yaml",
        "deflated",
        "overfitting",
        "stability",
        "costs",
        "mean_return_interval",
        "total_return_interval",
        "trials",
        "seed",
        "elapsed_seconds",
        "fold_series",
        "checks",
        "combined_return_pct",
        "beats_buy_and_hold",
        "returns",
        "profitable_folds",
        "fold_win_rate",
        "median_return_pct",
        "return_iqr_pct",
        "total_trades",
        "failures",
        "is_credible",
    }
)


def _serialised(result: object) -> dict[str, Any]:
    payload = to_dict(result)
    assert isinstance(payload, dict)
    return payload


def test_a_backtest_result_serialises_to_its_declared_keys(backtest_result: Any) -> None:
    assert set(_serialised(backtest_result)) == BACKTEST_KEYS


def test_an_optimization_result_serialises_to_its_declared_keys(optimize_result: Any) -> None:
    assert set(_serialised(optimize_result)) == OPTIMIZE_KEYS


def test_a_validation_report_serialises_to_its_declared_keys(walk_forward_report: Any) -> None:
    assert set(_serialised(walk_forward_report)) == WALK_FORWARD_KEYS


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("backtest_result", BACKTEST_KEYS),
        ("optimize_result", OPTIMIZE_KEYS),
        ("walk_forward_report", WALK_FORWARD_KEYS),
    ],
)
def test_every_declared_property_actually_appears(
    fixture: str, expected: frozenset[str], request: pytest.FixtureRequest
) -> None:
    """`_EXPOSED_PROPERTIES` is a promise; this checks the promise is kept.

    A name listed there but renamed on the model would serialise as nothing at all, which is
    the failure mode this catches -- the declaration and the model drifting apart without
    either of them being obviously wrong on its own.
    """
    result = request.getfixturevalue(fixture)
    declared = _EXPOSED_PROPERTIES.get(type(result).__name__, ())
    payload = _serialised(result)
    assert set(declared) <= set(payload)
    assert set(declared) <= expected


def test_the_verdict_travels_with_the_report(walk_forward_report: Any) -> None:
    """The three keys the API is forbidden from recomputing (spec §15.1)."""
    payload = _serialised(walk_forward_report)
    assert isinstance(payload["is_credible"], bool)
    assert isinstance(payload["failures"], list)
    assert len(payload["checks"]) == 8
    assert {check["name"] for check in payload["checks"]} == {
        "fold_results",
        "benchmark",
        "deflated_sharpe",
        "overfitting",
        "stability",
        "costs",
        "intervals",
        "trade_count",
    }
    # A failure is the detail of a check that did not pass -- one list derived from the other,
    # never two independently maintained lists.
    assert payload["failures"] == [
        check["detail"] for check in payload["checks"] if not check["passed"]
    ]
