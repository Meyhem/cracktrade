"""Phase 8: what the processes say about themselves, and what happens under contention.

Three things are pinned here. That the health endpoint answers honestly *including* when the
database is the thing that is wrong -- the case a health check exists for and the easiest one
to accidentally turn into a 500. That every response is traceable to a log line. And that the
invariants the schema enforces survive being raced, rather than merely being true when one
request at a time asks politely.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import TupleRow

from cracktrade.api.app import create_app
from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.middleware import MAX_ID_LENGTH, _safe
from cracktrade.api.repos import RunRepo
from cracktrade.api.settings import ApiSettings

BASE = "/api/v1"
TICKER = "TEST"


# --------------------------------------------------------------------------- request ids
# No database needed: the middleware runs before any route.


def test_a_request_id_is_returned_and_echoed() -> None:
    """A client-supplied id survives, so a trace through a proxy stays one trace."""
    with TestClient(create_app(ApiSettings(database_url="postgresql:///unused"))) as client:
        fresh = client.get(f"{BASE}/meta")
        assert fresh.headers["x-request-id"]

        echoed = client.get(f"{BASE}/meta", headers={"x-request-id": "abc-123"})
        assert echoed.headers["x-request-id"] == "abc-123"


def test_two_requests_get_different_ids() -> None:
    with TestClient(create_app(ApiSettings(database_url="postgresql:///unused"))) as client:
        first = client.get(f"{BASE}/meta").headers["x-request-id"]
        second = client.get(f"{BASE}/meta").headers["x-request-id"]
    assert first != second


@pytest.mark.parametrize(
    "supplied",
    [
        "trace\nGET /admin -> 200",  # a forged log line
        "id with spaces",
        "x" * (MAX_ID_LENGTH + 1),
        "",
        None,
    ],
)
def test_an_unusable_id_is_replaced_not_repaired(supplied: str | None) -> None:
    """A header is attacker-controlled, and a log file is read by tools that split on newlines.

    Over-long ids are replaced rather than truncated: a truncated id is a *different* id
    wearing the caller's, which is worse than an obviously new one.
    """
    result = _safe(supplied)
    assert result != supplied
    assert result.isalnum() or all(character.isalnum() or character in "-_" for character in result)


# --------------------------------------------------------------------------- health


@pytest.mark.db
def test_health_reports_a_migrated_database(client: TestClient) -> None:
    response = client.get(f"{BASE}/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["database"] is True
    assert body["migrations_current"] is True
    assert body["applied_migrations"] >= 1
    assert body["pending_migrations"] == []
    assert body["migration_refusal"] is None


def test_health_answers_when_the_database_is_unreachable() -> None:
    """The case it exists for. A 500 here would be the check failing, not reporting.

    The unit-of-work dependency borrows a connection *before* a route body runs, so a health
    route declaring it would have failed with an unexplained 500 in exactly this situation.
    Taking the pool instead is what makes an unreachable database an answer.
    """
    settings = ApiSettings(
        database_url="postgresql://nobody@localhost:59999/nope", pool_timeout_seconds=1.0
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(f"{BASE}/health")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["database"] is False
    assert body["migration_refusal"]


@pytest.mark.db
def test_health_reports_a_database_that_was_never_migrated(db_server_url: str) -> None:
    """An empty database is unhealthy and says which migrations are missing.

    Reporting must not *fix* anything: ``plan`` creates the ledger table when it is absent,
    which is right before migrating and wrong here -- a check that creates a table has changed
    the thing it was asked to observe. ``inspect`` is the read-only twin, and this is the test
    that would fail if the two were ever collapsed back into one.
    """
    name = "cracktrade_test_unmigrated"
    with psycopg.connect(_maintenance(db_server_url), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')

    url = _named(db_server_url, name)
    try:
        with TestClient(create_app(ApiSettings(database_url=url))) as client:
            response = client.get(f"{BASE}/health")

        assert response.status_code == 503
        body = response.json()
        assert body["migrations_current"] is False
        assert body["applied_migrations"] == 0
        assert body["pending_migrations"] == ["0001_initial.sql"]

        with psycopg.connect(url) as connection:
            found = connection.execute("SELECT to_regclass('schema_migrations')").fetchone()
        assert found is not None and found[0] is None, "the health check created the ledger"
    finally:
        with psycopg.connect(_maintenance(db_server_url), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _maintenance(server_url: str) -> str:
    """A connection to the maintenance database: one cannot be dropped from inside itself."""
    return make_conninfo("", **{**conninfo_to_dict(server_url), "dbname": "postgres"})


def _named(server_url: str, name: str) -> str:
    return make_conninfo("", **{**conninfo_to_dict(server_url), "dbname": name})


# --------------------------------------------------------------------------- concurrency


def _strategy(client: TestClient, name: str = "momentum_v2") -> str:
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": name,
            "ticker": TICKER,
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert response.status_code == 201, response.text
    identifier: str = response.json()["strategy"]["id"]
    return identifier


def _edited(config: dict[str, Any], commission: float) -> dict[str, Any]:
    changed = {
        section: dict(values) for section, values in config.items() if isinstance(values, dict)
    }
    return {**config, **changed, "execution": {**config["execution"], "commission_pct": commission}}


@pytest.mark.db
def test_parallel_saves_to_one_strategy_produce_exactly_one_version(client: TestClient) -> None:
    """Optimistic concurrency, raced. Last-write-wins would lose an edit while reporting success.

    Both requests state ``base_version: 1``. Exactly one may become v2; the other must be told
    the head moved, because the change it was based on is no longer the change it would apply.
    """
    strategy_id = _strategy(client)
    config = client.get(f"{BASE}/strategies/{strategy_id}").json()["head"]["config"]

    def save(commission: float) -> int:
        response = client.post(
            f"{BASE}/strategies/{strategy_id}/versions",
            json={"base_version": 1, "config": _edited(config, commission)},
        )
        return int(response.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(code for code in pool.map(save, (0.08, 0.09)))

    assert codes == [201, 409]
    assert client.get(f"{BASE}/strategies/{strategy_id}").json()["head"]["version"] == 2


@pytest.mark.db
def test_parallel_creates_of_one_name_produce_exactly_one_strategy(client: TestClient) -> None:
    """The unique constraint decides, and the loser is told so rather than getting a 500."""

    def create(_: int) -> int:
        return int(
            client.post(
                f"{BASE}/strategies",
                json={
                    "name": "contested",
                    "ticker": TICKER,
                    "start_date": "2020-01-01",
                    "end_date": "2023-12-31",
                },
            ).status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(create, (0, 1)))

    assert codes == [201, 409]
    assert (
        len(client.get(f"{BASE}/strategies", params={"search": "contested"}).json()["strategies"])
        == 1
    )


@pytest.mark.db
def test_two_workers_never_claim_the_same_run(client: TestClient, db_url: str) -> None:
    """``FOR UPDATE SKIP LOCKED`` is the whole queue design; this is the assertion behind it."""
    strategy_id = _strategy(client)
    launched = {
        client.post(
            f"{BASE}/strategies/{strategy_id}/runs", json={"kind": "backtest", "params": {}}
        ).json()["id"]
        for _ in range(6)
    }
    assert len(launched) == 6

    barrier = threading.Barrier(3)

    def drain(_: int) -> list[str]:
        claimed: list[str] = []
        with psycopg.connect(db_url) as connection:
            barrier.wait(timeout=10)
            while True:
                with unit_of_work_on(connection) as work:
                    run = RunRepo(work.connection).claim("racer")
                if run is None:
                    return claimed
                claimed.append(str(run.id))

    with ThreadPoolExecutor(max_workers=3) as pool:
        harvested = [identifier for batch in pool.map(drain, range(3)) for identifier in batch]

    assert sorted(harvested) == sorted(launched)
    assert len(harvested) == len(set(harvested)), "a run was claimed twice"


@pytest.mark.db
def test_one_run_cannot_be_promoted_twice_under_the_same_name(
    client: TestClient, db_url: str
) -> None:
    """Both requests offer the same default name, so the registry has to break the tie."""
    strategy_id = _strategy(client)
    run_id = client.post(
        f"{BASE}/strategies/{strategy_id}/runs", json={"kind": "optimize", "params": {}}
    ).json()["id"]

    yaml_text = client.get(f"{BASE}/strategies/{strategy_id}").json()["head"]["yaml"]
    with psycopg.connect(db_url) as connection:
        _force_running(connection, run_id)
        with unit_of_work_on(connection) as work:
            RunRepo(work.connection).succeed(
                UUID(run_id), result={"optimized_yaml": yaml_text, "changes": []}
            )

    def promote(_: int) -> int:
        return int(client.post(f"{BASE}/runs/{run_id}/promote", json={}).status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(promote, (0, 1)))

    assert codes == [201, 409]


def _force_running(connection: psycopg.Connection[TupleRow], run_id: str) -> None:
    with unit_of_work_on(connection) as work:
        work.connection.execute(
            "UPDATE run SET status = 'running', started_at = now(), claimed_by = 'test' "
            "WHERE id = %s",
            (run_id,),
        )
