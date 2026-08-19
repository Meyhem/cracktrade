"""How many bars there are in a year, for this run.

Normative reference: ``docs/ENGINE_SPEC.md`` section 7.5.

Annualisation used to be two module constants -- ``FREQ = "1D"`` and ``YEAR_FREQ = "252 days"``
-- which was correct while every history was daily and becomes silently, spectacularly wrong the
moment one is not. A 30-minute Sharpe computed on a 252-bar year is overstated by the square
root of seventeen: roughly a factor of four, printed to two decimal places, with nothing to
suggest it is anything other than the truth.

So the calendar is computed per run, from the data. The one number it cannot be told is how many
bars a session holds -- that is a property of the *exchange*, not of the interval. A 30-minute
Xetra session is 17 bars (09:00-17:30) and a 30-minute New York session is 13 (09:30-16:00).
Neither is a constant worth writing down, because the LSE, half-days, early closes and late
opens all disagree with any figure chosen. It is measured from the ticker's own history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pandas as pd

from cracktrade.config import Interval
from cracktrade.data.sessions import median_bars_per_session
from cracktrade.settings import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from cracktrade.data import MarketData


@dataclass(frozen=True, slots=True)
class Calendar:
    """The annualisation basis for one run.

    Attributes:
        freq: the pandas offset alias for one bar, handed to vectorbt as ``freq``.
        periods_per_year: how many bars a trading year contains at this interval on this
            exchange. ``252`` daily; ``252 * bars_per_session`` intraday.
        bars_per_session: bars in a typical session. ``1`` on daily data, where the session
            *is* the bar.
    """

    freq: str
    periods_per_year: float
    bars_per_session: int

    @property
    def year_freq(self) -> pd.Timedelta:
        """A trading year expressed as a duration, for vectorbt's ``year_freq``.

        vectorbt derives its annualisation factor as ``year_freq / freq``, so this has to be
        the length of a trading year *in trading time*, not in calendar time -- 252 days
        daily, and 252 x 17 half-hours (about 89 days) for 30-minute Xetra bars. Passing a
        calendar year instead is audit finding A2, which inflated every Sharpe by
        ``sqrt(365/252)``.

        Returned as a ``Timedelta`` rather than a string because the intraday values are not
        whole numbers of days and formatting them back into a string only adds a place to lose
        precision.
        """
        return pd.Timedelta(_BAR_DURATION[self.freq] * self.periods_per_year)

    @classmethod
    def of(cls, data: MarketData) -> Calendar:
        """The calendar implied by a price history.

        Daily returns :data:`DAILY` exactly, so the existing daily numbers are not merely
        equal to what they were but produced by the identical values.
        """
        interval = data.interval
        if not interval.is_intraday:
            return DAILY

        bars = median_bars_per_session(data.index) or 1
        return cls(
            freq=interval.pandas_freq,
            periods_per_year=float(TRADING_DAYS_PER_YEAR * bars),
            bars_per_session=bars,
        )


#: The daily calendar, unchanged from the constants it replaces: 252 one-day bars a year.
DAILY: Final = Calendar(
    freq="1D", periods_per_year=float(TRADING_DAYS_PER_YEAR), bars_per_session=1
)

#: Offset alias → bar duration, for :attr:`Calendar.year_freq`. Sourced from :class:`Interval`
#: rather than restated, so the two cannot drift.
_BAR_DURATION: Final[dict[str, pd.Timedelta]] = {
    interval.pandas_freq: pd.Timedelta(interval.bar_timedelta) for interval in Interval
}
