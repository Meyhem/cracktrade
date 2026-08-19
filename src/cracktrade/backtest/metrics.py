"""Turning a simulated portfolio into the domain's value objects.

Normative reference: ``docs/ENGINE_SPEC.md`` section 8.

Two corrections from the legacy engine are load-bearing here, and both are about the risk-free
rate:

* **It was ignored.** ``execution.risk_free_rate`` was parsed and then hardcoded to ``0.04`` at
  the point of use, so changing it changed nothing (defect D3).
* **It was passed on the wrong scale.** vectorbt's ``risk_free`` is a *per-period* figure, and
  an annual one was handed to it directly (defect D4). Measured on a 756-bar sample: the correct
  per-period conversion gives a Sharpe of 0.173, the annual rate passed raw gives **-74.97**.
  That is why every legacy strategy looked catastrophic.

The third correction is ``year_freq``. It is not a ``from_signals`` parameter, it defaults to a
365-day year, and it is passed explicitly at every call site here. On the same sample the
annualised return is 5.48% on 252 trading days and 8.04% on 365 calendar days -- a 47%
overstatement, silently.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from cracktrade.backtest.calendar import DAILY, Calendar
from cracktrade.domain import Metrics, Trade, YearReturn

if TYPE_CHECKING:
    import vectorbt as vbt

#: vectorbt's trade status code for a closed position.
_CLOSED = 1


def per_period_risk_free(annual_rate: float, calendar: Calendar = DAILY) -> float:
    """Convert an annual risk-free rate to the per-period figure vectorbt expects.

    ``(1 + r) ** (1 / periods_per_year) - 1``. Compounding the result over a year's worth of
    periods returns the annual rate, which is asserted by a test -- the conversion is the
    entire content of defect D4 and getting its direction wrong is not detectable by eye.

    A "period" is one bar, so on 30-minute Xetra bars the exponent is 1/4284 rather than
    1/252. Leaving it at 252 would charge a whole year of risk-free return against every
    seventeen bars.
    """
    return float((1.0 + annual_rate) ** (1.0 / calendar.periods_per_year) - 1.0)


def extract_metrics(
    portfolio: vbt.Portfolio,
    *,
    risk_free_rate: float,
    offset: int = 0,
    calendar: Calendar = DAILY,
) -> Metrics:
    """Compute the full metric set for one simulated portfolio.

    ``offset`` discards the first ``offset`` bars from every statistic. It exists for the
    optimizer's test window, whose indicators must warm up on *train* bars: the simulation runs
    over the warm-up prefix plus the test region, and the prefix is then excluded from scoring.
    Leaving it in would count bars on which nothing could trade -- deflating the annualised
    return and, worse, damping the volatility that Sharpe divides by.

    Metrics come from the returns accessor rather than the ``Portfolio`` methods so that offset
    and non-offset runs share one code path. At ``offset=0`` the two agree exactly, which is
    asserted by a test.
    """
    rf = per_period_risk_free(risk_free_rate, calendar)
    records = portfolio.trades.records
    if offset:
        records = records[records["entry_idx"] >= offset]
    closed = records[records["status"] == _CLOSED]

    pnl = closed["pnl"].to_numpy() if not closed.empty else np.zeros(0)
    gains = float(pnl[pnl > 0].sum()) if pnl.size else 0.0
    losses = float(-pnl[pnl < 0].sum()) if pnl.size else 0.0
    holding = (closed["exit_idx"] - closed["entry_idx"]).to_numpy() if not closed.empty else None

    returns = portfolio.returns().iloc[offset:]
    accessor = returns.vbt.returns(freq=calendar.freq, year_freq=calendar.year_freq)

    return Metrics(
        total_trades=len(closed),
        win_rate_pct=100.0 * float((pnl > 0).mean()) if pnl.size else 0.0,
        profit_factor=_profit_factor(gains, losses),
        total_pnl=float(pnl.sum()) if pnl.size else 0.0,
        final_equity=_scalar(portfolio.final_value()),
        total_return_pct=100.0 * _scalar(accessor.total()),
        cagr_pct=100.0 * _scalar(accessor.annualized(), default=0.0),
        max_drawdown_pct=100.0 * _scalar(accessor.max_drawdown(), default=0.0),
        sharpe_ratio=_scalar(accessor.sharpe_ratio(risk_free=rf)),
        sortino_ratio=_scalar(accessor.sortino_ratio(required_return=rf)),
        calmar_ratio=_scalar(accessor.calmar_ratio()),
        exposure_pct=100.0 * float(portfolio.position_mask().to_numpy()[offset:].mean()),
        avg_holding_bars=float(holding.mean()) if holding is not None and holding.size else 0.0,
        best_trade_pnl=float(pnl.max()) if pnl.size else 0.0,
        worst_trade_pnl=float(pnl.min()) if pnl.size else 0.0,
        bars=len(returns),
        yearly_returns=yearly_returns(returns),
        worst_rolling_12m_pct=worst_rolling_12m(returns, calendar),
    )


def extract_trades(
    portfolio: vbt.Portfolio, index: pd.DatetimeIndex, *, intraday: bool = False
) -> tuple[Trade, ...]:
    """Convert vectorbt's trade records into domain :class:`Trade` objects.

    ``intraday`` decides whether a trade is stamped with a date or a datetime. Daily runs keep
    emitting bare dates -- ``date``, not a midnight ``datetime`` -- so that stored results and
    the client's date parsing are byte-identical to what they were. An intraday trade needs the
    time or two trades in one session would report the same moment.
    """
    records = portfolio.trades.records
    if records.empty:
        return ()

    bars = len(index)
    # Column access rather than itertuples: one of the columns is named "return", which is a
    # Python keyword, so itertuples silently renames it to a positional alias.
    entry_idx = records["entry_idx"].to_numpy().astype(int)
    exit_idx = records["exit_idx"].to_numpy().astype(int)
    status = records["status"].to_numpy().astype(int)
    entry_price = records["entry_price"].to_numpy()
    exit_price = records["exit_price"].to_numpy()
    size = records["size"].to_numpy()
    pnl = records["pnl"].to_numpy()
    returns = records["return"].to_numpy()
    fees = records["entry_fees"].to_numpy() + records["exit_fees"].to_numpy()

    trades: list[Trade] = []
    for position in range(len(records)):
        # An open position's exit_idx points at the last bar, which is where it is marked to
        # market rather than where it was sold. Reporting that as an exit date would invent a
        # trade that has not happened.
        # bool(), like the float() and int() on every field below it. numpy's comparison
        # returns np.bool_, which is not a `bool` -- and the serializer's fallback renders an
        # unrecognised type with str(), so an uncoerced value reaches a client as the *string*
        # "False", which is truthy in JavaScript. Every closed trade would read as open.
        is_open = bool(status[position] != _CLOSED)
        closed_at = min(exit_idx[position], bars - 1)
        stamp = _stamp_intraday if intraday else _stamp_daily
        trades.append(
            Trade(
                entry_date=stamp(index[entry_idx[position]]),
                exit_date=None if is_open else stamp(index[closed_at]),
                entry_price=float(entry_price[position]),
                exit_price=None if is_open else float(exit_price[position]),
                size=float(size[position]),
                pnl=float(pnl[position]),
                return_pct=100.0 * float(returns[position]),
                fees=float(fees[position]),
                holding_bars=int(exit_idx[position] - entry_idx[position]),
                is_open=is_open,
            )
        )
    return tuple(trades)


def _stamp_daily(timestamp: pd.Timestamp) -> date:
    """A daily bar's identity is its date, and stays a ``date``.

    Not a midnight ``datetime``: the serializer renders both through ``isoformat`` and a
    ``datetime`` would start emitting ``2020-01-01T00:00:00`` where every stored result and
    every client parser expects ``2020-01-01``.
    """
    return timestamp.date()


def _stamp_intraday(timestamp: pd.Timestamp) -> datetime:
    """An intraday bar's identity includes its time, in the exchange's local wall clock."""
    return timestamp.to_pydatetime()


def yearly_returns(returns: pd.Series) -> tuple[YearReturn, ...]:
    """Compound each calendar year's returns separately."""
    if returns.empty:
        return ()
    index = returns.index
    assert isinstance(index, pd.DatetimeIndex)
    compounded = (1.0 + returns).groupby(index.year).prod() - 1.0
    return tuple(
        YearReturn(year=int(str(year)), return_pct=100.0 * float(value))
        for year, value in compounded.items()
    )


