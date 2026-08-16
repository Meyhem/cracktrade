"""Phase 6: the backtest engine (spec section 7).

Several tests here assert the behaviour of *vectorbt* rather than of this codebase. That is
deliberate. Spec section 2.6 fixes three conventions -- stop-loss wins a tied bar, a gap fills
at the open, NaN means no stop -- which the engine delegates to the library. Delegating them is
only safe if a library upgrade that changes them fails a test instead of quietly changing
everyone's backtest.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest

from cracktrade.backtest import (
    ATR_WINDOW,
    atr_stop_series,
    build_stops,
    expand_variants,
    require_daily_bars,
    run_variants,
)
from cracktrade.backtest.portfolio import _resolve_size
from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData
from cracktrade.errors import BacktestError
from cracktrade.indicators import registry
from cracktrade.indicators.compute import registry_installed
from cracktrade.signals.alignment import causal_shift

TICKER = "TEST"


type Path = list[float] | npt.NDArray[np.floating[Any]]


def make_frame(
    close: Path,
    *,
    open_: Path | None = None,
    high: Path | None = None,
    low: Path | None = None,
    freq: str = "B",
) -> pd.DataFrame:
    """An OHLCV frame from an explicit close path, with sane bracketing defaults."""
    closes = np.asarray(close, dtype=np.float64)
    opens = closes if open_ is None else np.asarray(open_, dtype=np.float64)
    highs = np.maximum(opens, closes) + 0.5 if high is None else np.asarray(high, dtype=np.float64)
    lows = np.minimum(opens, closes) - 0.5 if low is None else np.asarray(low, dtype=np.float64)
    index = pd.DatetimeIndex(
        pd.date_range("2024-01-01", periods=len(closes), freq=freq).to_numpy(), name="Date"
    )
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(len(closes), 1_000_000.0),
        },
        index=index,
    )


def market(frame: pd.DataFrame) -> MarketData:
    return MarketData(
        ticker=TICKER,
        frame=frame,
        requested_start=date(2024, 1, 1),
        requested_end=date(2030, 1, 1),
    )


def strategy_with(
    *,
    entry: str = "close > 30",
    exits: list[dict[str, Any]] | None = None,
    indicators: list[dict[str, Any]] | None = None,
    entries: list[dict[str, Any]] | None = None,
    sizing: dict[str, Any] | None = None,
) -> Strategy:
    config: dict[str, Any] = {
        "strategy": {"name": "s"},
        "universe": {
            "ticker": TICKER,
            "start_date": "2024-01-01",
            "end_date": "2030-01-01",
        },
        "execution": {
            "initial_capital": 10_000.0,
            "commission_pct": 0.0,
            "slippage_pct": 0.0,
        },
        "indicators": indicators or [],
        "entry_variants": entries or [{"name": "e", "signal": entry}],
        "exit_variants": exits or [{"name": "x", "max_holding_days": 500}],
    }
    if sizing is not None:
        config["position_sizing"] = sizing
    return parse_strategy(config)


def trades(simulation: Any) -> pd.DataFrame:
    records: pd.DataFrame = simulation.portfolio.trades.records
    return records


# ------------------------------------------------------ conformance 1: next-open execution


def test_a_signal_at_bar_20_enters_at_bar_21() -> None:
    """The canonical next-open test, ported verbatim from ``tests/test_execution.py:69``.

    The close jumps at index 20 so that ``close > 30`` is true only from there. The decision is
    taken at the close of bar 20 and must be acted on at the *open of bar 21* -- never at the
    close of 20, which is the price that produced the signal.
    """
    close = np.full(25, 20.0)
    close[20:] = 40.0
    data = market(make_frame(close))

    simulation = run_variants(strategy_with(), data)[0]

    record = trades(simulation).iloc[0]
    assert int(record["entry_idx"]) == 21
    assert float(record["entry_price"]) == float(data.frame["Open"].iloc[21])


def test_the_entry_never_fills_at_the_bar_that_produced_it() -> None:
    close = np.full(25, 20.0)
    close[20:] = 40.0
    data = market(make_frame(close))

    simulation = run_variants(strategy_with(), data)[0]

    entry_bar = int(trades(simulation).iloc[0]["entry_idx"])
    signalling_bar = 20
    assert entry_bar > signalling_bar


# ---------------------------------------------- section 2.6: the pessimistic conventions


def touch_both_stops_frame() -> pd.DataFrame:
    """Bar 2 ranges 80..130, touching both a 10% stop-loss (90) and a 10% take-profit (110)."""
    return make_frame(
        close=[100, 100, 100, 100],
        open_=[100, 100, 100, 100],
        high=[101, 101, 130, 101],
        low=[99, 99, 80, 99],
    )


def entry_at_bar_one() -> pd.Series:
    frame = touch_both_stops_frame()
    return pd.Series([False, True, False, False], index=frame.index)


def test_a_bar_touching_both_stops_exits_at_the_stop_loss() -> None:
    """Spec 2.6: a daily bar does not record intrabar order, so assume the worse outcome."""
    data = market(touch_both_stops_frame())
    strategy = strategy_with(
        entry="close > 99",
        exits=[{"name": "x", "stop_loss_pct": 10.0, "take_profit_pct": 10.0}],
    )

    simulation = run_variants(strategy, data)[0]

    record = trades(simulation).iloc[0]
    assert float(record["exit_price"]) == pytest.approx(90.0)
    assert float(record["pnl"]) < 0


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop_level() -> None:
    """Spec 2.6: the stop level was never reachable, so filling there would be fiction."""
    frame = make_frame(
        close=[100, 100, 70, 70],
        open_=[100, 100, 70, 70],
        high=[101, 101, 72, 72],
        low=[99, 99, 68, 68],
    )
    data = market(frame)
    strategy = strategy_with(
        entry="close > 99",
        exits=[{"name": "x", "stop_loss_pct": 10.0}],
    )

    simulation = run_variants(strategy, data)[0]

    record = trades(simulation).iloc[0]
    assert float(record["exit_price"]) == pytest.approx(70.0)
    assert float(record["exit_price"]) != pytest.approx(90.0)


# ------------------------------------------------------------------- D7: NaN is not zero


def test_a_nan_stop_distance_means_no_stop() -> None:
    """Defect D7. Legacy's ``.fillna(0)`` made this a stop at the entry price instead."""
    import vectorbt as vbt

    frame = make_frame(close=[100.0] * 4, open_=[100.0] * 4)
    entries = pd.Series([False, True, False, False], index=frame.index)
    exits = pd.Series(False, index=frame.index)

    common = {
        "close": frame["Close"],
        "entries": entries,
        "exits": exits,
        "price": frame["Open"],
        "init_cash": 10_000.0,
        "direction": "longonly",
        "freq": "1D",
    }
    with_nan = vbt.Portfolio.from_signals(**common, sl_stop=np.nan)
    with_zero = vbt.Portfolio.from_signals(**common, sl_stop=0.0)

    # status 0 == open, 1 == closed
    assert int(with_nan.trades.records.iloc[0]["status"]) == 0, "NaN must mean no stop"
    assert int(with_zero.trades.records.iloc[0]["status"]) == 1, "0.0 is a stop at entry price"


