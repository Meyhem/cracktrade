"""Phase 7: promotion, and the verdict as every screen sees it.

Runs here are landed by hand rather than executed. That is deliberate: what is under test is
the *derivation* -- which strategy carries a warning, when it clears, which run the verdict
comes from -- and driving that through the real engine would mean arranging for a stub market
to come out credible, which makes the test about the market rather than about the rule. The
engine's own judgement is pinned in ``tests/test_validate.py``; the engine-to-database path is
pinned in ``tests/api/test_worker.py``. This is the layer in between.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.repos import RunRepo, VersionRepo

pytestmark = pytest.mark.db

BASE = "/api/v1"
TICKER = "TEST"

#: Every check the engine reports, all passing. Copied in shape, not recomputed: the API is
#: forbidden from deriving a verdict from statistics, so a test that built one from statistics
#: would be testing something the code must never do.
_PASSING_CHECKS: tuple[dict[str, Any], ...] = tuple(
    {
        "name": name,
        "label": name.replace("_", " "),
        "passed": True,
        "plain": f"what {name} asks",
        "stat": "fine",
        "detail": f"{name} passed",
    }
    for name in (
        "fold_results",
        "benchmark",
        "deflated_sharpe",
        "overfitting",
        "stability",
        "costs",
        "intervals",
        "trade_count",
    )
)


def _failing(*names: str) -> tuple[dict[str, Any], ...]:
    return tuple({**check, "passed": check["name"] not in names} for check in _PASSING_CHECKS)


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


def _launch(client: TestClient, strategy_id: str, kind: str, **params: Any) -> str:
    response = client.post(
        f"{BASE}/strategies/{strategy_id}/runs", json={"kind": kind, "params": params}
    )
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    return run_id


def _head_yaml(db_url: str, strategy_id: str) -> str:
    with psycopg.connect(db_url) as connection, unit_of_work_on(connection) as work:
        return VersionRepo(work.connection).require_head(UUID(strategy_id)).config_yaml


def _winning_yaml(base_yaml: str) -> str:
    """The base config with one parameter moved, so a promotion is not a no-op copy."""
    return base_yaml.replace("window: 14", "window: 16")


def _land(
    db_url: str, run_id: str, *, result: dict[str, Any], is_credible: bool | None = None
) -> None:
    """Claim the queued run and land a result on it, as the worker would."""
    with psycopg.connect(db_url) as connection:
        _claim(connection, run_id)
        with unit_of_work_on(connection) as work:
            RunRepo(work.connection).succeed(UUID(run_id), result=result, is_credible=is_credible)


def _claim(connection: psycopg.Connection[TupleRow], run_id: str) -> None:
    """Move one *named* run to running.

    Deliberately not ``RunRepo.claim``, which hands out whichever run is next in the queue. A
    promotion queues a backtest of its own, so by the time these tests land a second result the
    queue holds runs they never asked about, and claiming blindly would land a walk-forward's
    result on a backtest.
    """
    with unit_of_work_on(connection) as work:
        affected = work.connection.execute(
            "UPDATE run SET status = 'running', started_at = now(), claimed_by = 'test' "
            "WHERE id = %s AND status = 'queued'",
            (run_id,),
        ).rowcount
    assert affected == 1, f"run {run_id} was not queued"


def _optimize_result(base_yaml: str) -> dict[str, Any]:
    return {
        "strategy_name": "momentum_v2",
        "ticker": TICKER,
        "objective": "calmar",
        "optimized_yaml": _winning_yaml(base_yaml),
        "trials": 240,
        "test_metrics": {"total_trades": 44, "has_enough_trades_to_judge": True},
        "changes": [
            {
                "path": "indicators.rsi_ind.window",
                "old_value": 14,
                "new_value": 16,
                "low": 11,
                "high": 32,
                "moved": True,
                "at_bound": False,
            },
            {
                "path": "indicators.sma_long.window",
                "old_value": 200,
                "new_value": 300,
                "low": 100,
                "high": 300,
                "moved": True,
                "at_bound": True,
            },
        ],
    }


def _walk_forward_result(base_yaml: str, *, credible: bool) -> dict[str, Any]:
    checks = _PASSING_CHECKS if credible else _failing("deflated_sharpe", "overfitting")
    return {
        "strategy_name": "momentum_v2",
        "ticker": TICKER,
        "objective": "calmar",
        "scheme": "anchored",
        "optimized_yaml": _winning_yaml(base_yaml),
        "folds": [{"was_profitable": True}, {"was_profitable": True}],
        "benchmark": {"total_return_pct": 149.0},
        "combined_return_pct": 6.6,
        "profitable_folds": 2,
        "total_trades": 61,
        "checks": list(checks),
        "failures": [check["detail"] for check in checks if not check["passed"]],
        "is_credible": credible,
    }


def _optimize_run(client: TestClient, db_url: str, strategy_id: str) -> str:
    run_id = _launch(client, strategy_id, "optimize", epochs=2)
    _land(db_url, run_id, result=_optimize_result(_head_yaml(db_url, strategy_id)))
    return run_id


def _walk_forward_run(client: TestClient, db_url: str, strategy_id: str, *, credible: bool) -> str:
    run_id = _launch(client, strategy_id, "walk_forward", folds=2)
    _land(
        db_url,
        run_id,
        result=_walk_forward_result(_head_yaml(db_url, strategy_id), credible=credible),
        is_credible=credible,
    )
    return run_id


# --------------------------------------------------------------------------- refusals


def test_a_backtest_has_nothing_to_promote(client: TestClient, db_url: str) -> None:
    """A backtest runs the config exactly as written, so promoting it would copy the parent."""
    strategy_id = _strategy(client)
    run_id = _launch(client, strategy_id, "backtest")
    _land(db_url, run_id, result={"metrics": {"total_trades": 30}})

    response = client.post(f"{BASE}/runs/{run_id}/promote", json={})
    assert response.status_code == 409, response.text
    assert "nothing to promote" in response.json()["detail"]


def test_an_unfinished_run_cannot_be_promoted(client: TestClient) -> None:
    """There is no winning config until the search has finished producing one."""
    run_id = _launch(client, _strategy(client), "optimize", epochs=2)
    response = client.post(f"{BASE}/runs/{run_id}/promote", json={})
    assert response.status_code == 409, response.text
    assert "queued" in response.json()["detail"]


def test_a_taken_name_is_refused_rather_than_altered(client: TestClient, db_url: str) -> None:
    """The server does not quietly pick another name. A strategy nobody named is worse."""
    strategy_id = _strategy(client)
    run_id = _optimize_run(client, db_url, strategy_id)
    _strategy(client, "taken")

    response = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "taken"})
    assert response.status_code == 409, response.text
    assert "already exists" in response.json()["detail"]


# --------------------------------------------------------------------------- promoting


def test_promoting_records_its_lineage_and_queues_a_backtest(
    client: TestClient, db_url: str
) -> None:
    """One transaction: the strategy, its v1, and numbers already on the way."""
    strategy_id = _strategy(client)
    run_id = _optimize_run(client, db_url, strategy_id)

    response = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"]["version"] == 1
    assert body["version"]["origin"] == "promoted"

    detail = client.get(f"{BASE}/strategies/{body['strategy_id']}").json()
    assert detail["lineage"] == {
        "origin": "promoted",
        "parent_strategy_id": strategy_id,
        "parent_version": None,
        "origin_run_id": run_id,
    }
    assert detail["counts"]["backtest"] == 1

    backtest = client.get(f"{BASE}/runs/{body['backtest_run_id']}").json()["run"]
    assert backtest["status"] == "queued"
    assert backtest["strategy"]["id"] == body["strategy_id"]
    assert backtest["version"] == 1


def test_the_promoted_config_is_renamed_to_its_new_strategy(
    client: TestClient, db_url: str
) -> None:
    """Two strategies sharing one name inside their configs would collide on export."""
    run_id = _optimize_run(client, db_url, _strategy(client))
    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()

    detail = client.get(f"{BASE}/strategies/{promoted['strategy_id']}").json()
    assert detail["head"]["config"]["strategy"]["name"] == "momentum_v3"
    # And the parameter the search moved actually travelled. The stored `config` is the
    # canonical parsed form, which keeps an indicator's type-specific settings under `params`
    # -- the flattened address (`indicators.rsi_ind.window`) is the YAML's and the diff's.
    windows = {
        item["name"]: item["params"]["window"] for item in detail["head"]["config"]["indicators"]
    }
    assert windows["rsi_ind"] == 16


def test_the_default_name_is_offered_by_the_run(client: TestClient, db_url: str) -> None:
    """The promote dialog pre-fills from the server, and promoting without a name uses it."""
    run_id = _optimize_run(client, db_url, _strategy(client))
    offered = client.get(f"{BASE}/runs/{run_id}").json()["default_promote_name"]
    assert offered == "momentum_v2_opt1"

    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={}).json()
    assert client.get(f"{BASE}/strategies/{promoted['strategy_id']}").json()["name"] == offered


def test_a_run_that_cannot_be_promoted_offers_no_name(client: TestClient, db_url: str) -> None:
    run_id = _launch(client, _strategy(client), "backtest")
    _land(db_url, run_id, result={"metrics": {"total_trades": 30}})
    assert client.get(f"{BASE}/runs/{run_id}").json()["default_promote_name"] is None


# --------------------------------------------------------------------------- the warning


def test_promoting_an_optimize_always_carries_the_warning(client: TestClient, db_url: str) -> None:
    """An optimization is a search, not a validation, whatever its parent's verdict says."""
    strategy_id = _strategy(client)
    # Give the parent a credible walk-forward first: the warning must survive it, because the
    # run that passed measured the parameters the optimization has since moved.
    _walk_forward_run(client, db_url, strategy_id, credible=True)
    assert client.get(f"{BASE}/strategies/{strategy_id}").json()["verdict"]["state"] == "credible"

    run_id = _optimize_run(client, db_url, strategy_id)
    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()
    assert promoted["carried_warning"] is True

    detail = client.get(f"{BASE}/strategies/{promoted['strategy_id']}").json()
    warning = detail["promoted_warning"]
    assert warning is not None
    assert warning["origin_run_id"] == run_id
    assert warning["parent_name"] == "momentum_v2"
    assert "had not been shown to be credible" in warning["text"]


