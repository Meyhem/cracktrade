"""Phase 3: annualisation, forced session closes, and intraday timestamps.

The two things worth being suspicious of in this phase are both silent failures. A wrong
annualisation basis does not raise -- it prints a plausible Sharpe that is off by a factor of
four. A forced close computed from "no later bar exists today" does not raise either -- it
produces a backtest that quietly knew when the session would end. Both are pinned here against
analytic expectations and against the truncation harness respectively, never by eye.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from cracktrade.backtest import (
    Calendar,
    buy_and_hold_portfolio,
    run_backtest,
    run_simulation,
    session_rules,
)
from cracktrade.backtest.calendar import DAILY
from cracktrade.backtest.metrics import per_period_risk_free
from cracktrade.backtest.portfolio import require_interval
from cracktrade.backtest.sessionclose import LEARNING_SESSIONS, count_overnight_carries
from cracktrade.config import Interval, Strategy, parse_strategy
from cracktrade.data import MarketData, StaticProvider, load_history, session_ids
from cracktrade.errors import BacktestError
from cracktrade.settings import TRADING_DAYS_PER_YEAR
from tests.intraday import load_fixture

AFTER_CAPTURE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def strategy(
    interval: str = "30m",
    ticker: str = "SAP.DE",
    start: str = "2026-07-01",
    end: str = "2026-08-18",
    entry: str = "close > open",
    **exit_rule: Any,
) -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "intraday"},
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
            "entry": {"signal": entry},
            "exit": dict({"max_holding_bars": 30}, **exit_rule),
        }
    )


def provider() -> StaticProvider:
    return StaticProvider(
        by_interval={
            ("SAP.DE", Interval.M30): load_fixture("sap_de_30m"),
            ("SAP.DE", Interval.H1): load_fixture("sap_de_1h"),
            ("VOD.L", Interval.H1): load_fixture("vod_l_1h_halfdays"),
            ("SPY", Interval.M30): load_fixture("spy_30m"),
        }
    )


def market(interval: str = "30m", **kwargs: Any) -> MarketData:
    return load_history(strategy(interval, **kwargs), provider(), now=AFTER_CAPTURE)


# ---------------------------------------------------------------- the calendar


def test_a_xetra_calendar_is_measured_not_assumed() -> None:
    """252 sessions x 17 half-hour bars. The 17 comes from the data, never from a constant."""
    calendar = Calendar.of(market("30m"))
    assert calendar.bars_per_session == 17
    assert calendar.periods_per_year == pytest.approx(252 * 17)
    assert calendar.freq == "30min"


def test_the_same_interval_gives_a_different_calendar_on_a_different_venue() -> None:
    """The number is a property of the exchange, not of the interval.

    This is the assertion that fails if anyone reintroduces a bars-per-day constant: no single
    value satisfies both a 6.5-hour New York session and an 8.5-hour Xetra one.
    """
    xetra = Calendar.of(market("30m"))
    new_york = Calendar.of(
        load_history(
            strategy("30m", ticker="SPY", start="2026-07-01", end="2026-08-18"),
            provider(),
            now=AFTER_CAPTURE,
        )
    )
    assert xetra.bars_per_session == 17
    assert new_york.bars_per_session == 13
    assert xetra.periods_per_year != new_york.periods_per_year


def test_the_daily_calendar_is_the_object_it_always_was() -> None:
    """Not merely equal to the old constants -- identical, so daily numbers cannot shift."""
    assert DAILY.freq == "1D"
    assert DAILY.periods_per_year == float(TRADING_DAYS_PER_YEAR)
    assert DAILY.year_freq == pd.Timedelta("252 days")


def test_year_freq_is_a_trading_year_not_a_calendar_year() -> None:
    """vectorbt divides ``year_freq`` by ``freq`` to get the annualisation factor.

    So it has to be a year of *trading time*: 4284 half-hours, which is about 89 calendar days.
    Handing it a calendar year is audit finding A2 in a new costume.
    """
    calendar = Calendar.of(market("30m"))
    assert calendar.year_freq / pd.Timedelta(calendar.freq) == pytest.approx(252 * 17)


def test_a_synthetic_intraday_series_annualises_to_the_analytic_figure() -> None:
    """Constructed so the right answer is known in closed form, then checked against it.

    A constant per-bar return of r over a full year of bars compounds to (1+r)**periods - 1.
    Eyeballing "that looks about right" is exactly how a factor-of-four error survives.
    """
    data = market("30m")
    calendar = Calendar.of(data)
    per_bar = 0.0001
    returns = pd.Series(per_bar, index=data.index)
    annualised = returns.vbt.returns(  # type: ignore[attr-defined]
        freq=calendar.freq, year_freq=calendar.year_freq
    ).annualized()

    expected = (1.0 + per_bar) ** calendar.periods_per_year - 1.0
    assert float(annualised) == pytest.approx(expected, rel=1e-6)


def test_the_risk_free_rate_is_charged_per_bar_not_per_day() -> None:
    """Compounding the per-period rate over a year of bars must return the annual rate.

    At the daily exponent a 30-minute strategy would be charged a full year of risk-free
    return against every seventeen bars -- the direction of defect D4, one interval down.
    """
    calendar = Calendar.of(market("30m"))
    annual = 0.04
    per_period = per_period_risk_free(annual, calendar)

    assert (1.0 + per_period) ** calendar.periods_per_year - 1.0 == pytest.approx(annual)
    assert per_period < per_period_risk_free(annual, DAILY)


def test_worst_rolling_twelve_months_is_absent_rather_than_zero_on_thin_history() -> None:
    """A year is 4284 half-hour bars and the provider serves at most 60 days of them.

    The 0.0 means "not measurable", and the UI has to say so rather than plot a flat line
    implying the strategy never had a losing year.
    """
    result = run_backtest(strategy("30m"), market("30m"))
    assert result.metrics.worst_rolling_12m_pct == 0.0
    assert result.metrics.bars < 252 * 17
    # The flag is what makes the two zeros tellable apart. Without it the CLI printed
    # "+0.00%" -- "never lost money over any twelve months", off seven weeks of history.
    assert result.metrics.worst_rolling_12m_measurable is False


def test_a_renderer_shows_an_unmeasurable_rolling_year_as_absent() -> None:
    """The CLI is a client and was breaking the rule the engine states for clients."""
    from cracktrade.cli.render import _rolling_year

    metrics = run_backtest(strategy("30m"), market("30m")).metrics
    assert _rolling_year(metrics) == "—"


def test_a_measurable_rolling_year_is_still_printed() -> None:
    from cracktrade.cli.render import _rolling_year
    from cracktrade.data import prepare_frame
    from tests.factories import make_ohlcv

    data = MarketData(
        ticker="TEST",
        frame=prepare_frame(make_ohlcv(600), ticker="TEST", now=date(2100, 1, 1)),
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )
    daily = parse_strategy(
        {
            "strategy": {"name": "d"},
            "universe": {
                "ticker": "TEST",
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": {"max_holding_days": 5},
        }
    )
    metrics = run_backtest(daily, data).metrics
    assert metrics.worst_rolling_12m_measurable is True
    assert _rolling_year(metrics).endswith("%")


# ------------------------------------------------------------- the interval gate


def test_intraday_bars_under_a_daily_declaration_are_refused() -> None:
    """The check that stands between the user and a Sharpe seventeen times too large."""
    data = market("30m")
    mislabelled = MarketData(
        ticker=data.ticker,
        frame=data.frame,
        requested_start=data.requested_start,
        requested_end=data.requested_end,
        interval=Interval.D1,
    )
    with pytest.raises(BacktestError, match="median bar spacing"):
        require_interval(mislabelled)


def test_hourly_bars_under_a_thirty_minute_declaration_are_refused() -> None:
    """Adjacent intervals differ by a factor of two, well outside the spacing tolerance."""
    data = market("1h", start="2026-06-01", end="2026-08-18")
    mislabelled = MarketData(
        ticker=data.ticker,
        frame=data.frame,
        requested_start=data.requested_start,
        requested_end=data.requested_end,
        interval=Interval.M30,
    )
    with pytest.raises(BacktestError, match="30m"):
        require_interval(mislabelled)


def test_overnight_gaps_do_not_trip_the_spacing_check() -> None:
    """The median is the within-session spacing; the nightly 15-hour holes are a minority."""
    require_interval(market("30m"))
    require_interval(market("1h", start="2026-06-01", end="2026-08-18"))


# --------------------------------------------------------- the forced session close


def test_no_position_is_held_overnight() -> None:
    """The constraint the trader asked for, asserted on realised trades rather than on masks."""
    data = market("30m")
    result = run_backtest(strategy("30m", entry="close > open"), data)
    sessions = session_ids(data.index)
    stamps = {stamp: position for position, stamp in enumerate(data.index)}

    assert result.trades
    for trade in result.trades:
        if trade.exit_date is None:
            continue
        entry = sessions[stamps[pd.Timestamp(trade.entry_date)]]
        exit_ = sessions[stamps[pd.Timestamp(trade.exit_date)]]
        assert entry == exit_, f"trade {trade.entry_date} -> {trade.exit_date} crossed a session"


def test_a_healthy_intraday_run_reports_no_overnight_carries() -> None:
    result = run_backtest(strategy("30m"), market("30m"))
    assert result.overnight_carries == 0


def test_a_daily_run_reports_no_carries_and_no_session_rules() -> None:
    """Holding overnight is the entire point of a daily strategy."""
    from cracktrade.data import prepare_frame
    from tests.factories import make_ohlcv

    frame = prepare_frame(make_ohlcv(200), ticker="TEST", now=date(2100, 1, 1))
    data = MarketData(
        ticker="TEST",
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )
    daily = parse_strategy(
        {
            "strategy": {"name": "d"},
            "universe": {
                "ticker": "TEST",
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": {"max_holding_days": 5},
        }
    )
    simulation = run_simulation(daily, data)
    assert simulation.sessions is None
    assert run_backtest(daily, data).overnight_carries == 0


def test_entries_are_suppressed_on_the_session_close_bar() -> None:
    """An entry there fills at that bar's open and is closed on the same bar, or carried."""
    data = market("30m")
    simulation = run_simulation(strategy("30m"), data)
    assert simulation.sessions is not None
    suppressed = simulation.sessions.suppressed_entries
    assert not bool((simulation.entries.to_numpy() & suppressed).any())