# -------------------------------------------------------- D6: ATR through the registry


def test_the_atr_stop_matches_a_registry_computed_atr_exactly() -> None:
    """Defect D6. Legacy called ``ta.atr`` directly, bypassing the registry's guarantees."""
    data = market(make_frame(np.linspace(100, 140, 80)))
    multiplier = 2.5

    produced = atr_stop_series(data, multiplier)

    registry_installed()
    spec = registry.get("atr")
    params = spec.params.model_validate({"window": ATR_WINDOW})
    reference = spec.compute(
        {
            "open": data.frame["Open"],
            "high": data.frame["High"],
            "low": data.frame["Low"],
            "close": data.frame["Close"],
            "volume": data.frame["Volume"],
            "source": data.frame["Close"],
        },
        params,
    )
    assert isinstance(reference, pd.Series)
    expected = causal_shift(
        pd.Series(np.asarray(reference, dtype=np.float64), index=data.index)
        * multiplier
        / data.frame["Close"],
        1,
    )

    pd.testing.assert_series_equal(produced, expected, check_names=False)


def test_the_atr_stop_is_nan_during_warmup_not_zero() -> None:
    """A zero there would be a stop at the entry price on every early trade (D7)."""
    data = market(make_frame(np.linspace(100, 140, 80)))

    stop = atr_stop_series(data, 2.0)

    assert bool(stop.iloc[:ATR_WINDOW].isna().all())
    assert not bool((stop.fillna(1.0) == 0.0).any())


