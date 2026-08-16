"""Error taxonomy for the HTTP interface.

Distinct from :mod:`cracktrade.errors`, which is the *engine's* taxonomy. These describe what
went wrong with a **request**: the thing asked for does not exist, the state it assumed has
moved on, the config it carried is invalid. Engine exceptions are translated into these at the
service boundary, once, so that exactly one place decides how a library failure looks over
HTTP.

Every error here carries the status and the ``type`` slug it renders as, so the problem+json
handler is a rendering step with no decisions of its own (spec section 15).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class FieldIssue:
    """One validation failure, addressed to the field that caused it.

    The shape of ``docs/ENGINE_SPEC.md`` section 3.9: a dotted path, a message, the YAML line
    where recoverable, and a did-you-mean suggestion for an unknown key. The editor attaches
    each of these to the field it names, so a config error lands where it can be fixed rather
    than in a banner.
    """

    path: str
    message: str
    line: int | None = None
    suggestion: str | None = None


class ApiError(Exception):
    """Base class for every failure the interface reports to a client.

    Subclasses set :attr:`status` and :attr:`slug`. Anything *not* derived from this class
    escaping a route is a bug, and is reported as 500 with the traceback preserved -- never
    flattened into a plausible-looking response (defect D10's rule, applied to the interface).
    """

    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    slug: str = "internal-error"
    title: str = "Internal error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(ApiError):
    """The addressed strategy, version, run, or series does not exist."""

    status = HTTPStatus.NOT_FOUND
    slug = "not-found"
    title = "Not found"


class ConflictError(ApiError):
    """The request assumed a state the database no longer holds.

    A save whose ``base_version`` is no longer the head, a duplicate strategy name, a promotion
    of a run that did not succeed, a save that changes nothing. Distinct from a validation
    failure: the request is well-formed, and would have been accepted a moment ago.
    """

    status = HTTPStatus.CONFLICT
    slug = "conflict"
    title = "Conflict"


class ValidationFailedError(ApiError):
    """A submitted configuration is invalid.

    Carries the per-field issues rather than a single blob, because the save is rejected
    outright and the editor has to show the user where (spec section 3.9). Warnings never
    appear here -- they do not block, so they travel on successful responses.
    """

    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "config-invalid"
    title = "Configuration invalid"

    def __init__(self, detail: str, issues: Sequence[FieldIssue] = ()) -> None:
        self.issues = tuple(issues)
        super().__init__(detail)


class InvariantViolationError(ApiError):
    """The database refused a write that the service layer should have prevented.

    Raised when an append-only trigger or a CHECK constraint fires: those guard against bugs,
    not against user input, so reaching one means the code above is wrong. Deliberately a 500
    -- reporting it as a client error would hide a defect behind a plausible response.
    """

    status = HTTPStatus.INTERNAL_SERVER_ERROR
    slug = "invariant-violation"
    title = "Invariant violation"
