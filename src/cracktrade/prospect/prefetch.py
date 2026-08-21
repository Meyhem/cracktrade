"""Parent-side price history for a pool of prospecting ticks -- spec section 19.7.

A tick fetches its home ticker plus every sibling and control in its family, about eleven for
semiconductors. Run P of them at once and each fetches the same eleven, so twelve concurrent
semiconductor ticks would ask the provider for 132 histories to use 11 -- and
:class:`~cracktrade.prospect.session.SweepHistory` warns that a second fetch of one ticker can
return *different bars*, which would leave two ticks disagreeing about the same instrument.

The parent fetches instead and hands frames to its children, so there is one rate-limit budget
and one place to throttle. Threads rather than processes, because fetching is I/O-bound; the
CPU-bound half is what the process pool is for.

**Raw frames, not loaded histories.** What is cached is exactly what the provider returned, and
each child still runs the identical :func:`~cracktrade.data.loader.load_history` path over it.
Caching the loaded result would put a second code path between the provider and a candidate, and
the whole claim of this design is that parallelism changes no number.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING

from cracktrade.errors import CracktradeError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    import pandas as pd

    from cracktrade.config import Interval
    from cracktrade.data import MarketDataProvider

logger = get_logger(__name__)

#: How long a fetched frame may be reused before it is fetched again.
#:
#: A sweep runs for days, and a horizon without a bound would keep searching a history that
#: stopped at the session's first tick while ``last_bar_seen`` and the leaderboard both claimed
#: to be current -- the hazard :class:`~cracktrade.data.cache.FrameCache` carries and the reason
#: it is off by default. Sixty seconds is comfortably under one tick's median of 22.2s measured
#: 2026-08-21, so consecutive ticks in a family still share their fetches.
DEFAULT_TTL_SECONDS = 60.0

#: Concurrent fetches, which is also the provider's rate-limit budget.
DEFAULT_MAX_THREADS = 8


@dataclass(frozen=True, slots=True)
class _Entry:
    frame: pd.DataFrame
    fetched_at: float


@dataclass(slots=True)
class PrefetchHorizon:
    """Fetches, deduplicates and ages out the histories a pool of ticks needs.

    Attributes:
        provider: where bars come from.
        interval: the session's frozen bar width.
        ttl_seconds: how long an entry may be reused.
        max_threads: concurrent fetches.
    """

    provider: MarketDataProvider
    interval: Interval
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    max_threads: int = DEFAULT_MAX_THREADS
    _entries: dict[str, _Entry] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def frames_for(self, tickers: Iterable[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        """Every requested ticker that could be fetched, keyed by ticker.

        A ticker the provider refuses is *omitted* rather than raised: one delisting must not
        stop the other ticks in flight. The child that needed it fails its own tick and is
        counted as a failure, exactly as it would be if it had fetched for itself.

        A refusal is deliberately not remembered. Caching an absence would let one provider
        hiccup blacklist an instrument for the rest of a sweep, which is a far longer punishment
        than the fault deserves.
        """
        wanted = list(dict.fromkeys(tickers))
        missing = [ticker for ticker in wanted if self._stale(ticker)]
        if missing:
            self._fetch_all(missing, start, end)
        with self._lock:
            return {
                ticker: self._entries[ticker].frame for ticker in wanted if ticker in self._entries
            }

    def _stale(self, ticker: str) -> bool:
        with self._lock:
            entry = self._entries.get(ticker)
        return entry is None or (time.monotonic() - entry.fetched_at) >= self.ttl_seconds

    def _fetch_all(self, tickers: list[str], start: date, end: date) -> None:
        workers = min(self.max_threads, len(tickers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fetched = pool.map(lambda ticker: self._fetch_one(ticker, start, end), tickers)
            for ticker, frame in zip(tickers, fetched, strict=True):
                if frame is None:
                    continue
                with self._lock:
                    self._entries[ticker] = _Entry(frame=frame, fetched_at=time.monotonic())

    def _fetch_one(self, ticker: str, start: date, end: date) -> pd.DataFrame | None:
        try:
            return self.provider.fetch(ticker, start, end, self.interval)
        except CracktradeError as failure:
            logger.info("prefetch of %s failed: %s: %s", ticker, type(failure).__name__, failure)
            return None


__all__ = ["DEFAULT_MAX_THREADS", "DEFAULT_TTL_SECONDS", "PrefetchHorizon"]
