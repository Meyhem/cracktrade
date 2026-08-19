"""Phase 4: optimize, walk-forward and evolution on intraday bars.

The search machinery is bar-denominated throughout, so most of it carries over untouched. What
does not carry over is *meaning*: thirty bars is a reasonable test window when a bar is a day
and under two Xetra sessions when it is half an hour, and 680 bars reads as 2.7 years to any
arithmetic still dividing by 252. Those are the two failure modes exercised here, plus the flag
that says so when a result is thin.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from typing import Any

import pytest
import yaml

from cracktrade.backtest import Calendar, run_backtest
from cracktrade.config import Interval, Strategy, parse_strategy
from cracktrade.data import MarketData, StaticProvider, load_history, prepare_frame
from cracktrade.errors import EvolutionError, OptimizationError
from cracktrade.evolution import Chassis, GaSettings, crossover, evolve, mutate, random_genome
from cracktrade.evolution.genome import MIN_HOLDING_GENE
from cracktrade.evolution.protocol import (
    DEFAULT_MIN_SEGMENT_BARS,
    MIN_SEGMENT_SESSIONS,
    effective_min_segment_bars,
)
from cracktrade.history import MULTI_WINDOW_SESSIONS, SINGLE_WINDOW_SESSIONS, scope_of
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import optimize
from cracktrade.optimize.objective import TradeFloor
from cracktrade.optimize.windows import MIN_TEST_SESSIONS, effective_min_test_bars, split
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from cracktrade.validate import walk_forward
from cracktrade.validate.folds import walk_forward_splits
from tests.factories import make_ohlcv
from tests.intraday import load_fixture

AFTER_CAPTURE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


def strategy(
    interval: str = "1h",
    ticker: str = "SAP.DE",
    start: str = "2024-09-20",
    end: str = "2026-08-18",
    window: int = 20,
) -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "intraday_search"},
            "universe": {
                "ticker": ticker,
                "start_date": start,
                "end_date": end,
                "interval": interval,
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "indicators": [{"name": "sma_fast", "type": "sma", "window": window}],
            "entry": {"signal": "close > sma_fast"},
            "exit": {"signal": "close < sma_fast", "max_holding_bars": 8},
        }
    )


def provider() -> StaticProvider:
    return StaticProvider(
        by_interval={
            ("SAP.DE", Interval.M30): load_fixture("sap_de_30m"),
            ("SAP.DE", Interval.H1): load_fixture("sap_de_1h"),
        }
    )


def market(**kwargs: Any) -> MarketData:
    return load_history(strategy(**kwargs), provider(), now=AFTER_CAPTURE)


def hourly() -> MarketData:
    """Two years of Xetra hourly bars -- the only intraday history deep enough to search."""
    return market()


def half_hourly() -> MarketData:
    """Eight weeks of 30-minute bars, which is all the provider will ever serve."""
    return market(interval="30m", start="2026-06-29", end="2026-08-18")


def a_daily_market(bars: int) -> MarketData:
    """Synthetic daily history -- the control every intraday assertion here is measured against."""
    return MarketData(
        ticker="TEST",
        frame=prepare_frame(make_ohlcv(bars), ticker="TEST", now=date(2100, 1, 1)),
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )


# -------------------------------------------------------- session-aware windows


def test_a_thirty_bar_test_window_is_under_two_xetra_sessions() -> None:
    """The premise. 30 bars is a fine floor daily and an anecdote at 30 minutes.

    Whether one overnight gap falls inside a window that short decides the result by itself.
    """
    data = half_hourly()
    assert Calendar.of(data).bars_per_session == 17
    assert 30 / 17 < 2


def test_the_test_window_floor_is_raised_to_whole_sessions_intraday() -> None:
    data = half_hourly()
    assert effective_min_test_bars(data, 30) == MIN_TEST_SESSIONS * 17


def test_the_daily_floor_is_left_exactly_as_it_was() -> None:
    assert effective_min_test_bars(a_daily_market(300), 30) == 30


def test_a_caller_asking_for_more_still_gets_more() -> None:
    """The session floor is a lower bound, not a replacement."""
    assert effective_min_test_bars(half_hourly(), 500) == 500


def test_a_split_leaving_under_five_sessions_is_refused_by_name() -> None:
    """The message has to say the interval, or "30 bars" reads as adequate."""
    data = half_hourly()
    with pytest.raises(OptimizationError, match="30m"):
        split(data, train_fraction=0.99, warmup=5, min_test_bars=30)


def test_too_many_folds_on_thin_intraday_history_is_refused() -> None:
    with pytest.raises(OptimizationError, match="30m"):
        walk_forward_splits(half_hourly(), warmup=20, folds=20)


def test_folds_that_fit_are_produced() -> None:
    splits = walk_forward_splits(hourly(), warmup=20, folds=4)
    assert len(splits) == 4


# ------------------------------------------------------------- the trade floor


def test_the_trade_floor_reads_intraday_bars_as_sessions_not_years() -> None:
    """680 half-hour bars is forty sessions, not 2.7 years.

    Left at 252 the rate-based floor would demand a year's worth of trades from eight weeks of
    history, then blame the strategy for not producing them.
    """
    floor = TradeFloor(minimum=0, per_year=12.0)
    calendar = Calendar.of(half_hourly())

    naive = floor.required(len(half_hourly()), periods_per_year=TRADING_DAYS_PER_YEAR)
    honest = floor.required(len(half_hourly()), periods_per_year=calendar.periods_per_year)

    assert naive > honest
    assert honest <= 2


def test_the_daily_trade_floor_is_unchanged() -> None:
    floor = TradeFloor(minimum=0, per_year=12.0)
    assert floor.required(TRADING_DAYS_PER_YEAR, periods_per_year=TRADING_DAYS_PER_YEAR) == 12
    assert floor.required(TRADING_DAYS_PER_YEAR, periods_per_year=TRADING_DAYS_PER_YEAR) == 12


# ------------------------------------------------------------- history scope


def test_a_backtest_on_thin_intraday_history_is_flagged() -> None:
    thin = strategy(interval="30m", start="2026-06-29", end="2026-08-18")
    result = run_backtest(thin, half_hourly())
    assert result.history is not None
    assert result.history.limited
    assert result.history.interval == "30m"
    assert result.history.sessions < SINGLE_WINDOW_SESSIONS


def test_the_note_is_stated_in_sessions_and_names_the_way_out() -> None:
    """ "680 bars" hides what "40 sessions of 30m bars" says plainly."""
    scope = scope_of(half_hourly())
    assert scope.note is not None
    assert "session" in scope.note
    assert "1h" in scope.note


def test_two_years_of_hourly_bars_is_not_flagged() -> None:
    """1h reaches back about two years, which is the honest recommendation the note carries."""
    scope = scope_of(hourly())
    assert not scope.limited
    assert scope.note is None
    assert scope.sessions > MULTI_WINDOW_SESSIONS


def test_a_walk_forward_needs_more_history_than_a_backtest() -> None:
    """Cutting thin history into folds makes each fold thinner still."""
    data = hourly().head(60 * 9)
    assert not scope_of(data).limited
    assert scope_of(data, multi_window=True).limited


def test_a_daily_history_counts_bars_as_sessions() -> None:
    scope = scope_of(a_daily_market(400))
    assert scope.sessions == 400
    assert scope.interval == "1d"
    assert not scope.limited


# ----------------------------------------------------- the four run kinds end to end


def test_a_backtest_runs_on_hourly_bars() -> None:
    result = run_backtest(strategy(), hourly())
    assert result.metrics.total_trades > 0
    assert result.overnight_carries >= 0


def test_an_optimization_runs_on_hourly_bars() -> None:
    result = optimize(strategy(), hourly(), epochs=2, workers=1, seed=0)
    assert result.history is not None
    assert result.test_bars > 0


def test_a_walk_forward_runs_on_hourly_bars() -> None:
    report = walk_forward(strategy(), hourly(), folds=3, epochs=2, workers=1, seed=0)
    assert len(report.folds) == 3
    assert report.history is not None
    assert not report.history.limited


def test_an_evolution_runs_on_hourly_bars() -> None:
    """The chassis has to carry the interval, and the genes have to be spelled in bars.

    Both were wrong when Phase 1 landed: ``Chassis`` had no interval at all, so every evolved
    strategy came back daily whatever was asked for, and ``mapping()`` emitted
    ``max_holding_days``, which an intraday strategy refuses outright -- so the first genome to
    draw a holding cap would have failed to parse.
    """
    chassis = Chassis(
        name="evolved_intraday",
        ticker="SAP.DE",
        start_date=date(2024, 9, 20),
        end_date=date(2026, 8, 18),
        interval=Interval.H1,
        initial_capital=10_000.0,
        slippage_pct=0.0,
        commission_pct=0.0,
    )
    result = evolve(
        chassis,
        hourly(),
        settings=GaSettings(population=8, generations=2),
        seed=0,
        workers=1,
    )
    assert "interval: 1h" in result.strategy_yaml
    assert "holding_days" not in result.strategy_yaml
    assert result.history is not None
    assert result.history.interval == "1h"

    # The evolved strategy has to survive the round trip it will actually make: the CLI writes
    # this YAML out, and the API stores it and re-parses it on every read.
    assert parse_strategy(yaml.safe_load(result.strategy_yaml)).universe.interval is Interval.H1


def test_evolution_on_half_hourly_bars_runs_and_says_how_thin_it_is() -> None:
    """Eight weeks is all 30m will ever serve, and the run is allowed on exactly that.

    It divides into four segments of about four Xetra sessions each. Nothing about that is
    comfortable, and the protection is not a refusal but the honesty either side of it: the
    result carries the thin-history flag naming the sessions it covers, and the winner still
    faces the holdout and the robustness checks like any other. A user who asks for a search on
    seven weeks of market, having been told that is what it is, gets it.
    """
    chassis = Chassis(
        name="evolved_thin",
        ticker="SAP.DE",
        start_date=date(2026, 6, 29),
        end_date=date(2026, 8, 18),
        interval=Interval.M30,
    )
    result = evolve(
        chassis, half_hourly(), settings=GaSettings(population=8, generations=2), seed=0
    )

    assert "interval: 30m" in result.strategy_yaml
    assert result.history is not None
    assert result.history.limited
    assert result.history.note is not None
    assert "37 sessions of 30m bars" in result.history.note


def test_a_fortnight_of_hourly_bars_is_still_refused_with_the_arithmetic_named() -> None:
    """The floor bends for the shortest intervals; it does not disappear.

    Nine bars to a Xetra hour means eight weeks is 333 bars, and the block library's warm-up
    takes 200 of them before a single bar is scored. There is no honest division here, so the
    run is refused and the message says what was short and by how much.
    """
    chassis = Chassis(
        name="evolved_hourly_thin",
        ticker="SAP.DE",
        start_date=date(2026, 6, 29),
        end_date=date(2026, 8, 18),
        interval=Interval.H1,
    )
    thin = market(interval="1h", start="2026-06-29", end="2026-08-18")
    with pytest.raises(EvolutionError, match="after the 200-bar warm-up"):
        evolve(chassis, thin, settings=GaSettings(population=8, generations=2), seed=0)


def test_the_daily_bar_floor_does_not_decide_an_intraday_division() -> None:
    """Sixty bars is a quarter of a year daily and under four sessions at 30 minutes.

    Maxing the two floors let that daily number refuse a New York 30-minute run whose own
    session floor was thirty-nine, for a reason that had nothing to do with the history in hand.
    """
    data = half_hourly()
    assert effective_min_segment_bars(data, DEFAULT_MIN_SEGMENT_BARS) < DEFAULT_MIN_SEGMENT_BARS * 2
    assert effective_min_segment_bars(data, 10_000) == effective_min_segment_bars(
        data, DEFAULT_MIN_SEGMENT_BARS
    )


def test_the_segment_floor_is_counted_in_sessions_intraday() -> None:
    data = half_hourly()
    assert effective_min_segment_bars(data, DEFAULT_MIN_SEGMENT_BARS) == MIN_SEGMENT_SESSIONS * 17


def test_the_daily_segment_floor_is_left_exactly_as_it_was() -> None:
    daily = a_daily_market(400)
    assert effective_min_segment_bars(daily, DEFAULT_MIN_SEGMENT_BARS) == DEFAULT_MIN_SEGMENT_BARS


def test_the_search_never_proposes_a_minimum_holding_no_session_could_satisfy() -> None:
    """A nine-bar Xetra hour against a gene drawn to twenty.

    Unclamped, every genome drawing a minimum above the session length renders to a strategy the
    engine refuses outright, and the search reports the loss as a defect in the block library
    rather than as its own overreach.
    """
    session_bars = Calendar.of(hourly()).bars_per_session
    assert session_bars < MIN_HOLDING_GENE.high, "the fixture must actually exercise the clamp"

    rng = random.Random(7)
    genome = random_genome(rng, session_bars=session_bars)
    clamped = 0
    for _ in range(300):
        minimum = genome.min_holding_bars
        if minimum is not None:
            assert minimum < session_bars
            clamped += 1
        partner = random_genome(rng, session_bars=session_bars)
        genome = mutate(
            crossover(genome, partner, rng, session_bars=session_bars),
            rng,
            rate=0.4,
            session_bars=session_bars,
        )
    assert clamped > 0, "the loop must have drawn a minimum holding period at all"


def test_the_clamp_leaves_a_daily_search_bit_for_bit_unchanged() -> None:
    """The clamp is applied after the draw, so the random sequence is the same at any interval."""
    with_clamp = [random_genome(random.Random(4), session_bars=None) for _ in range(20)]
    without = [random_genome(random.Random(4)) for _ in range(20)]
    assert with_clamp == without


def test_a_walk_forward_on_half_hourly_bars_carries_the_flag() -> None:
    """Eight weeks is every 30-minute bar the provider has. The flag will always be on.

    That is not a calibration failure -- it is true, and it is the whole reason the flag
    exists rather than a silent number.
    """
    data = half_hourly()
    report = walk_forward(
        strategy(interval="30m", start="2026-06-29", end="2026-08-18"),
        data,
        folds=2,
        epochs=2,
        workers=1,
        seed=0,
    )
    assert report.history is not None
    assert report.history.limited
    assert report.history.note is not None


# ------------------------------------------------- the search scores what the report says


def test_the_test_window_is_reported_on_the_run_calendar_not_a_daily_one() -> None:
    """The number the optimizer prints is the number the calendar implies -- pinned by value.

    Before this, exactly one of twelve ``extract_metrics`` call sites passed a calendar, so
    the search paths annualised hourly bars on a 252-bar year: on this fixture the same
    portfolio scored a *negative* Sharpe daily and a positive one honestly, because the daily
    basis charges a year of risk-free return against every 252 hours.
    """
    from cracktrade.backtest.calendar import DAILY
    from cracktrade.backtest.metrics import extract_metrics
    from cracktrade.backtest.runner import run_simulation
    from cracktrade.optimize.runner import _evaluate

    data = hourly()
    base = strategy()
    division = split(data, train_fraction=0.8, warmup=25)

    reported, _ = _evaluate(base, division.test)

    simulation = run_simulation(base, division.test.data)
    honest = extract_metrics(
        simulation.portfolio,
        risk_free_rate=base.execution.risk_free_rate,
        calendar=simulation.calendar,
        offset=division.test.offset,
    )
    daily = extract_metrics(
        simulation.portfolio,
        risk_free_rate=base.execution.risk_free_rate,
        calendar=DAILY,
        offset=division.test.offset,
    )

    assert simulation.calendar.periods_per_year == 252 * 9
    assert reported.sharpe_ratio == honest.sharpe_ratio
    assert reported.cagr_pct == honest.cagr_pct
    # The distance the fix covers. If these ever agree the test has stopped testing anything.
    assert reported.sharpe_ratio != daily.sharpe_ratio
    assert reported.cagr_pct != daily.cagr_pct


def test_trial_sharpes_deannualise_back_to_the_per_bar_footing_exactly() -> None:
    """Section 12.3 mixes the Sharpe with the observation count, so both must be per-bar.

    A trial Sharpe is annualised by ``sqrt(periods_per_year)`` on the calendar of the window
    it was scored on, and ``_per_period`` divides by the same factor -- so the round trip must
    be exact, not approximate. Before the fix the two factors came from different calendars:
    annualised at sqrt(252), divided by sqrt(2268), leaving hourly trial Sharpes three times
    too small and their variance nine times too small -- an *understated* luck threshold.
    """
    import numpy as np

    from cracktrade.validate.runner import _per_period

    calendar = Calendar.of(hourly())
    per_bar = np.array([0.02, -0.01, 0.005])
    annualised = per_bar * np.sqrt(calendar.periods_per_year)

    assert _per_period(annualised, calendar) == pytest.approx(per_bar)
