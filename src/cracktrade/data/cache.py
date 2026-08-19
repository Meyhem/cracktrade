"""On-disk cache for downloaded bars.

Off by default. This project stores nothing unless asked: the cache holds *inputs*, never
results, and exists only so that iterating on a strategy does not re-download the same decade
of prices on every run. Enable it with ``--cache``.

**A caveat that intraday makes visible.** The key includes the requested range, not the moment
of the request, so a range whose ``end`` is today freezes whatever the market had printed when
it was first fetched. Later runs the same day reuse it and see fewer bars than a fresh download
would return. Daily data has always behaved this way and it was nearly invisible, since the
day's own bar is dropped as incomplete anyway. On 30-minute bars it means an afternoon run can
silently reproduce the morning's history. It is left as it is for now -- a cache that
invalidated on a wall clock would stop being reproducible, which is worse -- so the honest
mitigations are elsewhere: the cache is off unless asked for, and the web UI defaults an
intraday range to end *yesterday*, so the common path never touches a live session.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from cracktrade.config import Interval
from cracktrade.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FrameCache:
    """Parquet-backed cache keyed by provider, ticker, and requested range.

    Parquet because it round-trips float64 and a DatetimeIndex exactly. A cache that returned
    subtly different numbers than a fresh download would be worse than no cache at all.
    """

    directory: Path

    def get(
        self,
        provider: str,
        ticker: str,
        start: date,
        end: date,
        interval: Interval = Interval.D1,
    ) -> pd.DataFrame | None:
        """Return the cached frame, or ``None`` on a miss or an unreadable entry."""
        path = self._path(provider, ticker, start, end, interval)
        if not path.exists():
            return None
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError) as error:
            # A corrupt entry is a cache miss, never a failure: the download still works.
            logger.warning("ignoring unreadable cache entry %s: %s", path, error)
            return None
        logger.debug("cache hit for %s %s", provider, ticker)
        return frame

    def put(
        self,
        provider: str,
        ticker: str,
        start: date,
        end: date,
        frame: pd.DataFrame,
        interval: Interval = Interval.D1,
    ) -> None:
        """Store ``frame``. Failure to write is logged and otherwise ignored."""
        path = self._path(provider, ticker, start, end, interval)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path)
        except (OSError, ValueError) as error:
            logger.warning("could not write cache entry %s: %s", path, error)

    def _path(self, provider: str, ticker: str, start: date, end: date, interval: Interval) -> Path:
        """Digest of everything that decides which bars come back.

        The interval joins the key rather than being appended to the filename, so a 30-minute
        request cannot be served the daily frame cached under the same ticker and range. The
        change alters every digest and orphans entries written before it; that is acceptable
        for an input cache, whose worst case is one extra download.
        """
        key = f"{provider}|{ticker}|{interval.value}|{start.isoformat()}|{end.isoformat()}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        safe_ticker = "".join(char if char.isalnum() else "_" for char in ticker)
        return self.directory / provider / f"{safe_ticker}-{digest}.parquet"
