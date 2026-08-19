"""The generate-validate-retry loop.

A language model writing into an exact schema gets it wrong sometimes: a field that was
renamed, an indicator output that does not exist, a comparison whose parentheses turn it into
something the grammar rejects. All of those are things the engine's own validator can already
say precisely, on the field that caused them.

So the model does not get to declare its output valid. Every draft is handed to a
:class:`Reviewer` -- in practice the same validation path the editor calls on every keystroke
-- and a draft that fails comes back with the errors quoted, to be fixed rather than argued
with.

The retry budget is enforced here rather than described to the model, because a limit a model
is merely told about is a limit it can talk itself out of. When the budget runs out the last
draft is returned *with* its errors, not discarded: a nearly-right file the user can see and
correct is worth more than a failure that shows them nothing.

The loop is deliberately stateless. Each attempt is a fresh prompt carrying the previous
attempt and its errors, so there is no conversation to store, expire, or resume -- and a
refinement the user asks for weeks later works exactly like the first draft did.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

#: How many drafts a proposal may cost. Four is measured against nothing -- it is a budget,
#: not a discovery -- but it is chosen on a real asymmetry: the first retry, which quotes the
#: engine's own error text, fixes most of what the first draft gets wrong, and an attempt that
#: has failed three times with the errors in front of it is usually failing on the request
#: rather than on the spelling.
DEFAULT_MAX_ATTEMPTS: Final = 4


@dataclass(frozen=True, slots=True)
class Critique:
    """What a reviewer found in one draft.

    Errors and warnings are plain strings rather than the structured issues the HTTP layer
    renders, and deliberately so: this is the granularity that gets fed back to the model, and
    a field path it cannot address is noise in the prompt. The caller keeps the structured form
    for the user.
    """

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


#: Judges a YAML strategy file. Injected rather than imported because the library must not
#: depend on the interface that happens to have the validator wired up.
Reviewer = Callable[[str], Critique]


@dataclass(frozen=True, slots=True)
class Draft:
    """One attempt: the file, and what the model says it did."""

    yaml: str
    notes: str


#: Turns a prompt into a draft. The system brief and the model choice belong to the
#: implementation, not to the loop.
Drafter = Callable[[str], Awaitable[Draft]]


@dataclass(frozen=True, slots=True)
class Request:
    """What the user asked for.

    ``base_yaml`` is the strategy being changed, when there is one. Its presence is what
    separates the two things this module does -- writing a strategy and revising one -- and it
    changes the instruction rather than the loop: a revision must be recognisable as a change
    to the file it started from, which a fresh draft would not be.
    """

    instruction: str
    base_yaml: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    """A strategy file that nothing has been done with yet.

    ``valid`` false is a reportable outcome and not an error: the caller shows the user the
    file and what is wrong with it. What the caller must not do is store it -- a proposal
    becomes a strategy only when a user says so.
    """

    yaml: str
    notes: str
    attempts: int
    critique: Critique = field(default_factory=lambda: Critique(valid=False))

    @property
    def valid(self) -> bool:
        """Whether the engine accepted the file as written."""
        return self.critique.valid


def _quote(text: str) -> str:
    """Fence a YAML document so a model reading it back cannot mistake it for instructions."""
    return f"```yaml\n{text.rstrip()}\n```"


def _issues(label: str, messages: Sequence[str]) -> str:
    return "\n".join([f"{label}:", *(f"- {message}" for message in messages)])


def first_prompt(request: Request) -> str:
    """The opening instruction: write a strategy, or revise the one supplied."""
    if request.base_yaml is None:
        return (
            "Write a strategy file for this request:\n\n"
            f"{request.instruction.strip()}\n\n"
            "Return the complete file. Every choice you had to make for the user -- the ticker "
            "if they did not name one, the date range, the costs -- belongs in your notes."
        )
    return (
        "Revise this strategy file:\n\n"
        f"{_quote(request.base_yaml)}\n\n"
        "The change asked for:\n\n"
        f"{request.instruction.strip()}\n\n"
        "Return the complete file, not a fragment or a diff. Change what the request asks for "
        "and leave the rest alone: the user will read this as a diff against what they had, "
        "and an unrequested change is one they have to notice and undo. Say in your notes what "
        "you changed and why, and name anything you left alone that they might have expected "
        "you to touch."
    )


def retry_prompt(request: Request, draft: Draft, critique: Critique) -> str:
    """The follow-up: the same task, plus the file that failed and the engine's verdict on it."""
    return "\n\n".join(
        (
            first_prompt(request),
            "Your previous attempt was rejected by the engine's validator:",
            _quote(draft.yaml),
            _issues("The validator reported", critique.errors),
            "Fix exactly these. The messages are the validator's own and name the field that "
            "caused each one; they are not suggestions to weigh. Do not rewrite the parts that "
            "were accepted, and do not drop a requirement of the original request in order to "
            "make an error go away -- if a request cannot be expressed in this schema, write "
            "the closest valid file and say so plainly in your notes.",
        )
    )


async def propose(
    request: Request,
    *,
    drafter: Drafter,
    reviewer: Reviewer,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Proposal:
    """Draft a strategy file and retry against the validator until it is accepted.

    Returns the first draft the reviewer accepts. If none is accepted within ``max_attempts``,
    returns the last one with its critique attached -- an invalid proposal is an outcome, and
    the caller reports it rather than raising.

    Warnings never cause a retry. They are the engine's advice on a valid file (a shadowed
    stop, a range the provider may not reach) and they ride along on the proposal for the user
    to weigh; treating them as failures would spend the budget rewriting files that are already
    correct.

    Raises:
        ValueError: ``max_attempts`` is below one, which would return a proposal with no draft
            in it.
    """
    if max_attempts < 1:
        msg = f"max_attempts must be at least 1, got {max_attempts}"
        raise ValueError(msg)

    prompt = first_prompt(request)
    draft = Draft(yaml="", notes="")
    critique = Critique(valid=False)

    for attempt in range(1, max_attempts + 1):
        draft = await drafter(prompt)
        critique = reviewer(draft.yaml)
        if critique.valid:
            return Proposal(yaml=draft.yaml, notes=draft.notes, attempts=attempt, critique=critique)
        prompt = retry_prompt(request, draft, critique)

    return Proposal(yaml=draft.yaml, notes=draft.notes, attempts=max_attempts, critique=critique)
