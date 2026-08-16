"""Process exit codes.

Distinct codes so the CLI is scriptable: a caller can tell a bad strategy file from an
unreachable data provider without parsing stderr.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Exit status returned by the ``cracktrade`` executable."""

    OK = 0
    #: An unexpected error. Indicates a bug; a traceback is shown with --verbose.
    INTERNAL = 1
    #: The strategy file is missing, malformed, or fails validation.
    CONFIG = 2
    #: Market data could not be obtained or violates the engine's contract.
    DATA = 3
    #: The simulation or the parameter search failed.
    ENGINE = 4
    #: The engine refused an operation that would introduce look-ahead bias.
    CAUSALITY = 5
