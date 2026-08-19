"""Phase 7: metrics, benchmark, and result models (spec section 8).

The risk-free rate carries two separate legacy defects and they compound. D3 made the configured
value inert; D4 fed an annual rate into a parameter vectorbt reads per-period. Measured on the
sample below, the second one alone moves Sharpe from 0.17 to roughly -75, which is why every
legacy strategy looked catastrophic and why the ratio was useless for comparing anything.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from cracktrade.backtest import (
    extract_metrics,
    extract_trades,
    per_period_risk_free,
    run_backtest,
    worst_rolling_12m,
    yearly_returns,
)
from cracktrade.backtest.metrics import _profit_factor
from cracktrade.backtest.results import frame_digest
from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData
from cracktrade.domain import MIN_TRADES_TO_JUDGE, BacktestResult
from cracktrade.settings import TRADING_DAYS_PER_YEAR

TICKER = "TEST"


def trending_market(bars: int = 760, drift: float = 0.0005, seed: int = 3) -> MarketData:
    """A random walk with mild upward drift, long enough for multi-year statistics."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.012, bars)))
    index = pd.DatetimeIndex(
        pd.date_range("2020-01-01", periods=bars, freq="B").to_numpy(), name="Date"
    )
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.008,
            "Low": close * 0.992,
            "Close": close,
            "Volume": np.full(bars, 1_000_000.0),
        },
        index=index,
    )
    return MarketData(
        ticker=TICKER,
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )


def strategy_with(
    *,
    risk_free_rate: float = 0.04,
    indicators: list[dict[str, Any]] | None = None,
    entry: dict[str, Any] | None = None,
    exit_rule: dict[str, Any] | None = None,
) -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "phase7"},
            "universe": {
                "ticker": TICKER,
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10_000.0,
                "commission_pct": 0.1,
                "slippage_pct": 0.05,
                "risk_free_rate": risk_free_rate,
            },
            "indicators": indicators or [{"name": "sma_fast", "type": "sma", "window": 20}],
            "entry": entry or {"signal": "close > sma_fast"},
            "exit": exit_rule or {"signal": "close < sma_fast"},
        }
    )


def result_for(**kwargs: Any) -> BacktestResult:
    return run_backtest(strategy_with(**kwargs), trending_market())


# ---------------------------------------------------------- D4: the per-period conversion


def test_an_annual_rate_compounds_back_to_itself_over_252_periods() -> None:
    """The whole content of defect D4, and its direction is not detectable by eye."""
    per_period = per_period_risk_free(0.04)

    compounded = (1.0 + per_period) ** TRADING_DAYS_PER_YEAR - 1.0

    assert compounded == pytest.approx(0.04, rel=1e-12)


@pytest.mark.parametrize("annual", [0.0, 0.01, 0.04, 0.10, 0.25])
def test_the_conversion_round_trips_for_any_rate(annual: float) -> None:
    assert (1.0 + per_period_risk_free(annual)) ** TRADING_DAYS_PER_YEAR - 1.0 == pytest.approx(
        annual, rel=1e-12
    )


def test_a_zero_rate_converts_to_zero() -> None:
    assert per_period_risk_free(0.0) == 0.0


def test_the_per_period_rate_is_far_smaller_than_the_annual_one() -> None:
    """Guards the direction: 4% a year is about 0.0155% a day, not 4% a day."""
    assert per_period_risk_free(0.04) < 0.04 / 200


# ---------------------------------------------------------------- D3: the rate is honoured


def test_changing_the_risk_free_rate_changes_sharpe() -> None:
    """Defect D3. Legacy parsed the field and then hardcoded 0.04 at the point of use."""
    low = result_for(risk_free_rate=0.0).metrics.sharpe_ratio
    high = result_for(risk_free_rate=0.10).metrics.sharpe_ratio

    assert low != high
    assert low > high, "a higher hurdle must lower the ratio"


def test_changing_the_risk_free_rate_changes_sortino() -> None:
    low = result_for(risk_free_rate=0.0).metrics.sortino_ratio
    high = result_for(risk_free_rate=0.10).metrics.sortino_ratio

    assert low > high


def test_the_risk_free_rate_is_reported_so_the_ratios_are_interpretable() -> None:
    assert result_for(risk_free_rate=0.03).risk_free_rate == pytest.approx(0.03)


# ------------------------------------------------------------ A2: the 252-day calendar