def test_promoting_a_credible_walk_forward_carries_no_warning(
    client: TestClient, db_url: str
) -> None:
    """The one case that clears it at birth: the promoted config is the validated config."""
    strategy_id = _strategy(client)
    run_id = _walk_forward_run(client, db_url, strategy_id, credible=True)

    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()
    assert promoted["carried_warning"] is False
    detail = client.get(f"{BASE}/strategies/{promoted['strategy_id']}").json()
    assert detail["origin_not_credible"] is False
    assert detail["promoted_warning"] is None


def test_promoting_an_uncredible_walk_forward_carries_the_warning(
    client: TestClient, db_url: str
) -> None:
    strategy_id = _strategy(client)
    run_id = _walk_forward_run(client, db_url, strategy_id, credible=False)

    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()
    assert promoted["carried_warning"] is True


def test_the_warning_clears_only_when_the_strategy_validates_itself(
    client: TestClient, db_url: str
) -> None:
    """The strategy's *own* credible walk-forward clears it. Nothing else does."""
    parent = _strategy(client)
    run_id = _optimize_run(client, db_url, parent)
    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()
    child = promoted["strategy_id"]
    assert client.get(f"{BASE}/strategies/{child}").json()["promoted_warning"] is not None

    # An uncredible walk-forward of its own does not clear it either.
    _walk_forward_run(client, db_url, child, credible=False)
    detail = client.get(f"{BASE}/strategies/{child}").json()
    assert detail["verdict"]["state"] == "not_credible"
    assert detail["promoted_warning"] is not None

    _walk_forward_run(client, db_url, child, credible=True)
    detail = client.get(f"{BASE}/strategies/{child}").json()
    assert detail["verdict"]["state"] == "credible"
    assert detail["promoted_warning"] is None
    # The snapshot is a historical fact and is never rewritten -- only the banner it feeds
    # is derived, and that is what went away.
    assert detail["origin_not_credible"] is True


