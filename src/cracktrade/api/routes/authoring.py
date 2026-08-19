"""Writing a configuration from a description of what it should do.

One route, and it creates nothing. It answers with a file and the validator's opinion of it;
adopting that file is a separate, deliberate act by the user through
``POST /strategies/import`` or ``POST /strategies/{id}/versions``, which are the same paths a
hand-written strategy takes. Generation that stored its own output would put a machine's draft
into the user's strategy list without anyone having read it, which is the one thing this
engine's whole design is against.

Declared ``async def``, against the rule in :mod:`cracktrade.api.app` that routes are
synchronous. The rule exists because the data layer is synchronous and Starlette runs a sync
route in its threadpool; this route touches no database at all. What it does do is wait --
minutes, potentially -- on a subprocess, and a threadpool worker blocked that long is one
fewer worker for every request that does need the database. So it is a real exception with a
real reason, not an oversight.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from cracktrade.api.dependencies import Settings
from cracktrade.api.schemas.requests import GenerateConfigRequest
from cracktrade.api.schemas.responses import GenerateConfigResponse
from cracktrade.api.services.authoring import generate_config
from cracktrade.authoring import Drafter, build_brief
from cracktrade.authoring.agent import agent_drafter

router = APIRouter(tags=["authoring"])


def drafter_of(settings: Settings) -> Drafter:
    """The thing that actually writes a draft.

    A dependency rather than a call inside the route, so a test can replace it with a scripted
    answer and exercise everything around it: the retry accounting, the validator wired to the
    editor's, the translation of an unreachable model into a 503.

    The brief is rebuilt per request. It costs a schema render and no I/O, and it carries
    today's date -- a cached one would quietly start telling the model it was some day last
    month, which is exactly the kind of stale fact that produces an intraday range the provider
    will not serve.
    """
    return agent_drafter(
        brief=build_brief(),
        model=settings.generate_model,
        timeout_seconds=settings.generate_timeout_seconds,
    )


#: The drafter, as an annotation the route declares.
Drafting = Annotated[Drafter, Depends(drafter_of)]


@router.post("/config/generate", response_model=GenerateConfigResponse)
async def post_generate(
    body: GenerateConfigRequest, settings: Settings, drafter: Drafting
) -> GenerateConfigResponse:
    """Draft a strategy file from a description, or revise one that already exists.

    Under ``/config`` rather than ``/strategies`` for the same reason ``/config/validate`` is:
    what comes back is a configuration, and a configuration is not a strategy until someone
    stores it.

    The draft is checked against the engine's validator and sent back to be fixed if it fails,
    up to ``generate_max_attempts`` times. A draft that never validates is still returned --
    with ``review.valid`` false and the errors attached -- because the user can see it, edit
    it, and adopt it, and none of that is possible if the server keeps it.

    ``503`` when generation is switched off, or when the model could not be reached at all: the
    ``claude`` CLI is missing from the server's PATH, its sign-in has expired, or the request
    timed out. The detail carries what the tool itself said, which is the only version of that
    message a user can act on.
    """
    return GenerateConfigResponse.of(
        await generate_config(
            settings,
            instruction=body.instruction,
            base_yaml=body.base_yaml,
            drafter=drafter,
        )
    )