def test_the_atr_stop_uses_only_past_bars() -> None:
    """Truncation equivalence for the stop series, same standard as every indicator."""
    full = market(make_frame(np.linspace(100, 140, 120)))
    at = 90

    produced_full = atr_stop_series(full, 2.0).iloc[at]
    produced_truncated = atr_stop_series(full.head(at + 1), 2.0).iloc[at]

    assert produced_full == produced_truncated


# ------------------------------------------------ D8: holding rules bind realised positions


def oscillating_market(bars: int = 120) -> MarketData:
    """A saw-tooth close, so entry and exit conditions alternate frequently."""
    steps = np.arange(bars, dtype=np.float64)
    close = 100.0 + 10.0 * np.sin(steps / 3.0)
    return market(make_frame(close))


def test_no_realised_position_closes_before_min_holding_days() -> None:
    """Defect D8. Legacy keyed the suppression off entry *signals*, including ignored ones."""
    data = oscillating_market()
    strategy = strategy_with(
        entry="close > 100",
        exits=[{"name": "x", "signal": "close < 100", "min_holding_days": 6}],
    )

    simulation = run_variants(strategy, data)[0]

    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    assert not closed.empty, "expected some closed trades"
    held = closed["exit_idx"] - closed["entry_idx"]
    assert int(held.min()) >= 6


def test_max_holding_days_forces_an_exit_that_many_bars_after_a_realised_entry() -> None:
    data = oscillating_market()
    strategy = strategy_with(
        entry="close > 100",
        exits=[{"name": "x", "max_holding_days": 4}],
    )

    simulation = run_variants(strategy, data)[0]

    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    assert not closed.empty
    held = closed["exit_idx"] - closed["entry_idx"]
    assert int(held.max()) <= 4


def test_a_stop_still_fires_inside_the_minimum_holding_window() -> None:
    """The decision recorded in spec 7.2: a stop disabled for N days is not a stop.

    A long minimum hold plus a falling market must still produce a stopped-out trade shorter
    than the minimum, because the stop outranks the holding rule.
    """
    close = np.concatenate([np.full(5, 100.0), np.linspace(100.0, 40.0, 40)])
    data = market(make_frame(close))
    strategy = strategy_with(
        entry="close > 99",
        exits=[{"name": "x", "stop_loss_pct": 3.0, "min_holding_days": 20}],
    )

    simulation = run_variants(strategy, data)[0]

    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    assert not closed.empty, "the stop must have closed a position"
    held = closed["exit_idx"] - closed["entry_idx"]
    assert int(held.min()) < 20, "the stop must outrank min_holding_days"


# ------------------------------------------------------------------ variants and sizing


