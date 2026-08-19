"""Access to the committed intraday fixtures.

The fixtures are real yfinance responses, captured once and frozen; see
``tests/fixtures/README.md`` for their provenance and the measured facts they encode. Loading
them through here rather than through ad-hoc paths keeps every intraday test offline and gives
one place to state the shape they arrive in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

FIXTURE_DIR: Final = Path(__file__).resolve().parent / "fixtures"

#: The day the fixtures were captured. Tests that need a wall clock pass a moment derived from
#: this rather than reading the real one, so the suite does not change behaviour over time.
CAPTURE_DATE: Final = "2026-08-19"

#: IANA zone each fixture's index carries, for assertions that would otherwise hardcode it.
FIXTURE_TIMEZONES: Final[dict[str, str]] = {
    "sap_de_30m": "Europe/Berlin",
    "sap_de_1h": "Europe/Berlin",
    "vod_l_1h_halfdays": "Europe/London",
    "spy_30m": "America/New_York",
}


def load_fixture(name: str) -> pd.DataFrame:
    """Return a captured provider response, in the raw shape the provider produces.

    Timezone-aware index in exchange-local time, capitalised OHLCV columns, integer volume --
    deliberately *not* normalised, because normalising it is what the tests are testing.
    """
    path = FIXTURE_DIR / f"{name}.parquet"
    if not path.exists():  # pragma: no cover - a missing fixture is a broken checkout
        msg = f"missing intraday fixture {path}; see tests/fixtures/README.md"
        raise FileNotFoundError(msg)
    return pd.read_parquet(path)
