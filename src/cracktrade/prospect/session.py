"""What a sweep is asking, and the history it asks it against -- spec section 19.7.

Two things live here that a caller would otherwise have to invent, and inventing them
separately in the CLI and in the API is how the two would come to mean different things.

:class:`SweepParams` is the question, frozen when a session starts. :class:`SweepHistory` is how
any ticker in that question gets its bars.

The date range is deliberately **not** part of the question. A sweep runs for days, and a window
fixed when it started would have it still searching last month's bars a fortnight later --
reporting a "latest" candidate discovered on a history that no longer reaches the present. The
window is therefore computed per tick from the interval's own reach, which is also the only
honest source for it: the provider serves roughly 726 days of hourly bars and 58 days of 15- and
30-minute bars, and that is a cliff rather than a taper (section 4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cracktrade.config import Interval, PositionSizing
from cracktrade.data import FrameCache, MarketData, MarketDataProvider, load_history
from cracktrade.evolution import Chassis, library_warmup
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from cracktrade.config import Strategy

#: Bars of margin beyond the library's warm-up, matching the worker's own ``WARMUP_MARGIN``. A
#: ticker whose history is entirely warm-up is refused at the data layer rather than failing
#: somewhere inside the search, where it would be recorded as an engine fault.
WARMUP_MARGIN = 30

#: How far back a daily sweep looks. The intraday intervals take their window from
#: :attr:`~cracktrade.config.Interval.max_lookback`, which is the provider's hard limit; daily
#: bars have no such limit, so this is a choice -- eight years, long enough to contain more than
#: one regime and short enough that the earliest bars still describe a market that exists.
DAILY_LOOKBACK_DAYS = 8 * 365


@dataclass(frozen=True, slots=True)
class SweepParams:
    """The question a session asks, identical for every ticker in its universe.

    Frozen at session creation and never edited. A sweep whose costs or bar width could change
    partway would make its own leaderboard incomparable with itself -- the candidate found on
    Tuesday and the one found on Thursday would have been asked different questions, and the
    ranking would be reading the difference between the questions.

    Attributes:
        interval: bar width. Part of the question, never searched over.
        population: genomes per generation.
        generations: how many generations each tick runs.
        objective: the fitness function's name.
        segments: in-sample segments the fitness function medians over.
        holdout_fraction: share of the history held back from the search.
        min_trades: absolute floor on closed trades.
        min_trades_per_year: rate floor, scaled by the length of the window.
        initial_capital: starting equity.
        slippage_pct: per-side slippage, in percent.
        commission_pct: per-side commission, in percent.
        risk_free_rate: annual rate, as a fraction.
        position_sizing: optional sizing rule, applied to every ticker alike.
        lookback_days: overrides the interval's own reach. Rarely wanted; it exists so a sweep
            can be narrowed deliberately rather than by editing a constant.
    """

    interval: Interval = Interval.H1
    population: int = 30
    generations: int = 10
    objective: str = "calmar"
    segments: int = 4
    holdout_fraction: float = 0.2
    min_trades: int = 20
    min_trades_per_year: float = 4.0
    initial_capital: float = 100_000.0
    slippage_pct: float = 0.1
    commission_pct: float = 0.1
    risk_free_rate: float = 0.04
    position_sizing: PositionSizing | None = None
    lookback_days: int | None = None

    @property
    def window_days(self) -> int:
        """How far back a tick reaches, in days.

        From the interval unless overridden. Asking for more hourly bars than the provider
        keeps does not fail -- it silently returns the 726 days that exist -- so taking the
        number from the interval is what stops a session's stated window from describing
        something it never received.
        """
        if self.lookback_days is not None:
            return self.lookback_days
        reach = self.interval.max_lookback
        return DAILY_LOOKBACK_DAYS if reach is None else reach.days

    def window(self, *, now: date | None = None) -> tuple[date, date]:
        """The date range for a tick starting now."""
        end = now or datetime.now(UTC).date()
        return end - timedelta(days=self.window_days), end

    def chassis_for(self, ticker: str, *, now: date | None = None) -> Chassis:
        """The chassis a tick evolves against for one ticker.

        Every field except the ticker and the dates is the session's, so two candidates from
        the same sweep differ in what was searched and not in what they were charged.
        """
        start, end = self.window(now=now)
        return Chassis(
            name=f"prospect_{ticker.lower()}",
            ticker=ticker,
            start_date=start,
            end_date=end,
            interval=self.interval,
            initial_capital=self.initial_capital,
            slippage_pct=self.slippage_pct,
            commission_pct=self.commission_pct,
            risk_free_rate=self.risk_free_rate,
            position_sizing=self.position_sizing,
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe mapping, for the session row's ``params`` column."""
        payload: dict[str, Any] = {
            "interval": self.interval.value,
            "population": self.population,
            "generations": self.generations,
            "objective": self.objective,
            "segments": self.segments,
            "holdout_fraction": self.holdout_fraction,
            "min_trades": self.min_trades,
            "min_trades_per_year": self.min_trades_per_year,
            "initial_capital": self.initial_capital,
            "slippage_pct": self.slippage_pct,
            "commission_pct": self.commission_pct,
            "risk_free_rate": self.risk_free_rate,
        }
        if self.position_sizing is not None:
            payload["position_sizing"] = self.position_sizing.model_dump(mode="json")
        if self.lookback_days is not None:
            payload["lookback_days"] = self.lookback_days
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SweepParams:
        """Rebuild from a stored ``params`` column.

        Every key is optional and falls back to the field default. A session written before a
        setting existed has to keep running, and the alternative -- refusing to resume it --
        would lose a leaderboard that took days to accumulate over a field that was added
        afterwards.
        """
        sizing = payload.get("position_sizing")
        return cls(
            interval=Interval(payload.get("interval", Interval.H1.value)),
            population=int(payload.get("population", 30)),
            generations=int(payload.get("generations", 10)),
            objective=str(payload.get("objective", "calmar")),
            segments=int(payload.get("segments", 4)),
            holdout_fraction=float(payload.get("holdout_fraction", 0.2)),
            min_trades=int(payload.get("min_trades", 20)),
            min_trades_per_year=float(payload.get("min_trades_per_year", 4.0)),
            initial_capital=float(payload.get("initial_capital", 100_000.0)),
            slippage_pct=float(payload.get("slippage_pct", 0.1)),
            commission_pct=float(payload.get("commission_pct", 0.1)),
            risk_free_rate=float(payload.get("risk_free_rate", 0.04)),
            position_sizing=PositionSizing.model_validate(sizing) if sizing else None,
            lookback_days=int(payload["lookback_days"]) if "lookback_days" in payload else None,
        )