def test_every_entry_is_crossed_with_every_exit() -> None:
    strategy = strategy_with(
        entries=[
            {"name": "e1", "signal": "close > 30"},
            {"name": "e2", "signal": "close > 40"},
            {"name": "e3", "signal": "close > 50"},
        ],
        exits=[
            {"name": "x1", "max_holding_days": 5},
            {"name": "x2", "stop_loss_pct": 2.0},
        ],
    )

    pairs = expand_variants(strategy)

    assert len(pairs) == 6
    assert {pair.label for pair in pairs} == {
        f"{entry} / {exit_}" for entry in ("e1", "e2", "e3") for exit_ in ("x1", "x2")
    }


def test_each_variant_pair_is_simulated_independently() -> None:
    data = oscillating_market()
    strategy = strategy_with(
        entries=[{"name": "e1", "signal": "close > 100"}, {"name": "e2", "signal": "close > 105"}],
        exits=[{"name": "x1", "max_holding_days": 5}, {"name": "x2", "max_holding_days": 20}],
    )

    simulations = run_variants(strategy, data)

    assert len(simulations) == 4
    assert len({simulation.label for simulation in simulations}) == 4


@pytest.mark.parametrize(
    ("sizing", "expected"),
    [
        (None, (np.inf, "amount")),
        ({"type": "fixed_pct", "value": 50.0}, (0.5, "percent")),
        ({"type": "fixed_cash", "value": 2500.0}, (2500.0, "value")),
        ({"type": "fixed_shares", "value": 10.0}, (10.0, "amount")),
    ],
)
def test_position_sizing_maps_onto_vectorbt(
    sizing: dict[str, Any] | None, expected: tuple[float, str]
) -> None:
    strategy = strategy_with(sizing=sizing)

    assert _resolve_size(strategy.position_sizing) == expected


# ------------------------------------------------------------ stops and the priority chain


@pytest.mark.parametrize(
    ("fields", "active", "trailing"),
    [
        ({"stop_loss_pct": 5.0}, "fixed", False),
        ({"trailing_stop_pct": 5.0}, "trailing", True),
        ({"atr_stop_multiplier": 2.0}, "atr", False),
        ({"atr_stop_multiplier": 2.0, "trailing_stop_pct": 5.0}, "atr", False),
        ({"trailing_stop_pct": 5.0, "stop_loss_pct": 3.0}, "trailing", True),
    ],
)
def test_the_stop_priority_chain_selects_one_stop(
    fields: dict[str, float], active: str, trailing: bool
) -> None:
    data = market(make_frame(np.linspace(100, 140, 60)))
    variant = strategy_with(exits=[{"name": "x", **fields}]).exit_variants[0]

    stops = build_stops(variant, data)

    assert stops.active_stop == active
    assert stops.sl_trail is trailing


def test_shadowed_stops_are_reported_not_silently_dropped() -> None:
    data = market(make_frame(np.linspace(100, 140, 60)))
    variant = strategy_with(
        exits=[{"name": "x", "atr_stop_multiplier": 2.0, "stop_loss_pct": 5.0}]
    ).exit_variants[0]

    stops = build_stops(variant, data)

    assert stops.shadowed_stops == ("stop_loss_pct",)


def test_a_variant_with_no_stop_has_no_stop_distance() -> None:
    data = market(make_frame(np.linspace(100, 140, 60)))
    variant = strategy_with(exits=[{"name": "x", "max_holding_days": 5}]).exit_variants[0]

    stops = build_stops(variant, data)

    assert stops.active_stop is None
    assert not stops.has_stop


def test_percentages_become_fractions() -> None:
    data = market(make_frame(np.linspace(100, 140, 60)))
    variant = strategy_with(
        exits=[{"name": "x", "stop_loss_pct": 7.5, "take_profit_pct": 20.0}]
    ).exit_variants[0]

    stops = build_stops(variant, data)

    assert stops.sl_stop == pytest.approx(0.075)
    assert stops.tp_stop == pytest.approx(0.20)


# --------------------------------------------------------------------- D14: daily bars only


def test_a_weekly_index_is_rejected() -> None:
    data = market(make_frame(np.linspace(100, 140, 40), freq="W"))

    with pytest.raises(BacktestError, match="not daily"):
        require_daily_bars(data)