def test_the_learned_close_lands_on_the_session_s_last_bar() -> None:
    """17:00 on Xetra, learned from earlier sessions rather than read off this one."""
    from cracktrade.backtest.sessionclose import _at_learned_close

    index = market("30m").index
    closes, _ = _at_learned_close(index)

    assert int(closes.sum()) > 20
    assert {stamp.strftime("%H:%M") for stamp in index[closes]} == {"17:00"}


def test_no_position_is_opened_in_a_session_whose_close_is_unknown() -> None:
    """The first session has no earlier session to learn from, so it is not traded.

    Letting a strategy open a position the engine cannot promise to close would breach the
    trader's constraint on bar one of every intraday run -- and would leave the overnight-carry
    counter permanently at one, which makes it useless as a signal.
    """
    from cracktrade.backtest.sessionclose import _at_learned_close

    data = market("30m")
    _, unlearned = _at_learned_close(data.index)
    sessions = session_ids(data.index)

    assert set(sessions[unlearned]) == {0}
    assert int(unlearned.sum()) == 17


def test_a_half_day_produces_a_visible_overnight_carry() -> None:
    """LSE trades a four-bar Dec 24 while the learned close says 16:00.

    The learned time is *wrong* here by construction -- taking the latest close among recent
    sessions means a half-day never matches it. The engine says a position carried overnight,
    because one did. Guessing a shorter close to make the number nicer would be inventing a
    calendar it does not have.
    """
    data = load_history(
        strategy("1h", ticker="VOD.L", start="2024-12-02", end="2025-01-31"),
        provider(),
        now=AFTER_CAPTURE,
    )
    result = run_backtest(
        strategy("1h", ticker="VOD.L", start="2024-12-02", end="2025-01-31", entry="close > open"),
        data,
    )
    assert result.overnight_carries >= 1


