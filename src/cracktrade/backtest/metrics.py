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

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from cracktrade.backtest.portfolio import YEAR_FREQ
from cracktrade.domain import Metrics, Trade, YearReturn
from cracktrade.settings import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    import vectorbt as vbt

#: Bars in a rolling one-year window, for the worst-12-month statistic.
_ROLLING_YEAR = TRADING_DAYS_PER_YEAR

#: vectorbt's trade status code for a closed position.
_CLOSED = 1


def per_period_risk_free(annual_rate: float) -> float:
    """Convert an annual risk-free rate to the per-period figure vectorbt expects.

    ``(1 + r) ** (1 / 252) - 1``. Compounding the result over 252 periods returns the annual
    rate, which is asserted by a test -- the conversion is the entire content of defect D4 and
    getting its direction wrong is not detectable by eye.
    """
    return float((1.0 + annual_rate) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0)


def extract_metrics(portfolio: vbt.Portfolio, *, risk_free_rate: float) -> Metrics:
    """Compute the full metric set for one simulated portfolio."""
    rf = per_period_risk_free(risk_free_rate)
    records = portfolio.trades.records
    closed = records[records["status"] == _CLOSED]

    pnl = closed["pnl"].to_numpy() if not closed.empty else np.zeros(0)
    gains = float(pnl[pnl > 0].sum()) if pnl.size else 0.0
    losses = float(-pnl[pnl < 0].sum()) if pnl.size else 0.0
    holding = (closed["exit_idx"] - closed["entry_idx"]).to_numpy() if not closed.empty else None

    returns = portfolio.returns()

    return Metrics(
        total_trades=len(closed),
        win_rate_pct=100.0 * float((pnl > 0).mean()) if pnl.size else 0.0,
        profit_factor=_profit_factor(gains, losses),
        total_pnl=float(pnl.sum()) if pnl.size else 0.0,
        final_equity=_scalar(portfolio.final_value()),
        total_return_pct=100.0 * _scalar(portfolio.total_return()),
        cagr_pct=100.0 * _scalar(portfolio.annualized_return(year_freq=YEAR_FREQ), default=0.0),
        max_drawdown_pct=100.0 * _scalar(portfolio.max_drawdown(), default=0.0),
        sharpe_ratio=_scalar(portfolio.sharpe_ratio(risk_free=rf, year_freq=YEAR_FREQ)),
        sortino_ratio=_scalar(portfolio.sortino_ratio(required_return=rf, year_freq=YEAR_FREQ)),
        calmar_ratio=_scalar(portfolio.calmar_ratio(year_freq=YEAR_FREQ)),
        exposure_pct=100.0 * float(portfolio.position_mask().to_numpy().mean()),
        avg_holding_days=float(holding.mean()) if holding is not None and holding.size else 0.0,
        best_trade_pnl=float(pnl.max()) if pnl.size else 0.0,
        worst_trade_pnl=float(pnl.min()) if pnl.size else 0.0,
        bars=len(returns),
        yearly_returns=yearly_returns(returns),
        worst_rolling_12m_pct=worst_rolling_12m(returns),
    )


def extract_trades(portfolio: vbt.Portfolio, index: pd.DatetimeIndex) -> tuple[Trade, ...]:
    """Convert vectorbt's trade records into domain :class:`Trade` objects."""
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
        is_open = status[position] != _CLOSED
        closed_at = min(exit_idx[position], bars - 1)
        trades.append(
            Trade(
                entry_date=index[entry_idx[position]].date(),
                exit_date=None if is_open else index[closed_at].date(),
                entry_price=float(entry_price[position]),
                exit_price=None if is_open else float(exit_price[position]),
                size=float(size[position]),
                pnl=float(pnl[position]),
                return_pct=100.0 * float(returns[position]),
                fees=float(fees[position]),
                holding_days=int(exit_idx[position] - entry_idx[position]),
                is_open=is_open,
            )
        )
    return tuple(trades)


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


def worst_rolling_12m(returns: pd.Series) -> float:
    """The worst any rolling one-year window did, as a percentage.

    A single aggregate figure cannot show that a strategy spent a year underwater. This can.
    """
    if len(returns) < _ROLLING_YEAR:
        return 0.0
    windows = (1.0 + returns).rolling(_ROLLING_YEAR).apply(np.prod, raw=True) - 1.0
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
