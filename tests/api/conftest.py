"""Fixtures for the API layer's tests.

The ``db``-marked tests run against a real PostgreSQL server -- the one in
``docker-compose.yml`` -- rather than against a substitute. Everything section 14 promises is
enforced *by the database*: append-only triggers, composite foreign keys, the CHECK constraints
that make an impossible run row impossible. A fake would only prove that the fake agrees with
itself.

Each test gets its own freshly created database, cloned from a session template, so a test can
insert whatever it likes and cannot influence the next one. Databases are created on the server
named by the connection URL; the database *in* that URL is only a connection target for
administrative statements and is never modified.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import TupleRow
from pydantic_settings import SettingsConfigDict

from cracktrade.api.app import create_app
from cracktrade.api.db.migrate import migrate
from cracktrade.api.settings import DEFAULT_DATABASE_URL, ApiSettings

#: Where the db-marked tests look for a server. Defaults to the compose database, so a fresh
#: clone needs no configuration: ``docker compose up -d`` and the tests run.
SERVER_URL_VAR = "CRACKTRADE_API_TEST_DATABASE_URL"

#: Set to 1 to turn "no server reachable" from a skip into a failure. Meant for CI, where a
#: suite that quietly skips its integration tests reports green while proving nothing.
REQUIRED_VAR = "CRACKTRADE_API_TEST_DB_REQUIRED"

#: Administrative connections target the maintenance database: a database cannot be created or
#: dropped from a connection that is attached to it.
MAINTENANCE_DATABASE = "postgres"


class IsolatedApiSettings(ApiSettings):
    """``ApiSettings`` that ignores any ``.env`` file in the working directory.

    A developer's local ``.env`` legitimately points at their own database. Reading it here
    would make the defaults tests pass or fail depending on whose machine ran them, so the
    tests read the environment only -- which they control through ``monkeypatch``.

    Subclassing rather than passing ``_env_file=None``: the private keyword is untyped, and
    strict mypy is right to reject it.
    """

    model_config = SettingsConfigDict(
        env_prefix="CRACKTRADE_API_",
        env_file=None,
        extra="ignore",
    )


def _with_database(url: str, dbname: str) -> str:
    """``url`` pointed at a different database on the same server."""
    parts = conninfo_to_dict(url)
    parts["dbname"] = dbname
    return make_conninfo("", **parts)


def _server_url() -> str:
    """Connection string for administrative work on the test server."""
    configured = os.environ.get(SERVER_URL_VAR)
    if configured:
        return configured
    return _with_database(DEFAULT_DATABASE_URL, MAINTENANCE_DATABASE)


def _unreachable(url: str, error: Exception) -> str:
    redacted = make_conninfo("", **{**conninfo_to_dict(url), "password": None})
    return (
        f"no PostgreSQL server at {redacted}: {error}\n"
        f"Start one with `docker compose up -d`, or point {SERVER_URL_VAR} at your own."
    )


@pytest.fixture(scope="session")
def db_server_url() -> str:
    """A reachable test server, or an explicit skip saying how to get one.

    Skipping is the default because a checkout without Docker should still be able to run the
    engine's own suite. It is a deliberate hole, so :data:`REQUIRED_VAR` closes it where that
    matters: honest reporting means a suite that did not run its database tests must be able to
    say so loudly rather than merely printing a smaller number of dots.
    """
    url = _server_url()
    try:
        with psycopg.connect(url, connect_timeout=5):
            pass
    except psycopg.Error as error:
        message = _unreachable(url, error)
        if os.environ.get(REQUIRED_VAR) == "1":
            pytest.fail(f"{REQUIRED_VAR}=1 but {message}")
        pytest.skip(message, allow_module_level=True)
    return url


def _provision_template(connection: psycopg.Connection[TupleRow]) -> None:
    """Put the schema into the template database by running the real migration chain.

    Deliberately not a hand-maintained DDL fixture: every db-marked test then runs against
    exactly the schema ``cracktrade-api db migrate`` produces. A fixture that built the schema
    its own way would be free to drift, and the suite would be checking a schema nobody
    deploys.
    """
    migrate(connection)


@pytest.fixture(scope="session")
def db_template(db_server_url: str) -> Iterator[str]:
    """A session-scoped template database holding the schema.

    Cloning a template is a page-level copy inside the server, which is fast enough to give
    every test its own database. Building the schema once per test would not be.
    """
    name = f"cracktrade_tmpl_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(db_server_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        # Provisioning runs on its own connection, which is then closed: PostgreSQL refuses to
        # clone a database while anything is connected to it. Autocommit because the migration
        # engine owns its transaction boundaries and refuses a connection that does not.
        with psycopg.connect(_with_database(db_server_url, name), autocommit=True) as connection:
            _provision_template(connection)
        yield name
    finally:
        with psycopg.connect(db_server_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def db_url(db_server_url: str, db_template: str) -> Iterator[str]:
    """A private database for one test, dropped afterwards."""
    name = f"cracktrade_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(db_server_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(db_template)
            )
        )
    try:
        yield _with_database(db_server_url, name)
    finally:
        # FORCE because a test that left a connection open should still not leak a database
        # into the next run.
        with psycopg.connect(db_server_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def db(db_url: str) -> Iterator[psycopg.Connection[TupleRow]]:
    """An open connection to this test's own database."""
    with psycopg.connect(db_url) as connection:
        yield connection


@pytest.fixture
def client(db_url: str) -> Iterator[TestClient]:
    """The application, wired to this test's own database.

    Exercised through the real ASGI stack rather than by calling route functions: the pieces
    most worth testing here -- the problem+json handler, the dependency that opens a
    transaction, status codes -- only exist once a request goes through it.
    """
    application = create_app(ApiSettings(database_url=db_url))
    with TestClient(application, raise_server_exceptions=False) as running:
        yield running