def test_editing_the_head_brings_the_warning_back(client: TestClient, db_url: str) -> None:
    """The clearance was about a configuration, and the configuration has changed."""
    parent = _strategy(client)
    run_id = _optimize_run(client, db_url, parent)
    child = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"}).json()[
        "strategy_id"
    ]
    _walk_forward_run(client, db_url, child, credible=True)
    assert client.get(f"{BASE}/strategies/{child}").json()["promoted_warning"] is None

    config = client.get(f"{BASE}/strategies/{child}").json()["head"]["config"]
    config["execution"]["commission_pct"] = 0.08
    saved = client.post(
        f"{BASE}/strategies/{child}/versions", json={"base_version": 1, "config": config}
    )
    assert saved.status_code == 201, saved.text

    detail = client.get(f"{BASE}/strategies/{child}").json()
    assert detail["verdict"]["state"] == "unvalidated"
    assert detail["promoted_warning"] is not None


# --------------------------------------------------------------------------- the verdict


def test_the_verdict_block_carries_the_engine_failures(client: TestClient, db_url: str) -> None:
    """A red badge with no reason teaches the reader to ignore the badge."""
    strategy_id = _strategy(client)
    run_id = _walk_forward_run(client, db_url, strategy_id, credible=False)

    verdict = client.get(f"{BASE}/strategies/{strategy_id}").json()["verdict"]
    assert verdict["state"] == "not_credible"
    assert verdict["run_id"] == run_id
    assert verdict["run_number"] == 1
    assert verdict["version"] == 1
    assert verdict["failures"] == ["deflated_sharpe passed", "overfitting passed"]
    assert len(verdict["checks"]) == 8
    assert sum(1 for check in verdict["checks"] if not check["passed"]) == 2
    assert (
        verdict["summary"] == "out-of-sample +6.6% vs +149.0% buy-and-hold, profitable in 2/2 folds"
    )
    assert verdict["meta"] == "2 folds · anchored · calmar"


