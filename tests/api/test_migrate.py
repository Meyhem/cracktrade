"""Phase 2: the migration engine.

Two things are being defended. The first is that migrations apply once, in order, and are
recorded -- ordinary, and checked against a real server because "it committed" is exactly the
kind of claim an in-memory substitute cannot make. The second is that the engine *refuses*
rather than guesses when the files and the ledger disagree, which is where a migration tool
does its real work: applying a chain is easy, and noticing that the chain is not the one this
database ran is the part that prevents a silent divergence.
"""

from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.db.migrate import (
    MIGRATIONS_DIR,
    AppliedMigration,
    Migration,
    MigrationDriftError,
    MigrationError,
    MigrationMissingError,
    MigrationOrderError,
    build_plan,
    checksum_of,
    discover,
    migrate,
    plan,
)

# --------------------------------------------------------------------------- discovery (no db)


def _migration(version: int, name: str = "thing", sql: str = "SELECT 1") -> Migration:
    return Migration(
        version=version,
        name=name,
        path=Path(f"{version:04d}_{name}.sql"),
        sql=sql,
        checksum=checksum_of(sql),
    )


def _applied(migration: Migration, *, checksum: str | None = None) -> AppliedMigration:
    return AppliedMigration(
        version=migration.version,
        name=migration.name,
        checksum=checksum if checksum is not None else migration.checksum,
        applied_at=datetime.now(UTC),
        duration_ms=1,
    )


def test_the_shipped_chain_is_discoverable() -> None:
    """The chain ships inside the package, so an installed app can migrate its own database."""
    migrations = discover()
    assert migrations, "no migrations found"
    assert migrations[0].version == 1
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)


def test_migrations_live_in_the_package() -> None:
    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "0001_initial.sql").is_file()


def test_a_misnamed_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "initial.sql").write_text("SELECT 1")
    with pytest.raises(MigrationError, match="not a migration filename"):
        discover(tmp_path)


def test_duplicate_version_numbers_are_refused(tmp_path: Path) -> None:
    (tmp_path / "0001_one.sql").write_text("SELECT 1")
    (tmp_path / "0001_two.sql").write_text("SELECT 2")
    with pytest.raises(MigrationError, match="share version"):
        discover(tmp_path)


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="no migrations directory"):
        discover(tmp_path / "nope")


# --------------------------------------------------------------------------- the refusals


def test_nothing_applied_means_everything_pending() -> None:
    one, two = _migration(1), _migration(2)
    result = build_plan([one, two], [])
    assert result.pending == (one, two)
    assert not result.is_up_to_date


def test_an_edited_applied_migration_is_refused() -> None:
    """Drift: the database holds the effects of the old text, which cannot be un-applied."""
    original = _migration(1, sql="CREATE TABLE a (id int)")
    edited = _migration(1, sql="CREATE TABLE a (id bigint)")
    with pytest.raises(MigrationDriftError, match="changed after it was applied"):
        build_plan([edited], [_applied(original)])


def test_a_migration_numbered_below_an_applied_one_is_refused() -> None:
    """Two branches each adding a migration, merged. The chain would run in two orders."""
    with pytest.raises(MigrationOrderError, match="numbered below"):
        build_plan([_migration(1), _migration(2), _migration(3)], [_applied(_migration(3))])


def test_a_ledger_entry_with_no_file_is_refused() -> None:
    """The database is ahead of the code -- a newer deployment ran against it."""
    with pytest.raises(MigrationMissingError, match="ahead of this checkout"):
        build_plan([_migration(1)], [_applied(_migration(1)), _applied(_migration(2))])


def test_a_gap_below_the_high_water_mark_is_still_refused() -> None:
    """A pending file need not be adjacent to the applied ones to be out of order.

    Every applied version has its file here, so the missing-file refusal cannot fire and this
    genuinely exercises ordering: 0005 sits below the applied 0009.
    """
    files = [_migration(1), _migration(5), _migration(9)]
    with pytest.raises(MigrationOrderError):
        build_plan(files, [_applied(_migration(1)), _applied(_migration(9))])


# --------------------------------------------------------------------------- against a database


