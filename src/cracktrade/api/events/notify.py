"""Progress events over PostgreSQL LISTEN/NOTIFY.

The worker and the server are separate processes with no channel between them except the
database they already share, so the database carries the notification too. No broker, for the
same reason there is no broker for the queue.

Payloads are deliberately tiny: an id, a strategy, a status. A client that hears one refetches
the run, so there is exactly one code path that renders a run and no risk of a notification and
a fetch disagreeing about it.
"""

from __future__ import annotations

import json
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

from cracktrade.log import get_logger

logger = get_logger(__name__)

#: The channel both sides agree on.
CHANNEL = "cracktrade_runs"


def notify_run(
    connection: psycopg.Connection[TupleRow], run_id: UUID, strategy_id: UUID, status: str
) -> None:
    """Announce that a run changed state.

    Failure to notify is logged and swallowed -- and only here. A live update is a convenience;
    losing one costs a client a few seconds until it polls, while raising would fail a run that
    had already completed successfully.
    """
    payload = json.dumps({"id": str(run_id), "strategy_id": str(strategy_id), "status": status})
    try:
        connection.execute(
            sql.SQL("SELECT pg_notify({}, {})").format(sql.Literal(CHANNEL), sql.Literal(payload))
        )
        connection.commit()
    except psycopg.Error as error:
        logger.warning("could not publish a run event: %s", error)