def test_annualisation_uses_trading_days_not_calendar_days() -> None:
    """``year_freq`` defaults to a 365-day year, overstating CAGR by roughly 47%."""
    from cracktrade.backtest.calendar import DAILY

    data = trending_market()
    result = run_backtest(strategy_with(), data)
    simulation_cagr = result.metrics.cagr_pct

    assert DAILY.year_freq == pd.Timedelta("252 days")

    # Recompute the same figure on the calendar year vectorbt would have used by default.
    from cracktrade.backtest.runner import run_simulation

    portfolio = run_simulation(strategy_with(), data).portfolio
    calendar_cagr = 100.0 * float(portfolio.annualized_return(year_freq="365 days"))

    assert simulation_cagr != pytest.approx(calendar_cagr)


def test_the_global_year_freq_setting_is_never_mutated() -> None:
    """It is process-wide state; writing to it would leak into any other vectorbt user."""
    import vectorbt as vbt

    before = vbt.settings["returns"]["year_freq"]
    run_backtest(strategy_with(), trending_market())

    assert vbt.settings["returns"]["year_freq"] == before


# --------------------------------------------------------------------- the metric set


def test_every_metric_is_populated() -> None:
    metrics = result_for().metrics

    assert metrics.total_trades > 0
    assert metrics.bars > 0
    assert metrics.final_equity > 0
    assert 0.0 <= metrics.win_rate_pct <= 100.0
    assert 0.0 <= metrics.exposure_pct <= 100.0
    assert metrics.max_drawdown_pct <= 0.0
    assert metrics.avg_holding_bars > 0
    assert metrics.worst_trade_pnl <= metrics.best_trade_pnl


def test_a_strategy_that_never_trades_reports_zeros_not_nan() -> None:
    """ "Nothing happened" must not surface as NaN, which reads as a broken run."""
    result = run_backtest(
        strategy_with(entry={"signal": "close > 1000000"}),
        trending_market(),
    )
    metrics = result.metrics

    assert metrics.total_trades == 0
    assert metrics.total_pnl == 0.0
    assert metrics.win_rate_pct == 0.0
    assert metrics.profit_factor == 0.0
    assert not np.isnan(metrics.cagr_pct)
    assert not np.isnan(metrics.sharpe_ratio)


@pytest.mark.parametrize(
    ("gains", "losses", "expected"),
    [
        (300.0, 100.0, 3.0),
        (0.0, 100.0, 0.0),
        (100.0, 0.0, float("inf")),
        (0.0, 0.0, 0.0),
    ],
)
def test_profit_factor_handles_the_degenerate_cases(
    gains: float, losses: float, expected: float
) -> None:
    assert _profit_factor(gains, losses) == expected


def test_exposure_is_reported_beside_the_risk_adjusted_metrics() -> None:
    """Audit B9: the ratios charge a hurdle over the whole period, exposure makes that readable."""
    metrics = result_for().metrics

    assert 0.0 < metrics.exposure_pct < 100.0


# --------------------------------------------------------------------- B11: sub-periods


def test_returns_are_broken_down_by_calendar_year() -> None:
    years = result_for().metrics.yearly_returns

    assert len(years) >= 3
    assert [year.year for year in years] == sorted(year.year for year in years)


def test_yearly_returns_compound_within_each_year() -> None:
    index = pd.DatetimeIndex(pd.date_range("2021-01-01", periods=4, freq="D").to_numpy())
    returns = pd.Series([0.1, 0.1, 0.0, 0.0], index=index)

    years = yearly_returns(returns)

    assert len(years) == 1
    assert years[0].return_pct == pytest.approx(21.0)


def test_the_worst_rolling_twelve_months_is_reported() -> None:
    """A single aggregate cannot show that a strategy spent a year underwater."""
    bars = 500
    index = pd.DatetimeIndex(pd.date_range("2020-01-01", periods=bars, freq="B").to_numpy())
    returns = pd.Series(np.full(bars, -0.001), index=index)

    worst = worst_rolling_12m(returns)

    assert worst < 0
    assert worst == pytest.approx(100.0 * ((1 - 0.001) ** TRADING_DAYS_PER_YEAR - 1), rel=1e-9)


def test_a_short_history_reports_no_rolling_year() -> None:
    index = pd.DatetimeIndex(pd.date_range("2020-01-01", periods=50, freq="B").to_numpy())

    assert worst_rolling_12m(pd.Series(np.zeros(50), index=index)) == 0.0


