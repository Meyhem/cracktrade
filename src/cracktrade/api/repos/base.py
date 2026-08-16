"""Shared repository plumbing.

Repositories hold a connection and run SQL on it. They do not own transactions -- the unit of
work does -- so several repositories composed in one workflow commit or roll back together.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import TupleRow

from cracktrade.api.db.translate import translate


class Repository:
    """Base class: a connection, and one place where psycopg errors become API errors."""

    __slots__ = ("connection",)

    def __init__(self, connection: psycopg.Connection[TupleRow]) -> None:
        self.connection = connection

    @contextmanager
    def _translating(self) -> Iterator[None]:
        try:
            yield
        except psycopg.Error as error:
            raise translate(error) from error

    def _fetch_one(self, query: str, params: Sequence[Any] = ()) -> TupleRow | None:
        with self._translating():
            return self.connection.execute(query, params).fetchone()

    def _fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[TupleRow]:
        with self._translating():
            return self.connection.execute(query, params).fetchall()

    def _execute(self, query: str, params: Sequence[Any] = ()) -> int:
        """Run a statement, returning the number of rows it affected."""
        with self._translating():
            return self.connection.execute(query, params).rowcount
