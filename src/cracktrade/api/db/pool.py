"""Connection pooling.

Synchronous, per spec section 15.3 D-13: the server and the worker share one data layer, and
an async pool for one of them would mean two implementations of every repository.

The pool is owned by the process, opened once at start-up and closed at shutdown. Nothing
below this module knows a pool exists -- repositories take a connection, which is what makes
them equally usable from a request, from the worker, and from a test that supplies its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from cracktrade.api.settings import ApiSettings
from cracktrade.log import get_logger

logger = get_logger(__name__)


def build_pool(
    settings: ApiSettings, *, open_now: bool = True
) -> ConnectionPool[psycopg.Connection[TupleRow]]:
    """Create the process's connection pool.

    ``open_now`` is false in tests that want to construct a pool without reaching a server.
    """
    pool: ConnectionPool[psycopg.Connection[TupleRow]] = ConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        open=False,
        # A request that cannot get a connection should fail quickly and say so, rather than
        # hanging until the client gives up and leaves a request in flight nobody is waiting on.
        timeout=10.0,
        name="cracktrade",
    )
    if open_now:
        pool.open(wait=True, timeout=10.0)
        logger.info("database pool open (%d-%d)", settings.pool_min_size, settings.pool_max_size)
    return pool


@contextmanager
def worker_connection(settings: ApiSettings) -> Iterator[psycopg.Connection[TupleRow]]:
    """A single long-lived connection for the worker process.

    The worker holds one run at a time and talks to the database between stretches of engine
    work, so a pool would only add machinery around a connection that is idle most of the time
    and used by one thread when it is not.
    """
    connection = psycopg.connect(settings.database_url)
    try:
        yield connection
    finally:
        connection.close()