def test_a_forced_exit_overrides_the_minimum_holding_period() -> None:
    """The interaction that would otherwise carry a position overnight, silently.

    ``min_holding_bars`` works by suppressing exits. Folding the forced close into the ordinary
    exit series would let that suppression swallow it -- and only on the strategies that asked
    to hold for a while, which is the worst possible population to break.
    """
    data = market("30m")
    result = run_backtest(strategy("30m", min_holding_bars=10, max_holding_bars=30), data)
    sessions = session_ids(data.index)
    stamps = {stamp: position for position, stamp in enumerate(data.index)}

    assert result.trades
    for trade in result.trades:
        if trade.exit_date is None:
            continue
        assert (
            sessions[stamps[pd.Timestamp(trade.entry_date)]]
            == (sessions[stamps[pd.Timestamp(trade.exit_date)]])
        )


def test_a_minimum_holding_period_longer_than_a_session_is_refused() -> None:
    """No position could ever reach it, so the strategy simulated is not the one written."""
    with pytest.raises(BacktestError, match="17"):
        run_backtest(strategy("30m", min_holding_bars=17, max_holding_bars=40), market("30m"))


def test_a_minimum_holding_period_that_fits_a_session_is_allowed() -> None:
    run_backtest(strategy("30m", min_holding_bars=8, max_holding_bars=16), market("30m"))


