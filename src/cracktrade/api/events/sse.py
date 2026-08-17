"""Server-sent events, relayed from LISTEN/NOTIFY.

The one genuinely asynchronous component (spec section 15.4, D-13). It owns an async
connection of its own and needs no repository: it forwards notifications and nothing more.

Clients that cannot use SSE poll ``GET /runs?status=queued,running`` instead, which is why the
event payload carries no result -- both paths end in the same refetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import psycopg
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from cracktrade.api.events.notify import CHANNEL
from cracktrade.api.settings import ApiSettings
from cracktrade.log import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["events"])

#: How often to emit a comment when nothing is happening, so an idle connection is not closed
#: by a proxy that cannot tell it apart from a dead one.
KEEPALIVE_SECONDS = 20.0


async def _events(settings: ApiSettings, request: Request) -> AsyncIterator[dict[str, str]]:
    connection = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True)
    try:
        await connection.execute(f"LISTEN {CHANNEL}")
        generator = connection.notifies()
        while not await request.is_disconnected():
            try:
                notification = await asyncio.wait_for(
                    generator.__anext__(), timeout=KEEPALIVE_SECONDS
                )
            except TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue

            payload = json.loads(notification.payload)
            status = payload.get("status", "")
            yield {
                "event": "run.finished" if status not in {"queued", "running"} else "run.updated",
                "data": notification.payload,
            }
    finally:
        with contextlib.suppress(psycopg.Error):
            await connection.close()


@router.get("/events")
async def stream_events(request: Request) -> EventSourceResponse:
    """Live run updates. Payloads carry no result; clients refetch the run."""
    settings: ApiSettings = request.app.state.settings
    return EventSourceResponse(_events(settings, request))
