"""The migration engine (spec section 14.6).

Ordered SQL files, a ledger recording what was applied, and three refusals. It is deliberately
small: the migrations themselves are plain SQL a reviewer can read, and the engine's whole job
is to apply them in order, exactly once, and to stop rather than guess when the files and the
database disagree.

**Forward-only.** There are no down migrations. Rolling back applied DDL on a database whose
design is append-only would be a fiction: the data a rollback destroys is precisely the data
the design promises to keep. Recovery is a new forward migration.

**Known limitation.** Each migration runs inside a transaction, so ``CREATE INDEX
CONCURRENTLY`` and the other statements PostgreSQL forbids in one cannot be used. Nothing in
the schema needs them yet. When something does, the fix is a ``-- migrate: no-transaction``
directive read from the file's first line, not the removal of transactions from the rest.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import TupleRow

from cracktrade.errors import CracktradeError
from cracktrade.log import get_logger

logger = get_logger(__name__)

#: Where the chain lives. Shipped inside the package so that an installed application can
#: migrate its own database without the repository being present.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: ``NNNN_some_name.sql``. The number orders the chain; the name is for humans.
FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

#: Key for the session-level advisory lock that serialises concurrent migrators. Arbitrary but
#: fixed: two processes starting together must not both decide the same migration is pending.
LOCK_KEY = 0x6372_6B74  # "crkt"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     integer PRIMARY KEY,
  name        text NOT NULL,
  checksum    text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  duration_ms integer NOT NULL
)
"""


class MigrationError(CracktradeError):
    """The migration chain cannot be applied as it stands."""


class MigrationDriftError(MigrationError):
    """An already-applied migration file has changed since it was applied.

    The database contains the effects of the old text, and applying the new text is not
    possible -- migrations run once. Either restore the file, or express the change as a new
    migration.
    """


class MigrationOrderError(MigrationError):
    """A pending migration is numbered below one that has already been applied.

    Usually two branches that each added a migration and were merged. Applying it would mean
    the chain ran in a different order here than everywhere else, so the number has to be
    raised above the applied ones instead.
    """


class MigrationMissingError(MigrationError):
    """The ledger records a migration that has no file.

    The database is ahead of the code -- typically a newer deployment ran against it, or a
    file was deleted. Continuing would apply a chain that is not the one this database has.
    """


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration file on disk."""

    version: int
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """One row of the ledger."""

    version: int
    name: str
    checksum: str
    applied_at: datetime
    duration_ms: int


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """What the engine found when it compared the files with the ledger."""

    applied: tuple[AppliedMigration, ...]
    pending: tuple[Migration, ...]

    @property
    def is_up_to_date(self) -> bool:
        """Whether the database already has every migration in the chain."""
        return not self.pending


def checksum_of(sql: str) -> str:
    """Content hash of a migration, used to detect a file edited after it was applied."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> tuple[Migration, ...]:
    """Every migration file, in application order.

    Raises:
        MigrationError: a file does not follow ``NNNN_name.sql``, or two share a number.
    """
    if not directory.is_dir():
        raise MigrationError(f"no migrations directory at {directory}")

    migrations: list[Migration] = []
    seen: dict[int, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name} is not a migration filename; expected NNNN_lower_snake_case.sql"
            )
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(
                f"two migrations share version {version:04d}: {seen[version].name} and {path.name}"
            )
        seen[version] = path
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=match.group(2),
                path=path,
                sql=sql,
                checksum=checksum_of(sql),
            )
        )
    return tuple(migrations)