@dataclass(slots=True)
class SweepHistory:
    """Supplies bars for any ticker a tick needs, over the tick's own window.

    Satisfies :data:`~cracktrade.prospect.transfer.DataFor`. Memoised, because one tick asks for
    the same controls repeatedly -- SPY and QQQ are controls for several families -- and a
    second fetch of the same ticker within one tick would not only be slow but could return
    *different bars*, which would make the transfer report an average over two histories.

    Deliberately built fresh per tick and not held for the life of a session. A sweep runs for
    days; a cache that lived that long would keep prospecting a history that stopped at the
    session's first tick, and ``last_bar_seen`` would quietly go stale while the leaderboard
    claimed to be current.
    """

    params: SweepParams
    provider: MarketDataProvider
    now: date | None = None
    cache: FrameCache | None = None
    max_filled_fraction: float = 1.0
    _loaded: dict[str, MarketData] = field(default_factory=dict, repr=False)

    def __call__(self, ticker: str) -> MarketData:
        """The history for one ticker, fetched once per tick.

        Raises:
            DataError: the provider has nothing, or too little, or too much of it filled.
        """
        cached = self._loaded.get(ticker)
        if cached is not None:
            return cached
        loaded = load_history(
            self._probe(ticker),
            self.provider,
            cache=self.cache,
            # The library's warm-up, not any one strategy's: the search can reach for any block
            # in the library and the longest looks back 200 bars, so a history sized from a
            # particular strategy would accept a window the search then cannot use.
            min_bars=library_warmup() + WARMUP_MARGIN,
            max_filled_fraction=self.max_filled_fraction,
        )
        self._loaded[ticker] = loaded
        return loaded

    def _probe(self, ticker: str) -> Strategy:
        """A minimal valid strategy, existing only to carry a ticker and a window.

        ``load_history`` reads the universe section and nothing else, but it takes a
        :class:`~cracktrade.config.Strategy` -- so rather than reimplement fetching and
        preparation here and let the two drift, this builds the smallest configuration the
        validator accepts. The rules are never evaluated.
        """
        start, end = self.params.window(now=self.now)
        return build_strategy(
            {
                "strategy": {"name": f"history_probe_{ticker.lower()}"},
                "universe": {
                    "ticker": ticker,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "interval": self.params.interval.value,
                },
                "execution": {
                    "initial_capital": self.params.initial_capital,
                    "slippage_pct": self.params.slippage_pct,
                    "commission_pct": self.params.commission_pct,
                    "risk_free_rate": self.params.risk_free_rate,
                },
                "indicators": [{"name": "probe_sma", "type": "sma", "window": 2}],
                "entry": {"signal": "close > probe_sma"},
                "exit": {"signal": "close < probe_sma"},
            }
        )


__all__ = ["DAILY_LOOKBACK_DAYS", "WARMUP_MARGIN", "SweepHistory", "SweepParams"]
