"""Phase 4: per-bar series captured at run time.

Charts are the most persuasive thing this application produces, which makes them the most
dangerous. Two properties are defended here. A captured series must **agree with the metrics
printed beside it** -- a drawdown chart bottoming somewhere other than the reported maximum
drawdown would make one of the two a lie. And capturing must **not change the run**, for the
same reason progress hooks must not.

The third property is structural: series are captured while the run executes and cannot be
rebuilt later, because the provider retroactively adjusts prices. That one is not testable
here -- it is a property of when the code is called, enforced by the schema having nowhere to
recompute into -- but it is why this module exists at all.
"""

from __future__ import annotations

import pandas as pd
import pytest

from cracktrade.backtest import run_backtest
from cracktrade.data import MarketData
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import optimize
from cracktrade.serialize import to_dict
from cracktrade.validate import FoldScheme, walk_forward
from tests.test_metrics import trending_market
from tests.test_optimize import strategy_with

SEED = 11


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


@pytest.fixture(scope="module")
def market() -> MarketData:
    return trending_market(bars=600)


# --------------------------------------------------------------------------- opt-in


def test_capture_is_off_by_default(market: MarketData) -> None:
    """An ordinary backtest pays nothing for charts nobody asked for."""
    assert run_backtest(strategy_with(), market).series is None


def test_capturing_does_not_change_the_result(market: MarketData) -> None:
    """The same guarantee as the progress hooks: observing must not perturb."""
    plain = run_backtest(strategy_with(), market)
    with_series = run_backtest(strategy_with(), market, capture_series=True)
    assert with_series.metrics == plain.metrics
    assert with_series.trades == plain.trades
    assert with_series.benchmark == plain.benchmark


# --------------------------------------------------------------------------- agreement


def test_the_drawdown_chart_bottoms_where_the_metric_says(market: MarketData) -> None:
    """The headline property. Two definitions of drawdown would make one of them wrong."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    assert min(result.series.drawdown.values) == pytest.approx(
        result.metrics.max_drawdown_pct, abs=1e-6
    )


def test_every_series_covers_the_same_bars(market: MarketData) -> None:
    """Charts 1-3 share an x-axis, so they must share a date index exactly."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    series = result.series
    assert series.equity.dates == series.close.dates
    assert series.equity.dates == series.drawdown.dates
    assert series.equity.dates == series.benchmark_equity.dates
    assert series.equity.dates == series.rolling_12m_return.dates


def test_a_series_has_one_value_per_date(market: MarketData) -> None:
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    for series in (
        result.series.equity,
        result.series.close,
        result.series.drawdown,
        result.series.benchmark_equity,
        result.series.rolling_12m_return,
    ):
        assert len(series.dates) == len(series.values)


def test_drawdown_is_never_positive(market: MarketData) -> None:
    """It measures distance below a running peak, so above zero is impossible by definition."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    assert max(result.series.drawdown.values) <= 1e-9


def test_monthly_returns_distinguish_flat_from_absent(market: MarketData) -> None:
    """A month holding nothing and a month that ended flat are different facts."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    monthly = result.series.monthly_returns
    assert len(monthly.months) == len(monthly.values) == len(monthly.in_market)
    assert all(len(month) == 7 and month[4] == "-" for month in monthly.months)
    assert any(monthly.in_market), "the strategy traded, so some month must be in the market"


def test_the_rolling_window_is_trailing(market: MarketData) -> None:
    """A centred window would place future bars at t, in a chart rather than a metric.

    The first year of bars has no twelve-month history behind it, so a trailing window leaves
    them at zero. A centred one would have filled them from bars that had not happened.
    """
    from cracktrade.backtest.calendar import DAILY

    window = round(DAILY.periods_per_year)
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    rolling = result.series.rolling_12m_return.values
    assert all(value == 0.0 for value in rolling[:window])
    assert any(value != 0.0 for value in rolling[window:])


# --------------------------------------------------------------------------- filled bars


def test_filled_bars_are_named_not_merely_counted(market: MarketData) -> None:
    """A chart cannot mark a bar it was only told the number of.

    ``DataVintage.filled_bars`` says how many bars repeated the previous session; this says
    which. Without the dates a time-domain chart has no option but to draw a straight line
    through a price that never traded (spec section 8.1, amended 2026-08-17).
    """
    filled = pd.Series(False, index=market.frame.index)
    filled.iloc[5] = True
    filled.iloc[9] = True
    tracked = MarketData(
        ticker=market.ticker,
        frame=market.frame,
        requested_start=market.requested_start,
        requested_end=market.requested_end,
        filled=filled,
    )

    result = run_backtest(strategy_with(), tracked, capture_series=True)
    assert result.series is not None
    assert result.series.filled == (
        market.frame.index[5].date(),
        market.frame.index[9].date(),
    )
    assert result.vintage.filled_bars == 2


