"""Drafting through the Claude Agent SDK, which runs the user's own ``claude`` CLI.

The engine has no API key and should not want one. A user running this already pays for Claude
Code, and the Agent SDK reaches that subscription by spawning the ``claude`` binary they
already have logged in -- so generation costs them nothing beyond what they have, and no
credential is read, stored, or passed by this codebase.

The session it opens is narrow, and narrow in a specific direction: it may **read the web and
nothing else**. Search and fetch are allowed because a description of a trading idea routinely
names a company rather than a symbol, and because whether a ticker existed across the requested
date range is a fact that can be checked rather than guessed. Everything that writes, executes,
or reads this machine is denied -- by omission from ``allowed_tools`` and again by name in
``disallowed_tools``, because a deny that names the tool survives a change in what the default
permission gate does.

``setting_sources=[]`` and ``skills=[]`` are load-bearing rather than tidy. Both fields default
to ``None``, which loads *every* source -- so leaving them unset would let a ``CLAUDE.md`` in
whatever directory the server was started from reach the drafting session.

The web tools bring untrusted text into a session that then writes a configuration, which is a
prompt-injection surface and is treated as one. Three things contain it, none of which relies on
the model behaving: the structured output schema fixes the response shape, the engine's validator
judges the file regardless of what any page said, and no proposal reaches storage without a
person reading it. The brief additionally tells the model to treat page contents as information
and never as instruction.

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

#: Read-only web access, and the whole of the drafting session's reach beyond its own prompt.
#:
#: ``WebSearch`` resolves what a person said into what a provider serves -- a company name into
#: a symbol, a symbol into the exchange-suffixed listing that actually has the currency and the
#: hours the user meant. ``WebFetch`` reads the page that search found. Neither can change
#: anything, which is why they are the two that are here.
RESEARCH_TOOLS: Final[tuple[str, ...]] = ("WebSearch", "WebFetch")

#: Denied by name as well as by omission.
#:
#: Omitting a tool from ``allowed_tools`` already means it is not auto-approved, and a
#: non-interactive session has nobody to approve it. That is a property of today's permission
#: gate, though, and this list is a property of the feature: drafting a YAML document has no
#: business touching a filesystem, a shell, or a subagent, and saying so explicitly means a
#: change in the gate's defaults cannot quietly widen what this session can do.
DENIED_TOOLS: Final[tuple[str, ...]] = (
    "Bash",
    "BashOutput",
    "Edit",
    "Glob",
    "Grep",
    "KillShell",
    "NotebookEdit",
    "Read",
    "Skill",
    "Task",
    "TodoWrite",
    "Write",
)

#: How many exchanges one draft may take. Research is a loop -- search, read, search again --
#: so a single turn is no longer enough; a dozen is room to check two or three facts and then
#: answer, and a ceiling on a session that has started chasing links instead of writing a file.
DEFAULT_MAX_TURNS: Final = 12

#: How long one draft may take before the request is abandoned. Generous, because the brief is
#: long, a retry re-reads it, and research adds round trips of its own; finite, because an HTTP
#: request nobody is waiting on any more still holds a connection.
DEFAULT_TIMEOUT_SECONDS: Final = 300.0

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
                "A few sentences for the user: what the strategy does, what you had to "
                "assume because they did not say, anything you looked up and what you took "
                "from it, and anything you could not express. Never a claim or a prediction "
                "about how it will perform."
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


def build_options(*, brief: str, model: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    """The drafting session's shape, in one place so a test can assert on it.

    Separated from :func:`agent_drafter` because what this session is *allowed to do* is the
    security-relevant part of the feature, and a property that only exists inside a closure is
    a property nothing can check.
    """
    return ClaudeAgentOptions(
        system_prompt=brief,
        model=model,
        output_format={"type": "json_schema", "schema": OUTPUT_SCHEMA},
        max_turns=DEFAULT_MAX_TURNS,
        allowed_tools=list(RESEARCH_TOOLS),
        disallowed_tools=list(DENIED_TOOLS),
        # Not tidiness: both default to None, which loads every filesystem source. Unset, a
        # CLAUDE.md beside the server's working directory would be read into this session.
        setting_sources=[],
        skills=[],
    )


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
    options = build_options(brief=brief, model=model)

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
