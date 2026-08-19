"""Drafting a configuration from a description, and judging it with the editor's own validator.

This is the seam between the authoring library, which knows nothing about HTTP or about this
engine's validation plumbing, and :mod:`cracktrade.api.services.config`, which is what the
editor already calls on every keystroke. The library asks for a
:data:`~cracktrade.authoring.Reviewer`; this supplies one made of ``review_yaml``.

That indirection earns its keep twice. The library stays free of an interface import, and --
more usefully -- the file a generation returns has been through *exactly* the check the user's
editor will run on it a second later. A generator with a validator of its own would eventually
disagree with the editor, and the disagreement would surface as a file that passed generation
and then lit up red the moment the user opened it.

Nothing here writes. This produces a proposal; whether it becomes a strategy is the user's
decision, taken through the import and save-version routes that already exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from cracktrade.api.errors import FieldIssue, UpstreamUnavailableError
from cracktrade.api.services.config import ConfigReview, review_yaml
from cracktrade.api.settings import ApiSettings
from cracktrade.authoring import Critique, Drafter, Request, propose
from cracktrade.errors import AuthoringUnavailableError


@dataclass(frozen=True, slots=True)
class Generated:
    """A drafted configuration and everything known about it.

    ``review`` is a full :class:`~cracktrade.api.services.config.ConfigReview`, not a verdict:
    the client that is about to show this file in an editor wants the canonical YAML, the
    signal namespace and the searchable parameters as much as it wants to know whether it
    parsed, and asking for them in a second round-trip would only give the two answers a chance
    to disagree.
    """

    yaml: str
    notes: str
    attempts: int
    review: ConfigReview


def _render(issue: FieldIssue) -> str:
    """Flatten one field issue into the line the model is asked to fix.

    The path is kept because it is how the engine names the thing that is wrong, and a model
    that has just written the file knows exactly which key ``exit.max_holding_days`` is. The
    line number is dropped: it addresses the YAML the *user* is looking at, and a model
    rewriting the file whole has no use for a coordinate into the draft it is replacing.
    """
    body = f"{issue.path}: {issue.message}" if issue.path else issue.message
    return f"{body} ({issue.suggestion})" if issue.suggestion else body


def critique(text: str) -> Critique:
    """The editor's validator, in the shape the authoring loop expects."""
    review = review_yaml(text)
    return Critique(
        valid=review.valid,
        errors=tuple(_render(issue) for issue in review.errors),
        warnings=tuple(_render(issue) for issue in review.warnings),
    )


async def generate_config(
    settings: ApiSettings,
    *,
    instruction: str,
    base_yaml: str | None = None,
    drafter: Drafter,
) -> Generated:
    """Draft a strategy file, retrying against the validator until it is accepted.

    ``drafter`` arrives from the route's dependency rather than being built here. That is
    what keeps this function free of the Agent SDK, and what lets the endpoint be tested end to
    end against a scripted draft -- the parts worth testing are the retry accounting and the
    translation of a failure into a status code, neither of which needs a model.

    Raises:
        UpstreamUnavailableError: generation is switched off, or the model could not be
            reached. Never raised for a draft that came back invalid -- that is returned, with
            its errors, for the user to see and fix.
    """
    if not settings.generate_enabled:
        msg = "strategy generation is switched off on this server (CRACKTRADE_API_GENERATE_ENABLED)"
        raise UpstreamUnavailableError(msg)

    try:
        proposal = await propose(
            Request(instruction=instruction, base_yaml=base_yaml),
            drafter=drafter,
            reviewer=critique,
            max_attempts=settings.generate_max_attempts,
        )
    except AuthoringUnavailableError as error:
        raise UpstreamUnavailableError(str(error)) from error

    # Re-reviewed rather than carried through the loop: the loop trades in strings, which is
    # all a model can act on, and the client needs the structured form the editor renders.
    return Generated(
        yaml=proposal.yaml,
        notes=proposal.notes,
        attempts=proposal.attempts,
        review=review_yaml(proposal.yaml),
    )
