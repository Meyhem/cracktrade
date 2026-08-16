"""Reading version history and comparing versions.

The write side lives in :mod:`cracktrade.api.services.strategies` beside the creation
workflows; this is everything a history tab or a diff view reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import NotFoundError
from cracktrade.api.repos import RunRepo, VersionRepo
from cracktrade.api.repos.rows import VersionRow
from cracktrade.api.services.diff import Change, SectionDiff, diff_by_section, flags_for, summarise


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One version, with what changed and what that means for runs made against it."""

    version: VersionRow
    head: bool
    changes: tuple[Change, ...]
    flags: tuple[str, ...]
    runs_against: int


def history(work: UnitOfWork, strategy_id: UUID) -> tuple[HistoryEntry, ...]:
    """The version timeline, newest first.

    Change summaries are computed against each version's predecessor at read time. Storing them
    would let a summary drift from the two configs it claims to describe.
    """
    versions = VersionRepo(work.connection)
    rows = versions.list(strategy_id)
    if not rows:
        raise NotFoundError(f"no strategy {strategy_id}")

    run_counts = versions.count_runs_against(strategy_id)
    head = rows[0].version
    by_version = {row.version: row for row in rows}

    entries: list[HistoryEntry] = []
    for row in rows:
        previous = by_version.get(row.version - 1)
        changes = summarise(previous.config, row.config) if previous else ()
        entries.append(
            HistoryEntry(
                version=row,
                head=row.version == head,
                changes=changes,
                flags=flags_for(changes),
                runs_against=run_counts.get(row.version, 0),
            )
        )
    return tuple(entries)


def get_version(work: UnitOfWork, strategy_id: UUID, version: int) -> VersionRow:
    """One immutable version, exactly as saved."""
    return VersionRepo(work.connection).require(strategy_id, version)


@dataclass(frozen=True, slots=True)
class VersionDiff:
    """Two versions compared, grouped by config section."""

    older: VersionRow
    newer: VersionRow
    head_version: int
    groups: tuple[SectionDiff, ...]


def compare(work: UnitOfWork, strategy_id: UUID, *, older: int, newer: int) -> VersionDiff:
    """Compare two versions of one strategy."""
    versions = VersionRepo(work.connection)
    head = versions.require_head(strategy_id)
    first = versions.require(strategy_id, older)
    second = versions.require(strategy_id, newer)
    return VersionDiff(
        older=first,
        newer=second,
        head_version=head.version,
        groups=tuple(diff_by_section(first.config, second.config)),
    )


def count_runs(work: UnitOfWork, strategy_id: UUID) -> int:
    """How many runs exist for a strategy, for the save panel's staleness warning."""
    return RunRepo(work.connection).count(strategy_id=strategy_id)