@pytest.mark.db
def test_the_template_database_is_already_migrated(db: psycopg.Connection[TupleRow]) -> None:
    """Every db-marked test inherits the real schema, not a fixture's idea of it."""
    row = db.execute("SELECT count(*) FROM schema_migrations").fetchone()
    assert row is not None
    assert row[0] == len(discover())


@pytest.mark.db
def test_applying_records_name_and_checksum(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as connection:
        row = connection.execute(
            "SELECT version, name, checksum, duration_ms FROM schema_migrations ORDER BY version"
        ).fetchone()
    assert row is not None
    first = discover()[0]
    assert row[0] == first.version
    assert row[1] == first.name
    assert row[2] == first.checksum
    assert row[3] >= 0


@pytest.mark.db
def test_migrating_again_does_nothing(db_url: str) -> None:
    """Idempotence is the property that makes `db migrate` safe to run on every deploy."""
    with psycopg.connect(db_url, autocommit=True) as connection:
        assert migrate(connection) == ()
        assert plan(connection).is_up_to_date


@pytest.mark.db
def test_drift_is_detected_against_a_real_ledger(db_url: str) -> None:
    """The end-to-end version of the drift refusal: a tampered ledger row must stop a run."""
    with psycopg.connect(db_url, autocommit=True) as connection:
        connection.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        with pytest.raises(MigrationDriftError):
            migrate(connection)


@pytest.mark.db
def test_a_non_autocommit_connection_is_refused(db_url: str) -> None:
    """Verified behaviour, not an assumption (psycopg 3.3).

    On a non-autocommit connection the first statement opens an implicit transaction that is
    never closed, and ``Connection.transaction()`` then issues a savepoint rather than a real
    transaction. Migrations would appear to apply, the ledger would record them, and
    ``close()`` would roll all of it back. This engine found that the hard way; the refusal is
    what keeps it found.
    """
    with psycopg.connect(db_url) as connection:
        assert not connection.autocommit
        with pytest.raises(MigrationError, match="autocommit"):
            migrate(connection)


@pytest.mark.db
def test_the_schema_survives_the_connection_closing(db_url: str) -> None:
    """The regression test for that bug: reconnect and confirm the objects are really there."""
    with psycopg.connect(db_url) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ).fetchall()
    names = {str(row[0]) for row in rows}
    assert {"strategy", "strategy_version", "run", "run_series"} <= names


def _migrate_in_subprocess(url: str, queue: multiprocessing.Queue[str]) -> None:  # pragma: no cover
    try:
        with psycopg.connect(url, autocommit=True) as connection:
            applied = migrate(connection)
        queue.put(f"ok:{len(applied)}")
    except Exception as error:  # noqa: BLE001 - the subprocess reports whatever it hit
        queue.put(f"error:{error!r}")


@pytest.mark.db
@pytest.mark.slow
def test_concurrent_migrators_serialise(db_server_url: str, db_template: str) -> None:
    """Two processes starting together must not both apply the same migration.

    The advisory lock is the whole reason this cannot be tested with threads sharing a
    connection: it is what makes a server and a worker booting simultaneously safe.
    """
    import uuid as uuid_module

    from psycopg import sql

    from tests.api.conftest import _with_database

    name = f"cracktrade_race_{uuid_module.uuid4().hex[:12]}"
    with psycopg.connect(db_server_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = _with_database(db_server_url, name)
    try:
        context = multiprocessing.get_context("spawn")
        queue: multiprocessing.Queue[str] = context.Queue()
        processes = [
            context.Process(target=_migrate_in_subprocess, args=(url, queue)) for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=60)

        results = sorted(queue.get(timeout=5) for _ in processes)
        expected = len(discover())
        # One applies the chain, the other finds it already applied. Neither errors, and
        # neither applies it twice.
        assert results == ["ok:0", f"ok:{expected}"], results

        with psycopg.connect(url, autocommit=True) as connection:
            row = connection.execute("SELECT count(*) FROM schema_migrations").fetchone()
        assert row is not None
        assert row[0] == expected
    finally:
        with psycopg.connect(db_server_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
