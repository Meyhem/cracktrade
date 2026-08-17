"""``GET /meta`` and ``GET /health`` -- what a client needs before its first render, and what
an operator needs before trusting the process."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from cracktrade.api.dependencies import Pool
from cracktrade.api.schemas.responses import HealthResponse, MetaResponse, meta_payload
from cracktrade.api.services.health import check
from cracktrade.api.services.meta import describe_engine

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
def get_meta() -> MetaResponse:
    """Constants, the indicator catalogue, and launch defaults."""
    return meta_payload(describe_engine())


@router.get("/health", response_model=HealthResponse)
def get_health(pool: Pool, response: Response) -> HealthResponse:
    """Whether the database is reachable and its schema is the one this build expects.

    503 when it is not, so a probe fails on the condition rather than on the first request that
    happens to touch a missing table. The body still describes what is wrong -- an unhealthy
    answer that says only "unhealthy" sends the reader to the logs for something the check
    already knows.
    """
    report = check(pool)
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse.of(report)
