"""Request-scoped dependencies.

A leaf module, and deliberately so. Routes need the unit-of-work dependency and the app module
needs the routes, so putting these in ``app`` made the two import each other -- which mypy
accepted and the interpreter did not. Everything here is imported by both and imports neither.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from cracktrade.api.db.uow import UnitOfWork, unit_of_work


def pool_of(request: Request) -> ConnectionPool[psycopg.Connection[TupleRow]]:
    """The process's connection pool, opened once at start-up."""
    pool: ConnectionPool[psycopg.Connection[TupleRow]] = request.app.state.pool
    return pool


def work_of(request: Request) -> Iterator[UnitOfWork]:
    """One transaction for one request.

    A dependency rather than something each route opens, so every route that touches the
    database gets the same boundary and none of them has to remember to.
    """
    with unit_of_work(pool_of(request)) as work:
        yield work


#: The unit-of-work dependency, as an annotation a route can declare directly.
#:
#: Routes take ``work: Work`` and never name the ``db`` layer themselves, which keeps the
#: declared layering honest: a route's transaction arrives from here rather than being
#: something it reaches down to open.
Work = Annotated[UnitOfWork, Depends(work_of)]
