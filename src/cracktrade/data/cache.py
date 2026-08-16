"""On-disk cache for downloaded bars.

Off by default. This project stores nothing unless asked: the cache holds *inputs*, never
results, and exists only so that iterating on a strategy does not re-download the same decade
of prices on every run. Enable it with ``--cache``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from cracktrade.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FrameCache:
    """Parquet-backed cache keyed by provider, ticker, and requested range.

    Parquet because it round-trips float64 and a DatetimeIndex exactly. A cache that returned
    subtly different numbers than a fresh download would be worse than no cache at all.
    """

    directory: Path

    def get(self, provider: str, ticker: str, start: date, end: date) -> pd.DataFrame | None:
        """Return the cached frame, or ``None`` on a miss or an unreadable entry."""
        path = self._path(provider, ticker, start, end)
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

    def put(self, provider: str, ticker: str, start: date, end: date, frame: pd.DataFrame) -> None:
        """Store ``frame``. Failure to write is logged and otherwise ignored."""
        path = self._path(provider, ticker, start, end)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path)
        except (OSError, ValueError) as error:
            logger.warning("could not write cache entry %s: %s", path, error)

    def _path(self, provider: str, ticker: str, start: date, end: date) -> Path:
        key = f"{provider}|{ticker}|{start.isoformat()}|{end.isoformat()}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        safe_ticker = "".join(char if char.isalnum() else "_" for char in ticker)
        return self.directory / provider / f"{safe_ticker}-{digest}.parquet"
