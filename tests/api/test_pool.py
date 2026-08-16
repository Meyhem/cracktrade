"""Phase 3: the connection pool and the unit of work over it.

Thin, but worth pinning: the pool is opened once per process and every request borrows from
it, so a mistake here is a mistake in every endpoint at once.
"""

from __future__ import annotations

import psycopg
import pytest

from cracktrade.api.db.pool import build_pool, worker_connection
from cracktrade.api.db.uow import unit_of_work
from cracktrade.api.repos import StrategyRepo
from cracktrade.api.repos.rows import StrategyOrigin
from cracktrade.api.settings import ApiSettings

pytestmark = pytest.mark.db


def _settings(db_url: str) -> ApiSettings:
    return ApiSettings(database_url=db_url, pool_min_size=1, pool_max_size=3)


def test_a_pool_can_be_built_without_reaching_a_server() -> None:
    """Constructing the pool must not connect: the app has to start before the database does.

    Otherwise a server booted alongside its database in compose fails on a race nobody can fix
    from the application side.
    """
    settings = ApiSettings(database_url="postgresql://nobody@localhost:59999/nope")
    pool = build_pool(settings, open_now=False)
    assert pool.name == "cracktrade"
    pool.close()


def test_work_committed_through_the_pool_persists(db_url: str) -> None:
    pool = build_pool(_settings(db_url))
    try:
        with unit_of_work(pool) as work:
            StrategyRepo(work.connection).create(name="momentum_v2", origin=StrategyOrigin.AUTHORED)
        with unit_of_work(pool) as work:
            found = StrategyRepo(work.connection).get_by_name("momentum_v2")
        assert found is not None
    finally:
        pool.close()


def test_a_failed_unit_of_work_rolls_back_through_the_pool(db_url: str) -> None:
    pool = build_pool(_settings(db_url))
    try:
        with pytest.raises(RuntimeError), unit_of_work(pool) as work:
            StrategyRepo(work.connection).create(name="doomed", origin=StrategyOrigin.AUTHORED)
            raise RuntimeError("something went wrong after the write")

        with unit_of_work(pool) as work:
            assert StrategyRepo(work.connection).get_by_name("doomed") is None
    finally:
        pool.close()


def test_connections_are_returned_and_reused(db_url: str) -> None:
    """A leaked connection would exhaust a small pool, so borrow more times than it holds."""
    settings = ApiSettings(database_url=db_url, pool_min_size=1, pool_max_size=2)
    pool = build_pool(settings)
    try:
        for _ in range(6):
            with unit_of_work(pool) as work:
                work.connection.execute("SELECT 1").fetchone()
    finally:
        pool.close()


def test_the_worker_gets_a_plain_connection(db_url: str) -> None:
    """The worker holds one run at a time; a pool would wrap a mostly idle connection."""
    with worker_connection(_settings(db_url)) as connection:
        assert isinstance(connection, psycopg.Connection)
        row = connection.execute("SELECT 1").fetchone()
        assert row is not None
