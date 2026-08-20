"""Repositories -- one per table, plus the two overview views.

SQL lives here and nowhere above. Rows come back as frozen dataclasses; nothing in this layer
imports the engine or pydantic, so the storage shape and the wire shape stay independent.

Repositories do not own transactions. A service composes several inside one unit of work, and
they commit or roll back together -- promotion writes a strategy, a version and a queued run,
and a half-applied promotion would leave a strategy the UI cannot render.
"""

from __future__ import annotations

from cracktrade.api.repos.base import Repository
from cracktrade.api.repos.prospect import ProspectRepo
from cracktrade.api.repos.run import RunRepo
from cracktrade.api.repos.series import SeriesRepo
from cracktrade.api.repos.strategy import StrategyRepo
from cracktrade.api.repos.version import VersionRepo

__all__ = ["ProspectRepo", "Repository", "RunRepo", "SeriesRepo", "StrategyRepo", "VersionRepo"]