def test_an_exit_signal_colliding_with_the_forced_close_exits_exactly_once() -> None:
    """The signal function returns one decision per bar, so vectorbt's conflict resolution --
    and the ``signals.clean`` quirk that deletes a colliding exit -- is never reached. Pinned
    rather than assumed, because the quirk is a library behaviour and libraries change.
    """
    data = market("30m")
    result = run_backtest(strategy("30m", signal="close > 0", max_holding_bars=30), data)
    entries = [trade.entry_date for trade in result.trades]
    assert len(entries) == len(set(entries))


def test_session_rules_tolerate_an_empty_index() -> None:
    rules = session_rules(pd.DatetimeIndex([]))
    assert len(rules.forced_exits) == 0
    assert len(rules.suppressed_entries) == 0


def test_counting_carries_on_no_trades_is_zero() -> None:
    data = market("30m")
    empty = np.zeros(0, dtype=np.int64)
    assert count_overnight_carries(data.index, empty, empty) == 0


def test_the_learning_window_is_documented_as_a_constant() -> None:
    """Small enough to follow a venue that moves its hours, large enough to ignore one oddity."""
    assert 2 <= LEARNING_SESSIONS <= 20


# ----------------------------------------------------------------- causality


@pytest.mark.causality
def test_session_rules_are_truncation_equivalent() -> None:
    """The heart of it: "the session's last bar" must not be read off later bars.

    A naive implementation -- "no later bar exists on this date" -- is a fact about the future
    of bar t. Truncate mid-session and every truncation point becomes a phantom close, so the
    same bar gets a different decision depending on how much history follows it.

    Truncation points are chosen to land mid-session, on a learned close bar, and on a
    session's first bar, because those are the three positions where a look-ahead
    implementation differs from a causal one.
    """
    data = market("30m")
    index = data.index
    rules = session_rules(index)
    opens = np.flatnonzero(
        np.concatenate(
            [[True], index.normalize().to_numpy()[1:] != index.normalize().to_numpy()[:-1]]
        )
    )

    probes: list[int] = []
    for session_open in opens[3:12]:
        probes.extend([int(session_open), int(session_open) + 8, int(session_open) + 16])
    probes = [position for position in probes if position < len(index)]
    assert probes

    for at in probes:
        prefix = session_rules(index[: at + 1])
        assert prefix.forced_exits[at] == rules.forced_exits[at], f"forced exit moved at bar {at}"
        assert prefix.suppressed_entries[at] == rules.suppressed_entries[at], (
            f"entry suppression moved at bar {at}"
        )


