"""The unit of work.

One workflow, one transaction, one connection. A service opens a unit of work, builds the
repositories it needs on ``uow.connection``, and either the whole workflow commits or none of
it does. That matters here more than in most applications: promotion writes a strategy, its
first version and a queued backtest, and a half-applied promotion would leave a strategy the
UI shows with no config to render.

Database refusals are translated on the way out (:mod:`cracktrade.api.db.translate`), so a
service sees the interface's own error taxonomy rather than a psycopg exception, and the
distinction between a race and a bug is made in one place.

The unit of work deliberately does not know what a repository is: repositories are built on
top of ``db``, so a unit of work that handed them out would invert the layering that spec
section 15.4 states and ``tests/api/test_layering.py`` enforces.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from cracktrade.api.db.translate import translate


@dataclass(frozen=True, slots=True)
class UnitOfWork:
    """The connection a workflow's repositories run on, inside one open transaction."""

    connection: psycopg.Connection[TupleRow]


@contextmanager
def unit_of_work_on(connection: psycopg.Connection[TupleRow]) -> Iterator[UnitOfWork]:
    """Run one transaction on a caller-supplied connection.

    Used by the worker, which owns a single connection rather than a pool, and by tests.
    """
    try:
        with connection.transaction():
            yield UnitOfWork(connection=connection)
    except psycopg.Error as error:
        # Deferred constraints and triggers can fire at COMMIT rather than at the statement
        # that caused them, so the boundary translates here too, not only around each query.
        raise translate(error) from error


@contextmanager
def unit_of_work(pool: ConnectionPool[psycopg.Connection[TupleRow]]) -> Iterator[UnitOfWork]:
    """Borrow a connection from the pool and run one transaction on it."""
    with pool.connection() as connection, unit_of_work_on(connection) as work:
        yield work