def test_the_verdict_follows_the_head(client: TestClient, db_url: str) -> None:
    """A credible run against v1 says nothing about v2, so the strategy reads unvalidated."""
    strategy_id = _strategy(client)
    _walk_forward_run(client, db_url, strategy_id, credible=True)
    assert client.get(f"{BASE}/strategies/{strategy_id}").json()["verdict"]["state"] == "credible"

    config = client.get(f"{BASE}/strategies/{strategy_id}").json()["head"]["config"]
    config["execution"]["commission_pct"] = 0.08
    client.post(
        f"{BASE}/strategies/{strategy_id}/versions",
        json={"base_version": 1, "config": config},
    )

    verdict = client.get(f"{BASE}/strategies/{strategy_id}").json()["verdict"]
    assert verdict["state"] == "unvalidated"
    assert verdict["run_id"] is None
    assert verdict["failures"] == []


# --------------------------------------------------------------------------- run detail


def test_an_optimize_reports_its_bounds(client: TestClient, db_url: str) -> None:
    """The engine already knows which parameters sat on their bound; the API does not guess."""
    run_id = _optimize_run(client, db_url, _strategy(client))
    diff = client.get(f"{BASE}/runs/{run_id}").json()["config_diff"]
    by_path = {move["path"]: move for move in diff}
    assert by_path["indicators.sma_long.window"]["at_bound"] is True
    assert by_path["indicators.sma_long.window"]["high"] == 300
    assert by_path["indicators.rsi_ind.window"]["at_bound"] is False


def test_a_walk_forward_reports_a_diff_without_inventing_bounds(
    client: TestClient, db_url: str
) -> None:
    """Each fold re-optimizes, so there is no single range to report. Say so, don't invent."""
    run_id = _walk_forward_run(client, db_url, _strategy(client), credible=False)
    diff = client.get(f"{BASE}/runs/{run_id}").json()["config_diff"]
    assert [move["path"] for move in diff] == ["indicators.rsi_ind.window"]
    assert diff[0]["old"] == 14
    assert diff[0]["new"] == 16
    assert diff[0]["low"] is None
    assert diff[0]["at_bound"] is False


def test_run_detail_passes_the_checks_through_unaltered(client: TestClient, db_url: str) -> None:
    run_id = _walk_forward_run(client, db_url, _strategy(client), credible=False)
    body = client.get(f"{BASE}/runs/{run_id}").json()
    assert [check["name"] for check in body["checks"]] == [
        check["name"] for check in body["result"]["checks"]
    ]


def test_a_backtest_has_no_checks_and_no_diff(client: TestClient, db_url: str) -> None:
    """Nothing was searched and nothing was judged. Empty is the honest answer."""
    run_id = _launch(client, _strategy(client), "backtest")
    _land(db_url, run_id, result={"metrics": {"total_trades": 30}})
    body = client.get(f"{BASE}/runs/{run_id}").json()
    assert body["checks"] == []
    assert body["config_diff"] == []


# --------------------------------------------------------------------------- deleting


def test_a_promoted_child_blocks_deleting_its_parent(client: TestClient, db_url: str) -> None:
    """A promotion points at the parent *and* at the run it came out of.

    Both are foreign keys into rows a purge of the parent would remove, and the child's own
    warning is rendered from that run. Deleting the parent underneath it would leave a strategy
    whose banner cites a measurement that no longer exists.
    """
    parent_id = _strategy(client)
    run_id = _optimize_run(client, db_url, parent_id)
    promoted = client.post(f"{BASE}/runs/{run_id}/promote", json={"name": "momentum_v3"})
    assert promoted.status_code == 201, promoted.text

    response = client.delete(f"{BASE}/strategies/{parent_id}")
    assert response.status_code == 409
    assert "momentum_v3" in response.json()["detail"]

    # And once the child is gone the parent is deletable, run and all. The child's own queued
    # backtest has to be cancelled first -- promotion launches one.
    child_id = promoted.json()["strategy_id"]
    backtest_id = promoted.json()["backtest_run_id"]
    assert client.post(f"{BASE}/runs/{backtest_id}/cancel").status_code == 202
    assert client.delete(f"{BASE}/strategies/{child_id}").status_code == 200
    assert client.delete(f"{BASE}/strategies/{parent_id}").status_code == 200