# ------------------------------------------------------------------ B1: the benchmark


def test_the_result_carries_a_buy_and_hold_benchmark() -> None:
    """Audit B1: without this the report cannot answer "should I invest"."""
    benchmark = result_for().benchmark.benchmark

    assert benchmark.exposure_pct > 90.0, "buy and hold is always invested"
    assert benchmark.final_equity > 0
    # Zero closed trades is correct, not a bug: buy and hold never sells, so its position is
    # still open at the last bar and total_trades counts *closed* positions.
    assert benchmark.total_trades == 0


def test_the_benchmark_pays_the_same_costs_as_the_strategy() -> None:
    """A frictionless benchmark would be an unfairly high hurdle for the strategy to clear."""
    data = trending_market()
    strategy = strategy_with()
    result = run_backtest(strategy, data)
    warmup = result.warmup_bars

    # What holding would have returned with no commission and no slippage at all.
    entry_price = float(data.frame["Open"].iloc[warmup + 1])
    frictionless_pct = 100.0 * (float(data.frame["Close"].iloc[-1]) / entry_price - 1.0)

    assert result.benchmark.benchmark.total_return_pct < frictionless_pct


def test_the_benchmark_starts_where_the_strategy_could_first_trade() -> None:
    """Starting it at bar zero would credit it with return the strategy could not capture."""
    data = trending_market()
    result = run_backtest(strategy_with(), data)

    entry_price = float(data.frame["Open"].iloc[result.warmup_bars + 1])
    implied = 100.0 * (float(data.frame["Close"].iloc[-1]) / entry_price - 1.0)

    # Within a couple of points: the difference is exactly the round-trip cost.
    assert result.benchmark.benchmark.total_return_pct == pytest.approx(implied, abs=2.0)


def test_excess_return_is_the_difference_from_buy_and_hold() -> None:
    result = result_for()

    expected = result.metrics.total_return_pct - result.benchmark.benchmark.total_return_pct
    assert result.benchmark.excess_return_pct == pytest.approx(expected)


def test_beating_buy_and_hold_is_stated_explicitly() -> None:
    result = result_for()

    assert result.benchmark.beats_buy_and_hold == (result.benchmark.excess_return_pct > 0)


def test_a_losing_strategy_is_reported_as_losing_to_the_benchmark() -> None:
    """A strategy that barely trades cannot beat a rising market, and must not claim to."""
    result = run_backtest(
        strategy_with(entry={"signal": "close > 100000"}),
        trending_market(drift=0.001),
    )

    assert not result.benchmark.beats_buy_and_hold


# ------------------------------------------------------------------- B10: data vintage


def test_the_result_records_which_prices_produced_it() -> None:
    result = result_for()
    vintage = result.vintage

    assert vintage.ticker == TICKER
    assert vintage.bars == 760
    assert vintage.first_bar < vintage.last_bar
    assert len(vintage.frame_digest) == 16


def test_the_digest_changes_when_a_price_changes() -> None:
    data = trending_market()
    original = frame_digest(data.frame)

    altered = data.frame.copy()
    closes = altered["Close"].to_numpy().copy()
    closes[10] += 0.01
    altered["Close"] = closes

    assert frame_digest(altered) != original


def test_the_digest_is_stable_across_calls() -> None:
    data = trending_market()

    assert frame_digest(data.frame) == frame_digest(data.frame)


# ------------------------------------------------------------------------- trade records


def test_trades_are_domain_objects_without_vectorbt_column_names() -> None:
    trades = result_for().trades

    assert trades
    first = trades[0]
    assert isinstance(first.entry_date, date)
    assert first.holding_bars >= 0
    assert first.is_winner == (first.pnl > 0)


def test_an_open_position_has_no_exit_date() -> None:
    """Its exit_idx points at the last bar, which is a mark to market, not a sale."""
    data = trending_market()
    result = run_backtest(strategy_with(exit_rule={"signal": "close < 0.01"}), data)

    trades = result.trades
    assert trades
    assert trades[-1].is_open
    assert trades[-1].exit_date is None
    assert trades[-1].exit_price is None


def test_trade_dates_come_from_the_price_index() -> None:
    data = trending_market()
    result = run_backtest(strategy_with(), data)

    dates = {bar.date() for bar in data.index}
    for trade in result.trades:
        assert trade.entry_date in dates
        if trade.exit_date is not None:
            assert trade.exit_date in dates