def worst_rolling_12m(returns: pd.Series, calendar: Calendar = DAILY) -> float:
    """The worst any rolling one-year window did, as a percentage.

    A single aggregate figure cannot show that a strategy spent a year underwater. This can.

    Returns ``0.0`` when the history is shorter than a year, which on a 15m or 30m strategy is
    always: the provider serves at most 60 days at those intervals, and a year is 4284 bars.
    That zero means "not measurable here", not "never lost money over a year", so the client
    must present it as absent rather than plot it (spec section 13).
    """
    window = round(calendar.periods_per_year)
    if len(returns) < window:
        return 0.0
    windows = (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
    worst = windows.min()
    return 0.0 if pd.isna(worst) else 100.0 * float(worst)


def _profit_factor(gains: float, losses: float) -> float:
    """Gross profit over gross loss.

    A strategy with profits and no losses has an undefined ratio; ``inf`` is the honest answer
    and reads correctly in a comparison. One with neither scores zero.
    """
    if losses > 0:
        return gains / losses
    return float("inf") if gains > 0 else 0.0


def _scalar(value: Any, *, default: float = 0.0) -> float:
    """Reduce a vectorbt result to a float, mapping NaN onto ``default``.

    Single-column portfolios return scalars, but the accessors are shape-polymorphic and a NaN
    arrives whenever a statistic is undefined -- no trades, no drawdown, a flat equity curve.
    Those are legitimately "nothing happened", not errors.
    """
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    number = float(value)
    return default if np.isnan(number) else number
