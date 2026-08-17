"""Reading version history and comparing versions.

The write side lives in :mod:`cracktrade.api.services.strategies` beside the creation
workflows; this is everything a history tab or a diff view reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import yaml

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import NotFoundError
from cracktrade.api.repos import RunRepo, VersionRepo
from cracktrade.api.repos.rows import VersionRow
from cracktrade.api.services.diff import Change, SectionDiff, diff_by_section, flags_for, summarise
from cracktrade.config import dump_strategy, parse_strategy
from cracktrade.errors import ConfigError
from cracktrade.indicators import install


@dataclass(frozen=True, slots=True)
class _Described:
    """One side of a comparison: the mapping it is compared as, and the YAML shown beside it."""

    mapping: dict[str, Any]
    text: str


def _canonical(config: dict[str, Any]) -> _Described | None:
    """A stored config in the form the rest of the application addresses it in.

    Versions are stored as ``model_dump`` output, which nests an indicator's type-specific
    settings under ``params``. Comparing two of those directly addresses a window as
    ``indicators.sma_long.params.window``, while the optimizer (spec section 9.1), the editor's
    searchable parameters and ``POST /config/diff`` all address that same leaf as
    ``indicators.sma_long.window``. Spec section 15.1 requires one path per fact, so this routes
    the stored form through the same YAML writer those three already go through.

    Returns ``None`` for a config the current engine can no longer validate. That is not
    hypothetical over an append-only audit trail: an indicator retired from the registry makes
    an old version unparseable while leaving it a perfectly truthful record of what the user
    believed at the time. Raising here would take a whole timeline down in order to describe one
    row of it, so the caller falls back to the stored form for that pair instead.
    """
    install()
    try:
        text = dump_strategy(parse_strategy(config))
    except (ConfigError, ValueError):
        return None
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return _Described(mapping=parsed, text=text)


def _comparable(older: VersionRow, newer: VersionRow) -> tuple[_Described, _Described]:
    """Two versions described the same way, canonically where both sides allow it.

    Both or neither. Canonicalising one side of a pair and not the other would report every
    indicator parameter as removed from one address and added at another -- a diff that is
    entirely artefact, and one that reads as a rewrite of the whole strategy.
    """
    left, right = _canonical(older.config), _canonical(newer.config)
    if left is None or right is None:
        return _stored(older), _stored(newer)
    return left, right


def _stored(row: VersionRow) -> _Described:
    return _Described(mapping=row.config, text=row.config_yaml)


def differences(older: dict[str, Any], newer: dict[str, Any]) -> tuple[Change, ...]:
    """What changed between two stored configs, addressed the way spec section 15.1 requires.

    The one place that question is answered. The save path asks it in order to refuse a version
    that changes nothing, and the timeline asks it in order to describe every version it lists;
    answering it in two places would eventually admit a version whose own history row reports no
    changes.
    """
    left, right = _canonical(older), _canonical(newer)
    if left is None or right is None:
        return summarise(older, newer)
    return summarise(left.mapping, right.mapping)


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
        changes = differences(previous.config, row.config) if previous else ()
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
    """Two versions compared, grouped by config section, with both YAML panes."""

    older: VersionRow
    newer: VersionRow
    head_version: int
    groups: tuple[SectionDiff, ...]
    older_yaml: str
    newer_yaml: str


def compare(work: UnitOfWork, strategy_id: UUID, *, older: int, newer: int) -> VersionDiff:
    """Compare two versions of one strategy.

    The panes are re-dumped from the stored configs rather than served as the stored text. A
    version imported from a file keeps that file's own formatting (see ``import_strategy``), so
    a diff of an import against a later save would otherwise set the user's flow-style document
    beside canonical block style and highlight every line of a config in which one number moved.
    Spec section 15.1 asks that a listed change correspond to a visible line, and it cannot when
    the two panes are written in different hands.

    ``GET /strategies/{id}/versions/{v}`` is unaffected and still returns the stored text: an
    imported document read back is still the user's own. Only the comparison canonicalises,
    because only the comparison needs both sides described the same way.
    """
    versions = VersionRepo(work.connection)
    head = versions.require_head(strategy_id)
    first = versions.require(strategy_id, older)
    second = versions.require(strategy_id, newer)

    left, right = _comparable(first, second)
    return VersionDiff(
        older=first,
        newer=second,
        head_version=head.version,
        groups=tuple(diff_by_section(left.mapping, right.mapping)),
        older_yaml=left.text,
        newer_yaml=right.text,
    )


def count_runs(work: UnitOfWork, strategy_id: UUID) -> int:
    """How many runs exist for a strategy, for the save panel's staleness warning."""
    return RunRepo(work.connection).count(strategy_id=strategy_id)
