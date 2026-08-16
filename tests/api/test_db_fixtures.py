"""Phase 1: the database test harness itself.

These tests check the fixtures rather than the application, which is worth doing exactly once:
every db-marked test from here on inherits the isolation they provide, and an isolation bug
would show up much later as a test that passes alone and fails in a suite.
"""

from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.db


def test_the_server_is_a_real_postgres(db: psycopg.Connection[tuple[object, ...]]) -> None:
    row = db.execute("SELECT version()").fetchone()
    assert row is not None
    assert "PostgreSQL" in str(row[0])


def test_supported_server_version(db: psycopg.Connection[tuple[object, ...]]) -> None:
    """Spec section 14 requires 15+.

    ``DROP DATABASE ... WITH (FORCE)`` (used by these fixtures) and ``gen_random_uuid()``
    (used by the schema) are both core from 13 onwards, but the schema is written against 15
    and tested there; a server older than that would fail later and less clearly.
    """
    assert db.info.server_version >= 150000


def test_each_test_gets_its_own_database(db: psycopg.Connection[tuple[object, ...]]) -> None:
    """First half of the isolation proof: write a table into this test's database."""
    db.execute("CREATE TABLE leak_check (id int)")
    db.execute("INSERT INTO leak_check VALUES (1)")
    db.commit()
    row = db.execute("SELECT count(*) FROM leak_check").fetchone()
    assert row is not None
    assert row[0] == 1


def test_nothing_leaks_from_the_previous_test(db: psycopg.Connection[tuple[object, ...]]) -> None:
    """Second half: the table the previous test created is not visible here.

    Ordering-dependent by construction -- it has to be, since it asserts the absence of a
    side effect that the preceding test definitely performed.
    """
    row = db.execute("SELECT to_regclass('public.leak_check')").fetchone()
    assert row is not None
    assert row[0] is None


def test_the_maintenance_database_is_untouched(
    db_server_url: str, db: psycopg.Connection[tuple[object, ...]]
) -> None:
    """The database named in the connection URL is a connection target, not a workspace."""
    db.execute("CREATE TABLE should_not_appear_on_the_server (id int)")
    db.commit()
    with psycopg.connect(db_server_url) as admin:
        row = admin.execute(
            "SELECT to_regclass('public.should_not_appear_on_the_server')"
        ).fetchone()
    assert row is not None
    assert row[0] is None
