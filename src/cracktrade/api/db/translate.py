"""Turning database refusals into the interface's own error taxonomy.

The schema refuses two very different kinds of write, and conflating them would be a
reporting failure. A unique violation or a foreign key that does not resolve is usually a
*race*: the request was well formed and would have succeeded a moment earlier, so it is a
client-visible conflict. A fired trigger or a violated CHECK is not: those guard invariants
the service layer is supposed to uphold, so reaching one means the code above is wrong, and it
is reported as a server error with the detail preserved.

Mapping here rather than in each repository means one place decides, and a new constraint
gets a considered translation instead of whichever exception happened to escape first.
"""

from __future__ import annotations

import psycopg

from cracktrade.api.errors import ApiError, ConflictError, InvariantViolationError

#: PostgreSQL SQLSTATE codes this module distinguishes. The rest are covered by the default.
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"

#: ``RAISE EXCEPTION`` in plpgsql without an explicit SQLSTATE. Every append-only trigger in
#: the schema lands here, and every one of them means a bug above this layer.
RAISED_EXCEPTION = "P0001"

#: Constraints whose violation is a race rather than a bug, with the message the client gets.
#: Named individually: a constraint absent from this mapping is reported as an invariant
#: violation, which is the safe direction to be wrong in -- a 500 gets investigated, while a
#: 409 on a genuine bug gets retried forever.
RACE_CONSTRAINTS: dict[str, str] = {
    "strategy_name_unique": "a strategy with that name already exists",
    "strategy_version_number_unique": "the strategy was modified concurrently; reload and retry",
    "strategy_version_pair_idx": "the strategy was modified concurrently; reload and retry",
    "run_number_unique": "a run was launched concurrently; retry",
    "prospect_session_name_unique": "a prospecting session with that name already exists",
}


def _constraint_of(error: psycopg.Error) -> str | None:
    diagnostic = error.diag.constraint_name
    return str(diagnostic) if diagnostic else None


def translate(error: psycopg.Error) -> ApiError:
    """Map a psycopg error onto the interface taxonomy.

    Anything unrecognised becomes an :class:`InvariantViolationError`, deliberately: an
    unfamiliar database failure is a bug until someone has looked at it, and reporting it as a
    client error would hide it behind a status the client is expected to see.
    """
    state = error.sqlstate
    constraint = _constraint_of(error)
    detail = str(error).strip()

    if state == UNIQUE_VIOLATION and constraint in RACE_CONSTRAINTS:
        return ConflictError(RACE_CONSTRAINTS[constraint])

    if state == FOREIGN_KEY_VIOLATION:
        # The referenced strategy, version or run does not exist -- either the client named
        # something gone, or another request removed the ground under this one. Both are
        # conflicts from here; only the service layer knows which, and it can say so.
        return ConflictError(f"a referenced record does not exist ({constraint or 'foreign key'})")

    # Everything else -- a fired append-only trigger (P0001), a CHECK the service layer should
    # have satisfied, a NOT NULL it should have filled, and anything unfamiliar -- is a defect
    # above this layer. The detail is carried through verbatim so the log says which.
    return InvariantViolationError(detail)