def test_an_intraday_index_is_rejected() -> None:
    data = market(make_frame(np.linspace(100, 140, 40), freq="h"))

    with pytest.raises(BacktestError, match="intraday"):
        require_daily_bars(data)


def test_business_days_with_weekend_gaps_are_accepted() -> None:
    require_daily_bars(market(make_frame(np.linspace(100, 140, 40))))


# ------------------------------------------------------------------ definedness carried through


def test_a_simulation_carries_the_signal_definedness_record() -> None:
    data = oscillating_market()
    strategy = strategy_with(entry="close > 100", exits=[{"name": "x", "max_holding_days": 5}])

    simulation = run_variants(strategy, data)[0]

    assert simulation.entry_signal.defined_pct == pytest.approx(100.0)
    assert simulation.exit_signal is None


# ------------------------------------------------- holding rules are applied in one pass


def test_holding_rules_cost_exactly_one_simulation_per_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A performance property with correctness consequences, so it is pinned by a test.

    The first implementation evaluated the holding rules by iterating exits and realised
    positions to a fixed point. It was correct, but the recursion resolves one trade per pass,
    so a history with 200 trades needed 200 simulations per variant -- far too slow to sit
    inside an optimizer loop. Applying the rules inside ``signal_func_nb`` makes it one pass.
    """
    from cracktrade.backtest.portfolio import simulate as real_simulate

    data = oscillating_market(400)
    strategy = strategy_with(
        entry="close > 100",
        exits=[
            {"name": "x", "signal": "close < 100", "min_holding_days": 2, "max_holding_days": 9}
        ],
    )

    calls = 0

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real_simulate(*args, **kwargs)

    monkeypatch.setattr("cracktrade.backtest.runner.simulate", counting)
    simulation = run_variants(strategy, data)[0]

    assert calls == 1
    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    assert len(closed) > 10, "expected a history with many trades"
    held = closed["exit_idx"] - closed["entry_idx"]
    assert int(held.min()) >= 2
    assert int(held.max()) <= 9


def test_both_holding_bounds_bind_together() -> None:
    data = oscillating_market(300)
    strategy = strategy_with(
        entry="close > 100",
        exits=[
            {"name": "x", "signal": "close < 102", "min_holding_days": 3, "max_holding_days": 7}
        ],
    )

    simulation = run_variants(strategy, data)[0]

    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    assert not closed.empty
    held = closed["exit_idx"] - closed["entry_idx"]
    assert int(held.min()) >= 3
    assert int(held.max()) <= 7


def test_a_forced_exit_does_not_re_enter_on_the_same_bar() -> None:
    """The exit and a re-entry cannot both happen at one open; the re-entry waits a bar."""
    data = oscillating_market(200)
    strategy = strategy_with(entry="close > 100", exits=[{"name": "x", "max_holding_days": 3}])

    simulation = run_variants(strategy, data)[0]

    records = trades(simulation).sort_values("entry_idx")
    exits = records["exit_idx"].to_numpy()[:-1]
    later_entries = records["entry_idx"].to_numpy()[1:]
    assert bool((later_entries > exits).all())


def test_holding_rules_key_off_realised_positions_not_signals() -> None:
    """Defect D8. The entry condition holds for long runs of bars; only the first opens a trade.

    With ``max_holding_days`` of 3 every closed trade must last exactly 3 bars. Keyed off
    signals instead, the time exits would land 3 bars after *every* True entry bar, closing
    positions early and producing shorter trades.
    """
    data = oscillating_market(200)
    strategy = strategy_with(entry="close > 100", exits=[{"name": "x", "max_holding_days": 3}])

    simulation = run_variants(strategy, data)[0]

    closed = trades(simulation)
    closed = closed[closed["status"] == 1]
    held = set((closed["exit_idx"] - closed["entry_idx"]).tolist())
    assert held == {3}
