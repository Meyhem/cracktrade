"""Version history, diffs, saves and restores.

The append-only half of the surface. Nothing here updates or deletes; a save appends, and so
does a restore.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from cracktrade.api.dependencies import Work
from cracktrade.api.schemas.requests import RestoreVersionRequest, SaveVersionRequest
from cracktrade.api.schemas.responses import (
    ChangeOut,
    DiffResponse,
    SavedVersion,
    SectionDiffOut,
    VersionOut,
    VersionSummary,
    VersionSummaryRef,
)
from cracktrade.api.services.strategies import restore_version, save_version
from cracktrade.api.services.versions import compare, count_runs, history
from cracktrade.api.services.versions import get_version as read_version

router = APIRouter(tags=["versions"])


@router.get("/strategies/{strategy_id}/versions", response_model=list[VersionSummary])
def list_versions(strategy_id: UUID, work: Work) -> list[VersionSummary]:
    """The history timeline, newest first.

    Each entry's change summary is computed against its predecessor at read time. Storing it
    would let a summary drift from the configs it claims to describe.
    """
    return [
        VersionSummary(
            version=entry.version.version,
            head=entry.head,
            origin=entry.version.origin.value,
            restored_from=entry.version.restored_from,
            note=entry.version.note,
            created_at=entry.version.created_at,
            change_summary=[ChangeOut.of(change) for change in entry.changes],
            flags=list(entry.flags),
            runs_against=entry.runs_against,
        )
        for entry in history(work, strategy_id)
    ]


@router.get("/strategies/{strategy_id}/versions/{version}", response_model=VersionOut)
def get_version(strategy_id: UUID, version: int, work: Work) -> VersionOut:
    """One immutable version, exactly as it was saved."""
    return VersionOut.of(read_version(work, strategy_id, version))


@router.get("/strategies/{strategy_id}/diff", response_model=DiffResponse)
def diff_versions(
    strategy_id: UUID,
    work: Work,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> DiffResponse:
    """Compare two versions, grouped by config section."""
    result = compare(work, strategy_id, older=from_version, newer=to_version)
    return DiffResponse(
        from_version=VersionSummaryRef.of(
            result.older, head=result.older.version == result.head_version
        ),
        to_version=VersionSummaryRef.of(
            result.newer, head=result.newer.version == result.head_version
        ),
        groups=[SectionDiffOut.of(section) for section in result.groups],
        from_yaml=result.older.config_yaml,
        to_yaml=result.newer.config_yaml,
    )


@router.post(
    "/strategies/{strategy_id}/versions",
    response_model=SavedVersion,
    status_code=status.HTTP_201_CREATED,
)
def create_version(strategy_id: UUID, body: SaveVersionRequest, work: Work) -> SavedVersion:
    """Save an edit as the next version.

    Refused with 409 if the head moved under the edit, and refused if nothing changed.
    """
    stale = count_runs(work, strategy_id)
    version = save_version(
        work,
        strategy_id=strategy_id,
        base_version=body.base_version,
        config=body.config,
        yaml_text=body.yaml,
        note=body.note,
    )
    return SavedVersion(version=VersionOut.of(version), stale_runs=stale)


@router.post(
    "/strategies/{strategy_id}/versions/{version}/restore",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def restore(strategy_id: UUID, version: int, body: RestoreVersionRequest, work: Work) -> VersionOut:
    """Append a copy of an older version at the head. Nothing is rewound."""
    return VersionOut.of(
        restore_version(work, strategy_id=strategy_id, version=version, note=body.note)
    )