def test_untracked_provenance_marks_nothing(market: MarketData) -> None:
    """An empty tuple is not a claim that nothing was filled.

    The fixture's provider never recorded provenance. Reporting no marks is the only honest
    answer -- the vintage block is where "none" and "not recorded" are told apart.
    """
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    assert market.filled is None
    assert result.series.filled == ()


def test_an_optimization_marks_only_the_filled_bars_it_charted(market: MarketData) -> None:
    """The window is the test window, so a filled bar in the train half is not on this chart."""
    filled = pd.Series(False, index=market.frame.index)
    filled.iloc[2] = True
    filled.iloc[-3] = True
    tracked = MarketData(
        ticker=market.ticker,
        frame=market.frame,
        requested_start=market.requested_start,
        requested_end=market.requested_end,
        filled=filled,
    )

    result = optimize(strategy_with(), tracked, epochs=2, seed=SEED, workers=1, capture_series=True)
    assert result.series is not None
    assert result.series.filled == (market.frame.index[-3].date(),)


# --------------------------------------------------------------------------- the other runners


def test_an_optimization_captures_only_the_test_window(market: MarketData) -> None:
    """Charts for a search must describe the window it never saw, not the one it fitted."""
    result = optimize(strategy_with(), market, epochs=2, seed=SEED, workers=1, capture_series=True)
    assert result.series is not None
    assert len(result.series.equity.values) == result.test_bars


def test_walk_forward_captures_one_series_per_fold(market: MarketData) -> None:
    report = walk_forward(
        strategy_with(),
        market,
        folds=2,
        scheme=FoldScheme.ANCHORED,
        epochs=2,
        seed=SEED,
        workers=1,
        capture_series=True,
    )
    assert len(report.fold_series) == len(report.folds)
    for series in report.fold_series:
        assert series.equity.dates


def test_walk_forward_does_not_splice_folds_into_one_curve(market: MarketData) -> None:
    """Each fold re-optimizes, so the folds are different strategies.

    Joining their equity curves would draw a single strategy that was never traded, which is
    exactly the impression a continuous line gives.
    """
    report = walk_forward(
        strategy_with(),
        market,
        folds=2,
        scheme=FoldScheme.ANCHORED,
        epochs=2,
        seed=SEED,
        workers=1,
        capture_series=True,
    )
    assert not hasattr(report, "series")
    assert len(report.fold_series) == 2


def test_walk_forward_captures_nothing_by_default(market: MarketData) -> None:
    report = walk_forward(
        strategy_with(),
        market,
        folds=2,
        scheme=FoldScheme.ANCHORED,
        epochs=2,
        seed=SEED,
        workers=1,
    )
    assert report.fold_series == ()


# --------------------------------------------------------------------------- serialisation


def test_series_survive_serialisation(market: MarketData) -> None:
    """The API stores this verbatim, so it has to survive the same conversion the CLI uses."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    payload = to_dict(result)
    series = payload["series"]
    assert set(series) == {
        "equity",
        "benchmark_equity",
        "drawdown",
        "close",
        "monthly_returns",
        "rolling_12m_return",
        "filled",
    }
    assert len(series["equity"]["dates"]) == len(series["equity"]["values"])
    assert isinstance(series["equity"]["dates"][0], str)
    assert series["monthly_returns"]["in_market"][0] in (True, False)


def test_an_uncaptured_result_serialises_to_null(market: MarketData) -> None:
    assert to_dict(run_backtest(strategy_with(), market))["series"] is None


def test_monthly_returns_compound_to_the_windows_total_return(market: MarketData) -> None:
    """Spec 8.1. Every bar's return belongs to exactly one month, so the cells must reconcile.

    They did not. A month was measured as ``(last - first) / first`` of its own equity, which
    drops the move from the previous month's *close* into its first bar -- so the cells fell
    short of the headline by the sum of those gaps, and a month whose gain arrived in an
    opening gap was understated by exactly that gain.
    """
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None

    compounded = 1.0
    for value in result.series.monthly_returns.values:
        compounded *= 1.0 + value / 100.0

    assert 100.0 * (compounded - 1.0) == pytest.approx(result.metrics.total_return_pct, abs=1e-6)


def test_a_months_opening_move_is_not_discarded(market: MarketData) -> None:
    """The bar that opens a month is measured against the previous month's close, not itself."""
    result = run_backtest(strategy_with(), market, capture_series=True)
    assert result.series is not None
    monthly = result.series.monthly_returns

    equity = pd.Series(
        result.series.equity.values, index=pd.DatetimeIndex(result.series.equity.dates)
    )
    first_to_last = equity.resample("ME").last() / equity.resample("ME").first() - 1.0

    # The two agree only where a month opened exactly where the previous one closed. Over a
    # real equity curve that is not every month, and the difference is what used to be lost.
    assert any(
        abs(100.0 * float(naive) - reported) > 1e-9
        for naive, reported in zip(first_to_last.to_numpy(), monthly.values, strict=True)
    )
