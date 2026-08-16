"""Launching runs, reading them, and fetching the series their charts are drawn from."""

from __future__ import annotations

import csv
import io
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from cracktrade.api.dependencies import Work
from cracktrade.api.schemas.runs import (
    LaunchRunRequest,
    RunDetail,
    RunList,
    RunOut,
    SeriesCatalog,
    SeriesPoints,
)
from cracktrade.api.services.runs import (
    kind_named,
    launch,
    list_runs,
    parse_kinds,
    parse_statuses,
    request_cancellation,
    require_run,
    series_catalog,
    series_points,
)

router = APIRouter(tags=["runs"])

#: Series stored as dates + values; the rest declare their own columns.
_COLUMN_ORDER = ("dates", "months", "values", "in_market")


@router.post(
    "/strategies/{strategy_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_run(strategy_id: UUID, body: LaunchRunRequest, work: Work) -> RunOut:
    """Queue a run. 202: it is accepted, not finished.

    The caller returns to the strategy immediately and watches progress over ``/events``; the
    worker picks the run up from the queue.
    """
    run = launch(
        work,
        strategy_id=strategy_id,
        kind=kind_named(body.kind),
        params=body.params,
        seed=body.seed,
    )
    return RunOut.of(require_run(work, run.id))


@router.get("/runs", response_model=RunList)
def get_runs(
    work: Work,
    strategy_id: Annotated[UUID | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    """The run tabs, the all-runs page, the queue, and the chart picker."""
    listing = list_runs(
        work,
        strategy_id=strategy_id,
        kinds=parse_kinds(kind),
        statuses=parse_statuses(run_status),
        limit=limit,
        offset=offset,
    )
    return RunList(runs=[RunOut.of(row) for row in listing.rows], total=listing.total)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: UUID, work: Work) -> RunDetail:
    """One run, with the engine's serialised result passed through verbatim."""
    row = require_run(work, run_id)
    return RunDetail(run=RunOut.of(row), result=row.run.result, error=row.run.error)


@router.post("/runs/{run_id}/cancel", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def post_cancel(run_id: UUID, work: Work) -> RunOut:
    """Ask a run to stop. A queued run ends at once; a running one at its next checkpoint."""
    request_cancellation(work, run_id)
    return RunOut.of(require_run(work, run_id))


@router.get("/runs/{run_id}/series", response_model=SeriesCatalog)
def get_series_catalog(run_id: UUID, work: Work) -> SeriesCatalog:
    """Which series this run captured. Fold 0 is the whole run."""
    return SeriesCatalog(series=series_catalog(work, run_id))


# The CSV route is declared *before* the generic one deliberately. Starlette matches in
# declaration order and a path parameter happily swallows a dot, so with the generic route
# first every request for `equity.csv` was answered by it -- looking for a series literally
# named "equity.csv" and 404ing. Order is the fix; a test covers it.
@router.get("/runs/{run_id}/series/{name}.csv")
def get_series_csv(
    run_id: UUID, name: str, work: Work, fold: Annotated[int, Query(ge=0)] = 0
) -> Response:
    """The same series as CSV, streamed from the same stored points.

    The chart and the file are therefore the same bytes: an export that recomputed anything
    could disagree with the picture it claims to be.
    """
    points = series_points(work, run_id, name, fold)
    columns = [column for column in _COLUMN_ORDER if column in points]
    columns += [column for column in points if column not in columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(zip(*(points[column] for column in columns), strict=False))

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{name}_fold{fold}.csv"'},
    )


@router.get("/runs/{run_id}/series/{name}", response_model=SeriesPoints)
def get_series(
    run_id: UUID, name: str, work: Work, fold: Annotated[int, Query(ge=0)] = 0
) -> SeriesPoints:
    """One captured series, exactly as it was stored at run time."""
    return SeriesPoints(name=name, fold=fold, points=series_points(work, run_id, name, fold))
