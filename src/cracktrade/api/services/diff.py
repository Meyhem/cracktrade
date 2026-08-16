"""Comparing two configurations.

Computed from the stored configs every time, never stored. A saved diff is a second description
of a relationship between two things that already exist, and it can only ever go out of date
with them.

The output is grouped by config section because that is how consequences group. Two changed
numbers under ``execution`` are not two facts, they are one: the cost model moved, and every
run made before it measured a different question.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Config sections, in the order the diff view lists them.
SECTIONS: tuple[str, ...] = (
    "strategy",
    "universe",
    "execution",
    "indicators",
    "entry",
    "exit",
    "position_sizing",
)

#: Sections whose change invalidates comparison with earlier runs, and what to say about it.
CONSEQUENCES: dict[str, str] = {
    "execution": (
        "The cost model changed, so every run made before this version measured a different "
        "question. Numbers either side of it compare experiments, not strategies."
    ),
    "universe": (
        "The universe changed, so runs before and after this version are not comparable: they "
        "cover different history."
    ),
}


@dataclass(frozen=True, slots=True)
class Change:
    """One leaf value that differs between two configurations."""

    path: str
    old: Any
    new: Any


@dataclass(frozen=True, slots=True)
class SectionDiff:
    """Everything that changed inside one config section, and what it means."""

    section: str
    changes: tuple[Change, ...]
    consequence: str | None

    @property
    def changed(self) -> bool:
        """Whether this section differs at all."""
        return bool(self.changes)


def flatten(config: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Config as dotted leaf paths.

    Indicators are keyed by their ``name`` rather than by list position, so inserting one at
    the top does not read as every indicator having changed. That is the same addressing the
    optimizer uses for parameter paths (spec section 9.1), so a diff path and a searchable
    parameter path are the same string.
    """
    leaves: dict[str, Any] = {}
    for key, value in config.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            leaves.update(flatten(value, f"{path}."))
        elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
            for index, item in enumerate(value):
                name = item.get("name", index)
                leaves.update(flatten(item, f"{path}.{name}."))
        else:
            leaves[path] = value
    return leaves


def changes_between(old: dict[str, Any], new: dict[str, Any]) -> tuple[Change, ...]:
    """Every leaf that differs, added, or removed, in a stable order."""
    old_leaves = flatten(old)
    new_leaves = flatten(new)
    paths = sorted(set(old_leaves) | set(new_leaves))
    return tuple(
        Change(path=path, old=old_leaves.get(path), new=new_leaves.get(path))
        for path in paths
        if old_leaves.get(path) != new_leaves.get(path)
    )


def _section_of(path: str) -> str:
    head = path.split(".", 1)[0]
    return head if head in SECTIONS else "strategy"


def diff_by_section(old: dict[str, Any], new: dict[str, Any]) -> tuple[SectionDiff, ...]:
    """Group the differences by config section, including sections that did not change.

    Unchanged sections are listed too. "Indicators: no changes" is information -- it tells a
    reader the search space is the same -- and omitting it would leave them to infer that from
    an absence.
    """
    grouped: dict[str, list[Change]] = {section: [] for section in SECTIONS}
    for change in changes_between(old, new):
        grouped[_section_of(change.path)].append(change)

    return tuple(
        SectionDiff(
            section=section,
            changes=tuple(changes),
            consequence=CONSEQUENCES.get(section) if changes else None,
        )
        for section, changes in grouped.items()
    )


def summarise(old: dict[str, Any], new: dict[str, Any]) -> tuple[Change, ...]:
    """The flat change list the history timeline shows under each version."""
    return changes_between(old, new)


def flags_for(changes: tuple[Change, ...]) -> tuple[str, ...]:
    """Which sections changed in a way that makes earlier runs incomparable."""
    sections = {_section_of(change.path) for change in changes}
    return tuple(f"{section}_changed" for section in sorted(sections & set(CONSEQUENCES)))