def _require_autocommit(connection: psycopg.Connection[TupleRow]) -> None:
    """Refuse a connection whose transactions this engine does not control.

    Verified against psycopg 3.3, not assumed. On a non-autocommit connection the first
    statement opens an implicit transaction that stays open, and ``Connection.transaction()``
    then issues a *savepoint* rather than a real transaction: the block appears to succeed,
    nothing is committed, and ``close()`` discards every migration while the ledger reports
    them applied. That failure is silent, which is why this is a refusal rather than a note in
    the docstring.
    """
    if not connection.autocommit:
        raise MigrationError(
            "the migration engine needs an autocommit connection: it opens each migration's "
            "transaction itself, and an implicit transaction underneath would turn those into "
            "savepoints that are never committed."
        )


def _ensure_ledger(connection: psycopg.Connection[TupleRow]) -> None:
    connection.execute(LEDGER_DDL)


def _read_ledger(connection: psycopg.Connection[TupleRow]) -> tuple[AppliedMigration, ...]:
    rows = connection.execute(
        "SELECT version, name, checksum, applied_at, duration_ms "
        "FROM schema_migrations ORDER BY version"
    ).fetchall()
    return tuple(
        AppliedMigration(
            version=int(row[0]),
            name=str(row[1]),
            checksum=str(row[2]),
            applied_at=row[3],
            duration_ms=int(row[4]),
        )
        for row in rows
    )


def build_plan(
    migrations: Sequence[Migration], applied: Sequence[AppliedMigration]
) -> MigrationPlan:
    """Compare the chain against the ledger, refusing rather than guessing.

    Pure, so the refusals can be tested without a database.

    Raises:
        MigrationDriftError: an applied file's content changed.
        MigrationMissingError: the ledger holds a version with no file.
        MigrationOrderError: a pending migration sorts below an applied one.
    """
    by_version = {migration.version: migration for migration in migrations}
    applied_by_version = {record.version: record for record in applied}

    for record in applied:
        migration = by_version.get(record.version)
        if migration is None:
            raise MigrationMissingError(
                f"the database has migration {record.version:04d}_{record.name} applied, but "
                f"no such file exists. The database is ahead of this checkout."
            )
        if migration.checksum != record.checksum:
            raise MigrationDriftError(
                f"{migration.path.name} changed after it was applied "
                f"(ledger {record.checksum[:12]}, file {migration.checksum[:12]}). "
                f"Restore the file, or add the change as a new migration."
            )

    highest_applied = max(applied_by_version, default=0)
    pending = tuple(
        migration for migration in migrations if migration.version not in applied_by_version
    )
    for migration in pending:
        if migration.version < highest_applied:
            raise MigrationOrderError(
                f"{migration.path.name} is numbered below {highest_applied:04d}, which is "
                f"already applied. Renumber it above the applied migrations."
            )
    return MigrationPlan(applied=tuple(applied), pending=pending)


def plan(
    connection: psycopg.Connection[TupleRow], directory: Path = MIGRATIONS_DIR
) -> MigrationPlan:
    """Read the ledger and compare it with the files, applying nothing."""
    _require_autocommit(connection)
    _ensure_ledger(connection)
    return build_plan(discover(directory), _read_ledger(connection))


def migrate(
    connection: psycopg.Connection[TupleRow], directory: Path = MIGRATIONS_DIR
) -> tuple[Migration, ...]:
    """Apply every pending migration. Returns those applied, in order.

    The whole run is serialised by a session-level advisory lock, so two processes starting
    together -- a server and a worker, or two deployments -- do not both decide the same
    migration is pending. Each migration then runs in its own transaction with its ledger row,
    so a failure half-way leaves the earlier ones applied and recorded rather than the database
    in a state the ledger does not describe.
    """
    _require_autocommit(connection)
    applied: list[Migration] = []
    connection.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        current = plan(connection, directory)
        for migration in current.pending:
            started = time.monotonic()
            with connection.transaction():
                connection.execute(migration.sql)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, duration_ms) "
                    "VALUES (%s, %s, %s, %s)",
                    (migration.version, migration.name, migration.checksum, elapsed_ms),
                )
            logger.info("applied %s in %d ms", migration.path.name, elapsed_ms)
            applied.append(migration)
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return tuple(applied)
