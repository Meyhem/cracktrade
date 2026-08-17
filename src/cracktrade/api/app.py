"""The FastAPI application.

Routes are declared ``def`` rather than ``async def`` on purpose (spec section 15.4, D-13):
the data layer is synchronous, and Starlette runs a sync route in its threadpool, so one
repository implementation serves both this process and the worker.

The exception handler is the only place a failure becomes a status code. Everything below
raises from the :mod:`cracktrade.api.errors` taxonomy, which already carries the status and the
slug, so this layer renders and decides nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cracktrade.api.db.pool import build_pool
from cracktrade.api.errors import ApiError, InvariantViolationError, ValidationFailedError
from cracktrade.api.events import sse
from cracktrade.api.middleware import HEADER as REQUEST_ID_HEADER
from cracktrade.api.middleware import RequestContext, request_id
from cracktrade.api.routes import meta, runs, strategies, versions
from cracktrade.api.settings import ApiSettings, load_api_settings
from cracktrade.log import get_logger

logger = get_logger(__name__)

#: Everything lives under one version prefix, so a breaking change can be introduced beside
#: the old surface rather than on top of it.
API_PREFIX = "/api/v1"

PROBLEM_JSON = "application/problem+json"


def _problem(error: ApiError, request: Request) -> JSONResponse:
    identifier = request_id.get()
    body: dict[str, Any] = {
        "type": f"https://cracktrade.invalid/errors/{error.slug}",
        "title": error.title,
        "status": int(error.status),
        "detail": error.detail,
        "instance": str(request.url.path),
        # Repeated in the body, not only the header: a user reporting a failure copies what is
        # on the screen, and what is on the screen is the body.
        "request_id": identifier,
    }
    if isinstance(error, ValidationFailedError):
        body["errors"] = [
            {
                "path": issue.path,
                "message": issue.message,
                "line": issue.line,
                "suggestion": issue.suggestion,
            }
            for issue in error.issues
        ]
    return JSONResponse(
        status_code=int(error.status),
        content=body,
        media_type=PROBLEM_JSON,
        headers={REQUEST_ID_HEADER: identifier},
    )


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build the application.

    ``settings`` is injectable so a test can point the app at its own database.
    """
    resolved = settings or load_api_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = build_pool(resolved)
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="cracktrade",
        version="1",
        summary="Strategy optimization engine",
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
    )
    app.state.settings = resolved
    app.add_middleware(RequestContext)

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        if isinstance(exc, InvariantViolationError):
            # A guard the service layer should have satisfied. Logged as the defect it is,
            # rather than quietly returned as though the client had done something wrong.
            logger.error(
                "invariant violation on %s: %s [%s]",
                request.url.path,
                exc.detail,
                request_id.get(),
            )
        return _problem(exc, request)

    app.include_router(meta.router, prefix=API_PREFIX)
    app.include_router(strategies.router, prefix=API_PREFIX)
    app.include_router(versions.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)
    app.include_router(sse.router, prefix=API_PREFIX)
    return app
