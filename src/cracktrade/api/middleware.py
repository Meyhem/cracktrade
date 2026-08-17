"""Request identity and the access log.

Every response carries an ``x-request-id``, every log line written while handling that request
carries the same one, and every problem+json body repeats it. That is the whole point: when a
user reports "it said 500", the id in front of them is enough to find the exact lines, without
guessing from timestamps which of several concurrent requests theirs was.

A client-supplied ``x-request-id`` is honoured so a trace survives a proxy in front, but it is
bounded and stripped of anything that would let a header inject a line break into the log.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from cracktrade.log import get_logger

logger = get_logger(__name__)

HEADER = "x-request-id"

#: Longest client-supplied id accepted. Beyond this it is replaced rather than truncated: a
#: truncated id is a *different* id that looks like the caller's, which is worse than a new one.
MAX_ID_LENGTH = 64

#: The id of the request being handled, for anything that logs outside the route's own frame.
#: A ContextVar rather than a parameter because the exception handler and the repositories are
#: nowhere near the middleware that assigned it.
request_id: ContextVar[str] = ContextVar("request_id", default="-")


def _safe(value: str | None) -> str:
    """A usable id from an untrusted header, or a fresh one.

    Only unreserved URL characters survive. A header is attacker-controlled input, and a log
    file is read by people and by tools that split on newlines -- a value carrying one would
    let a caller forge log entries.
    """
    if not value or len(value) > MAX_ID_LENGTH:
        return uuid.uuid4().hex
    cleaned = "".join(character for character in value if character.isalnum() or character in "-_")
    return cleaned or uuid.uuid4().hex


class RequestContext(BaseHTTPMiddleware):
    """Assign an id, log the exchange, and return the id on the response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        identifier = _safe(request.headers.get(HEADER))
        token = request_id.set(identifier)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Logged here as well as by the handler above, because this is the only frame that
            # still knows how long it ran and which request it was.
            logger.exception(
                "%s %s failed after %.0fms [%s]",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
                identifier,
            )
            raise
        finally:
            request_id.reset(token)

        elapsed = (time.perf_counter() - started) * 1000
        # Server errors are worth a line at WARNING even when the interface is quiet; ordinary
        # traffic is INFO, and a health probe every few seconds should not drown either.
        level = logger.warning if response.status_code >= 500 else logger.info
        level(
            "%s %s -> %d in %.0fms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
            identifier,
        )
        response.headers[HEADER] = identifier
        return response
