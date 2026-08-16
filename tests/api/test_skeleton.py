"""Phase 0: the API layer's shape, before it does anything.

Two properties worth pinning this early. The console script's surface is one: ``serve``,
``worker`` and ``db`` are the contract the compose file, the README and every later phase are
written against, and a subcommand that silently disappears would be found much later. The
other is that an unbuilt subcommand *fails* -- a command that exits zero while doing nothing is
the interface-layer version of returning a plausible zero, which is the defect this project
exists to avoid.
"""

from __future__ import annotations

import pytest

from cracktrade.api.errors import (
    ApiError,
    ConflictError,
    FieldIssue,
    InvariantViolationError,
    NotFoundError,
    ValidationFailedError,
)
from cracktrade.api.main import DATABASE_ERROR, NOT_IMPLEMENTED, main
from cracktrade.api.settings import DEFAULT_DATABASE_URL
from tests.api.conftest import IsolatedApiSettings

# --------------------------------------------------------------------------- console script


def test_help_exits_zero() -> None:
    assert main(["--help"]) == 0


@pytest.mark.parametrize("argv", [["serve"], ["worker"]])
def test_registered_but_unbuilt_subcommands_fail(argv: list[str]) -> None:
    """An unimplemented command reports failure rather than a quiet success.

    The ``db`` subcommands were on this list until phase 2 built them; their behaviour is now
    covered by ``test_migrate.py`` against a real database.
    """
    assert main(argv) == NOT_IMPLEMENTED


@pytest.mark.parametrize("argv", [["db", "migrate"], ["db", "status"], ["db", "verify"]])
def test_db_subcommands_report_an_unreachable_database(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that is not running is an operational condition, not a crash.

    It deserves a sentence naming the fix, and an exit code a script can act on -- not a
    traceback, and emphatically not a zero.
    """
    monkeypatch.setenv("CRACKTRADE_API_DATABASE_URL", "postgresql://nobody@localhost:59999/nope")
    assert main(argv) == DATABASE_ERROR


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert "cracktrade-api" in capsys.readouterr().out


def test_unknown_subcommand_is_not_a_success() -> None:
    assert main(["definitely-not-a-command"]) != 0


# --------------------------------------------------------------------------- settings


def test_defaults_match_the_compose_database() -> None:
    """Zero configuration works against a freshly composed development database."""
    settings = IsolatedApiSettings()
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert "5432" in settings.database_url


def test_binds_loopback_by_default() -> None:
    """There is no authentication (spec section 15.3, D-5), so exposure must be deliberate."""
    assert IsolatedApiSettings().host == "127.0.0.1"


def test_reads_the_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRACKTRADE_API_DATABASE_URL", "postgresql://x:y@example:5555/z")
    monkeypatch.setenv("CRACKTRADE_API_PORT", "9001")
    settings = IsolatedApiSettings()
    assert settings.database_url == "postgresql://x:y@example:5555/z"
    assert settings.port == 9001


def test_engine_settings_are_not_shadowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CRACKTRADE_SEED`` configures the engine, not the interface.

    The prefixes differ by one word, and a collision would let an interface setting silently
    change a computed result -- the one thing the API layer must never do.
    """
    monkeypatch.setenv("CRACKTRADE_SEED", "12345")
    assert not hasattr(IsolatedApiSettings(), "seed")


def test_lease_outlives_the_heartbeat() -> None:
    """A worker must get to beat before its lease is judged stale (spec section 14.5)."""
    settings = IsolatedApiSettings()
    assert settings.worker_lease_seconds > settings.worker_heartbeat_seconds


# --------------------------------------------------------------------------- error taxonomy


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("gone"), 404),
        (ConflictError("moved on"), 409),
        (ValidationFailedError("bad config"), 422),
        (InvariantViolationError("trigger fired"), 500),
    ],
)
def test_errors_carry_their_own_status(error: ApiError, status: int) -> None:
    """The problem+json handler renders; it does not decide."""
    assert error.status == status
    assert error.slug
    assert error.title


def test_validation_failure_keeps_issues_addressed_to_fields() -> None:
    """Section 3.9's contract: errors attach to the field that caused them."""
    issue = FieldIssue(path="exit", message="at least one exit mechanism must be set", line=14)
    error = ValidationFailedError("invalid", [issue])
    assert error.issues == (issue,)
    assert error.issues[0].path == "exit"


def test_invariant_violation_is_a_server_error() -> None:
    """A fired trigger means the layer above was wrong, so it must not read as a client error."""
    assert InvariantViolationError("append-only").status == 500
