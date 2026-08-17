"""What the process can honestly say about itself.

A health check that reports "ok" because the process is running answers a question nobody
asked. This one answers the two that matter operationally: can the database be reached, and is
its schema the one this build expects. Either failing means requests will fail later, inside a
request, less clearly.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool, PoolTimeout

from cracktrade import __version__
from cracktrade.api.db.migrate import MigrationError, inspect


@dataclass(frozen=True, slots=True)
class Health:
    """The report. ``ok`` is a conjunction, never a summary of the interesting half."""

    database: bool
    migrations_current: bool
    applied: int
    pending: tuple[str, ...]
    #: Set when the migration chain refuses outright -- drift, a rewritten history, a database
    #: ahead of the code. Distinct from "pending": those are states to fix, this is a refusal.
    refusal: str | None
    version: str

    @property
    def ok(self) -> bool:
        return self.database and self.migrations_current and self.refusal is None


def check(pool: ConnectionPool[psycopg.Connection[TupleRow]]) -> Health:
    """Report on the database this process is actually using.

    Takes the pool rather than a unit of work, which is the whole point: the unit-of-work
    dependency *borrows* a connection before the route body runs, so a health check declaring
    it would fail with an unexplained 500 in exactly the case it exists to describe. Here an
    unreachable database is an answer.
    """
    try:
        with pool.connection() as connection:
            current = inspect(connection)
    except (psycopg.OperationalError, PoolTimeout) as error:
        return Health(
            database=False,
            migrations_current=False,
            applied=0,
            pending=(),
            refusal=str(error).strip() or error.__class__.__name__,
            version=__version__,
        )
    except MigrationError as error:
        # Drift, a rewritten history, or a database ahead of the code. The database answered,
        # so it is reachable; what it holds is not what this build was written against.
        return Health(
            database=True,
            migrations_current=False,
            applied=0,
            pending=(),
            refusal=str(error),
            version=__version__,
        )

    return Health(
        database=True,
        migrations_current=current.is_up_to_date,
        applied=len(current.applied),
        pending=tuple(migration.path.name for migration in current.pending),
        refusal=None,
        version=__version__,
    )
