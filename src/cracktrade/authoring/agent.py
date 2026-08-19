"""Drafting through the Claude Agent SDK, which runs the user's own ``claude`` CLI.

The engine has no API key and should not want one. A user running this already pays for Claude
Code, and the Agent SDK reaches that subscription by spawning the ``claude`` binary they
already have logged in -- so generation costs them nothing beyond what they have, and no
credential is read, stored, or passed by this codebase.

The session it opens is as small as the SDK allows. No tools, one turn, no filesystem settings
and no skills: this call writes a YAML document from a description and must not be able to read
a file, run a command, or pick up instructions from whatever directory the server happens to
have been started in. ``setting_sources=[]`` is load-bearing for that -- the field's default of
``None`` loads *every* source, which is the opposite of what its name suggests.

Structured output does the rest. The model returns ``{yaml, notes}`` against a schema rather
than prose with a fenced block in it, so there is no parsing step to get wrong and no way for a
sentence of commentary to end up inside the strategy file.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    ResultMessage,
    query,
)

from cracktrade.authoring.generate import Draft, Drafter
from cracktrade.errors import AuthoringUnavailableError

#: The model that writes strategies. The most capable one available: the schema is exact, the
#: grammar is unusual, and the cost of a draft that is subtly not what was asked for lands on
#: someone deciding where to put money.
DEFAULT_MODEL: Final = "claude-opus-5"

#: How long one draft may take before the request is abandoned. Generous, because the brief is
#: long and a retry re-reads it; finite, because an HTTP request nobody is waiting on any more
#: still holds a connection.
DEFAULT_TIMEOUT_SECONDS: Final = 180.0

#: The shape of a draft. ``additionalProperties: false`` so a model that decides to explain
#: itself in an extra key fails the schema rather than smuggling prose into the response.
OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "yaml": {
            "type": "string",
            "description": (
                "The complete strategy file as YAML. No fences, no commentary, no leading or "
                "trailing prose -- this string is parsed as a strategy exactly as it arrives."
            ),
        },
        "notes": {
            "type": "string",
            "description": (
                "A few sentences for the user: what the strategy does, what you had to assume "
                "because they did not say, and anything you could not express. Never a claim "
                "or a prediction about how it will perform."
            ),
        },
    },
    "required": ["yaml", "notes"],
    "additionalProperties": False,
}


def _draft_from(structured: dict[str, Any] | None) -> Draft:
    """Read a draft out of a structured result, refusing to guess at a malformed one."""
    if not isinstance(structured, dict):
        msg = "the strategy writer returned no structured output"
        raise AuthoringUnavailableError(msg)
    text, notes = structured.get("yaml"), structured.get("notes", "")
    if not isinstance(text, str) or not text.strip():
        msg = "the strategy writer returned an empty file"
        raise AuthoringUnavailableError(msg)
    return Draft(yaml=text, notes=notes if isinstance(notes, str) else "")


def agent_drafter(
    *,
    brief: str,
    model: str = DEFAULT_MODEL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Drafter:
    """Build a :data:`~cracktrade.authoring.generate.Drafter` backed by the Agent SDK.

    The brief is bound here rather than sent with every prompt: it is the system prompt, it is
    the same for every attempt of every request, and keeping it out of the loop is what lets
    the loop be tested against a function that returns a fixed string.

    Raises:
        AuthoringUnavailableError: at call time, if the CLI is absent, cannot authenticate, or
            does not answer within ``timeout_seconds``.
    """
    options = ClaudeAgentOptions(
        system_prompt=brief,
        model=model,
        output_format={"type": "json_schema", "schema": OUTPUT_SCHEMA},
        # One turn, nothing to call, nothing on disk to read. A drafting session that could
        # use a tool is a drafting session that can be talked into using one.
        max_turns=1,
        allowed_tools=[],
        setting_sources=[],
        skills=[],
    )

    async def draft(prompt: str) -> Draft:
        structured: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, ResultMessage):
                        if message.is_error:
                            detail = message.result or message.subtype
                            msg = f"the strategy writer failed: {detail}"
                            raise AuthoringUnavailableError(msg)
                        structured = message.structured_output
        except TimeoutError as error:
            msg = f"the strategy writer did not answer within {timeout_seconds:.0f}s"
            raise AuthoringUnavailableError(msg) from error
        except CLINotFoundError as error:
            msg = (
                "the `claude` CLI is not installed or not on this server's PATH, so strategies "
                "cannot be generated. Install Claude Code and sign in"
            )
            raise AuthoringUnavailableError(msg) from error
        except ClaudeSDKError as error:
            # Whatever the CLI said, verbatim. "Generation failed" is unactionable; "OAuth
            # session expired and could not be refreshed" tells the user to run `claude` once.
            msg = f"the strategy writer could not be reached: {error}"
            raise AuthoringUnavailableError(msg) from error
        return _draft_from(structured)

    return draft
