"""``GET /meta`` -- the engine facts a client needs before its first render."""

from __future__ import annotations

from fastapi import APIRouter

from cracktrade.api.schemas.responses import MetaResponse, meta_payload
from cracktrade.api.services.meta import describe_engine

router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
def get_meta() -> MetaResponse:
    """Constants, the indicator catalogue, and launch defaults."""
    return meta_payload(describe_engine())