def test_extract_trades_on_an_empty_record_set_returns_nothing() -> None:
    from cracktrade.backtest.runner import run_simulation

    data = trending_market()
    simulation = run_simulation(strategy_with(entry={"signal": "close > 1000000"}), data)

    assert extract_trades(simulation.portfolio, data.index) == ()


# ------------------------------------------------------------------ B7: honest reporting


def test_a_thin_result_is_flagged_as_unjudgeable() -> None:
    """Audit B7: a point estimate off six trades implies a confidence it does not have."""
    data = trending_market()
    result = run_backtest(strategy_with(exit_rule={"max_holding_days": 700}), data)

    if result.metrics.total_trades < MIN_TRADES_TO_JUDGE:
        assert not result.metrics.has_enough_trades_to_judge


def test_a_rich_result_is_judgeable() -> None:
    metrics = result_for().metrics

    assert metrics.total_trades >= MIN_TRADES_TO_JUDGE
    assert metrics.has_enough_trades_to_judge


def test_the_definedness_of_each_signal_reaches_the_result() -> None:
    result = result_for()

    assert result.entry_defined_pct == pytest.approx(100.0)
    assert result.exit_defined_pct == pytest.approx(100.0)


def test_an_undefined_heavy_signal_is_visible_in_the_result() -> None:
    """psar_psarl holds a value only while the trend is up; the report must say so."""
    result = run_backtest(
        strategy_with(
            indicators=[{"name": "sar", "type": "psar"}],
            entry={"signal": "close > sar_psarl"},
            exit_rule={"signal": "close < sar_psars"},
        ),
        trending_market(),
    )

    assert result.entry_defined_pct < 90.0


# ------------------------------------------------------------------------ stop reporting


def test_the_active_stop_and_its_shadows_reach_the_result() -> None:
    result = run_backtest(
        strategy_with(
            exit_rule={"atr_stop_multiplier": 2.0, "stop_loss_pct": 5.0},
        ),
        trending_market(),
    )

    assert result.active_stop == "atr"
    assert result.shadowed_stops == ("stop_loss_pct",)


def test_metrics_are_extractable_without_the_result_wrapper() -> None:
    """The extraction layer is usable directly; Phase 8 calls it per candidate."""
    from cracktrade.backtest.runner import run_simulation

    simulation = run_simulation(strategy_with(), trending_market())

    metrics = extract_metrics(simulation.portfolio, risk_free_rate=0.04)

    assert metrics.total_trades > 0


# ------------------------------------------------------------------------ rendering


def test_the_report_renders_without_crashing() -> None:
    """Formatting bugs surface only at print time, so the render path gets exercised."""
    from rich.console import Console

    from cracktrade.cli.render import render_result

    console = Console(file=io.StringIO(), width=100, force_terminal=False)
    render_result(result_for(), console)
    output = console.file.getvalue()  # type: ignore[attr-defined]

    assert "Buy & hold" in output
    assert "Return by year" in output
    assert "Sharpe" in output


def test_the_report_leads_with_the_benchmark_verdict() -> None:
    """Spec 12.8: a report that buries the benchmark is how a backtest misleads."""
    from rich.console import Console

    from cracktrade.cli.render import render_result

    console = Console(file=io.StringIO(), width=100, force_terminal=False)
    result = result_for()
    render_result(result, console)
    lines = console.file.getvalue().splitlines()  # type: ignore[attr-defined]

    verdict = "BEATS" if result.benchmark.beats_buy_and_hold else "LOSES TO"
    header = "\n".join(lines[:3])
    assert verdict in header
    assert "buy-and-hold" in header


def test_an_infinite_profit_factor_renders() -> None:
    from cracktrade.cli.render import _ratio

    assert _ratio(float("inf")) == "∞"
    assert _ratio(2.5) == "2.50"


def test_a_thin_or_undefined_result_is_warned_about_in_the_report() -> None:
    from rich.console import Console

    from cracktrade.cli.render import render_result

    console = Console(file=io.StringIO(), width=120, force_terminal=False)
    render_result(
        run_backtest(
            strategy_with(
                indicators=[{"name": "sar", "type": "psar"}],
                entry={"signal": "close > sar_psarl"},
                exit_rule={"signal": "close < sar_psars"},
            ),
            trending_market(),
        ),
        console,
    )
    output = console.file.getvalue()  # type: ignore[attr-defined]

    assert "undefined on" in output