@pytest.mark.causality
def test_the_whole_session_mask_is_a_prefix_of_itself() -> None:
    """Stronger than per-bar equality: every earlier value must be untouched too."""
    data = market("30m")
    index = data.index
    full = session_rules(index)

    for at in (40, 137, 300, 480):
        if at >= len(index):
            continue
        prefix = session_rules(index[:at])
        np.testing.assert_array_equal(prefix.forced_exits, full.forced_exits[:at])
        np.testing.assert_array_equal(prefix.suppressed_entries, full.suppressed_entries[:at])


@pytest.mark.causality
def test_an_intraday_backtest_is_truncation_equivalent() -> None:
    """End to end: the same bars must produce the same trades however much history follows."""
    data = market("30m")
    at = 400
    full = run_backtest(strategy("30m"), data)
    prefix = run_backtest(strategy("30m"), data.head(at))

    shared = [trade for trade in full.trades if not trade.is_open]
    truncated = [trade for trade in prefix.trades if not trade.is_open]
    overlap = min(len(shared), len(truncated))
    assert overlap > 3

    for original, on_prefix in zip(shared[:overlap], truncated[:overlap], strict=True):
        assert original.entry_date == on_prefix.entry_date
        assert original.exit_date == on_prefix.exit_date
        assert original.pnl == on_prefix.pnl


# ------------------------------------------------------------------ timestamps


def test_intraday_trades_carry_the_time_of_day() -> None:
    """Two trades in one Xetra session would otherwise report the same moment."""
    result = run_backtest(strategy("30m"), market("30m"))
    assert result.trades
    stamps = [trade.entry_date for trade in result.trades]
    assert all(isinstance(stamp, datetime) for stamp in stamps)
    assert len({stamp.strftime("%H:%M") for stamp in stamps}) > 1


def test_intraday_trade_timestamps_serialise_with_their_time() -> None:
    from cracktrade.serialize import to_dict

    result = run_backtest(strategy("30m"), market("30m"))
    payload = to_dict(result.trades[0])
    assert isinstance(payload, dict)
    assert "T" in str(payload["entry_date"])


def test_holding_is_reported_in_bars() -> None:
    """ "Avg holding: 5" meaning five half-hours must not read as five days."""
    result = run_backtest(strategy("30m"), market("30m"))
    assert result.metrics.avg_holding_bars > 0
    assert all(trade.holding_bars >= 0 for trade in result.trades)


# ------------------------------------------------------- captured series


def test_a_captured_intraday_series_keeps_the_time_of_day() -> None:
    """Dropping it would stack a whole session on one x-value.

    Seventeen Xetra half-hours drawn at midnight is not a curve -- it is a vertical line
    repeated once per session, on a chart sitting directly beneath a trade list that does show
    the times. Found by looking at what the series endpoint actually served, not by reading the
    capture code.
    """
    from cracktrade.backtest.series import capture

    data = market()
    simulation = run_simulation(strategy(), data)
    series = capture(
        portfolio=simulation.portfolio,
        benchmark=buy_and_hold_portfolio(data, strategy().execution, None, start_bar=0),
        data=data,
    )

    stamps = series.close.dates
    assert any(isinstance(stamp, datetime) and stamp.hour != 0 for stamp in stamps)
    assert len({str(stamp) for stamp in stamps}) == len(stamps), "no two bars share a timestamp"


def test_a_captured_daily_series_is_still_dates() -> None:
    """Unchanged, and a midnight suffix on every daily chart label would be pure noise."""
    from cracktrade.backtest.series import capture
    from cracktrade.data import prepare_frame
    from tests.factories import make_ohlcv

    data = MarketData(
        ticker="TEST",
        frame=prepare_frame(make_ohlcv(200), ticker="TEST", now=date(2100, 1, 1)),
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )
    daily = parse_strategy(
        {
            "strategy": {"name": "d"},
            "universe": {
                "ticker": "TEST",
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "entry": {"signal": "close > open"},
            "exit": {"max_holding_days": 5},
        }
    )
    series = capture(
        portfolio=run_simulation(daily, data).portfolio,
        benchmark=buy_and_hold_portfolio(data, daily.execution, None, start_bar=0),
        data=data,
    )

    assert all(not isinstance(stamp, datetime) for stamp in series.close.dates)
