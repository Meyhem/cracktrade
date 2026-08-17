"""Phase 5: the HTTP surface, through the real ASGI stack against a real database.

Route functions are thin enough that calling them directly would prove very little. What is
worth checking is everything *around* them: the status code a workflow refusal turns into, the
transaction the dependency opens, the problem+json a failure renders as, and the handful of
contract details that would otherwise be discovered by the UI much later -- a no-op save
minting nothing, a fork starting at v1, an import keeping the user's own formatting.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db

BASE = "/api/v1"

VALID_YAML = """\
strategy:
  name: momentum_v2
universe:
  ticker: NVDA
  start_date: "2018-01-01"
  end_date: "2025-12-31"
execution:
  initial_capital: 10000.0
  slippage_pct: 0.1
  commission_pct: 0.05
indicators:
  - name: sma_long
    type: sma
    window: 200
entry:
  signal: "close > sma_long"
exit:
  stop_loss_pct: 5.0
"""


def _config() -> dict[str, Any]:
    return {
        "strategy": {"name": "momentum_v2"},
        "universe": {
            "ticker": "NVDA",
            "start_date": "2018-01-01",
            "end_date": "2025-12-31",
        },
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.1,
            "commission_pct": 0.05,
        },
        "indicators": [{"name": "sma_long", "type": "sma", "window": 200}],
        "entry": {"signal": "close > sma_long"},
        "exit": {"stop_loss_pct": 5.0},
    }


def _create(client: TestClient, name: str = "momentum_v2", ticker: str = "NVDA") -> dict[str, Any]:
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": name,
            "ticker": ticker,
            "start_date": "2018-01-01",
            "end_date": "2025-12-31",
        },
    )
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()["strategy"]
    return detail


# --------------------------------------------------------------------------- meta


def test_meta_reports_the_engines_own_constants(client: TestClient) -> None:
    """Restating the trade floor in the client is how a UI and an engine come to disagree."""
    body = client.get(f"{BASE}/meta").json()
    assert body["trade_floor"] == 20
    assert body["significance"] == 0.95
    assert "calmar" in body["objectives"]
    assert set(body["fold_schemes"]) == {"anchored", "rolling"}
    assert len(body["indicators"]) > 50
    assert body["limits"]


def test_meta_orders_the_stop_priority_chain(client: TestClient) -> None:
    """The editor needs to say which stop shadows which without hard-coding the chain again."""
    fields = {
        f["name"]: f["stop_priority"] for f in client.get(f"{BASE}/meta").json()["exit_fields"]
    }
    assert fields["atr_stop_multiplier"] == 1
    assert fields["trailing_stop_pct"] == 2
    assert fields["stop_loss_pct"] == 3
    assert fields["take_profit_pct"] is None


# --------------------------------------------------------------------------- validation


def test_a_valid_config_reports_its_namespace_and_search_space(client: TestClient) -> None:
    body = client.post(f"{BASE}/config/validate", json={"config": _config()}).json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert "sma_long" in body["namespace"]
    assert "close" in body["namespace"]
    # Spec 9.1 discovers numeric leaves in `indicators` and `exit` alike, so a stop is tunable
    # exactly as an indicator window is. The entry contributes nothing: it holds only `signal`,
    # and numbers written inside an expression are unreachable by the search.
    paths = {p["path"] for p in body["searchable_parameters"]}
    assert paths == {"indicators.sma_long.window", "exit.stop_loss_pct"}
    window = next(p for p in body["searchable_parameters"] if p["path"].endswith("window"))
    assert (window["low"], window["value"], window["high"]) == (100.0, 200.0, 300.0)
    assert body["canonical_yaml"].startswith("strategy:")


def test_an_invalid_config_answers_200_with_field_errors(client: TestClient) -> None:
    """Invalidity is the answer. A config being typed is exactly what this endpoint is for."""
    broken = _config()
    broken["exit"] = {}
    response = client.post(f"{BASE}/config/validate", json={"config": broken})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(issue["path"] == "exit" for issue in body["errors"])


def test_errors_are_addressed_to_the_field_that_caused_them(client: TestClient) -> None:
    """So the editor can put the message on the input rather than in a banner."""
    broken = _config()
    broken["execution"]["initial_capital"] = -1
    body = client.post(f"{BASE}/config/validate", json={"config": broken}).json()
    paths = {issue["path"] for issue in body["errors"]}
    assert "execution.initial_capital" in paths


def test_malformed_yaml_reports_the_line(client: TestClient) -> None:
    body = client.post(
        f"{BASE}/config/validate", json={"yaml": "strategy:\n  name: [unclosed\n"}
    ).json()
    assert body["valid"] is False
    assert body["errors"][0]["line"]


def test_a_shadowed_stop_warns_without_invalidating(client: TestClient) -> None:
    config = _config()
    config["exit"] = {"atr_stop_multiplier": 2.5, "stop_loss_pct": 5.0}
    body = client.post(f"{BASE}/config/validate", json={"config": config}).json()
    assert body["valid"] is True
    assert [warning["path"] for warning in body["warnings"]] == ["exit.stop_loss_pct"]


def test_supplying_both_forms_is_refused(client: TestClient) -> None:
    response = client.post(
        f"{BASE}/config/validate", json={"config": _config(), "yaml": VALID_YAML}
    )
    assert response.status_code >= 400


# --------------------------------------------------------------------------- strategies


def test_creating_a_strategy_starts_it_never_run(client: TestClient) -> None:
    """No run is launched on creation, and the list must say so rather than imply neutrality."""
    detail = _create(client)
    assert detail["head"]["version"] == 1
    assert detail["verdict"]["state"] == "never_run"
    # Nothing to point at: never-run is the absence of a walk-forward, not a failed one.
    assert detail["verdict"]["run_id"] is None
    assert detail["verdict"]["failures"] == []
    assert detail["promoted_warning"] is None
    assert detail["counts"]["versions"] == 1
    assert detail["lineage"]["origin"] == "authored"


def test_a_duplicate_name_is_a_conflict(client: TestClient) -> None:
    _create(client)
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": "momentum_v2",
            "ticker": "AAPL",
            "start_date": "2018-01-01",
            "end_date": "2025-12-31",
        },
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "already exists" in response.json()["detail"]


def test_an_unknown_strategy_is_a_404_problem(client: TestClient) -> None:
    response = client.get(f"{BASE}/strategies/00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == 404
    assert body["title"]
    assert body["type"].endswith("not-found")


def test_the_list_filters_by_search_and_verdict(client: TestClient) -> None:
    _create(client, name="momentum_v2", ticker="NVDA")
    _create(client, name="gap_fade_spy", ticker="SPY")

    everything = client.get(f"{BASE}/strategies").json()
    assert len(everything["strategies"]) == 2
    assert everything["totals"]["never_run"] == 2
    assert everything["totals"]["credible"] == 0

    by_name = client.get(f"{BASE}/strategies", params={"search": "momentum"}).json()
    assert [s["name"] for s in by_name["strategies"]] == ["momentum_v2"]

    by_ticker = client.get(f"{BASE}/strategies", params={"search": "spy"}).json()
    assert [s["name"] for s in by_ticker["strategies"]] == ["gap_fade_spy"]

    filtered = client.get(f"{BASE}/strategies", params={"verdict": "credible"}).json()
    assert filtered["strategies"] == []


def test_import_keeps_the_file_as_written(client: TestClient) -> None:
    """Importing and reading back must not silently reformat the user's own document."""
    response = client.post(
        f"{BASE}/strategies/import", json={"yaml": VALID_YAML, "filename": "momentum_v2.yaml"}
    )
    assert response.status_code == 201
    detail = response.json()["strategy"]
    assert detail["head"]["yaml"] == VALID_YAML
    assert detail["lineage"]["origin"] == "imported"
    assert "momentum_v2.yaml" in detail["head"]["note"]


def test_importing_an_invalid_file_is_422_with_field_errors(client: TestClient) -> None:
    response = client.post(f"{BASE}/strategies/import", json={"yaml": "strategy:\n  name: x\n"})
    assert response.status_code == 422
    body = response.json()
    assert body["errors"]
    assert client.get(f"{BASE}/strategies").json()["strategies"] == []


def test_a_fork_starts_a_fresh_history_at_v1(client: TestClient) -> None:
    """A fork does not inherit the parent's versions; it records where it came from."""
    parent = _create(client)
    client.post(
        f"{BASE}/strategies/{parent['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {"stop_loss_pct": 9.0}}},
    )

    response = client.post(f"{BASE}/strategies/{parent['id']}/fork", json={"name": "momentum_v3"})
    assert response.status_code == 201
    fork = response.json()["strategy"]
    assert fork["head"]["version"] == 1
    assert fork["lineage"]["origin"] == "forked"
    assert fork["lineage"]["parent_strategy_id"] == parent["id"]
    assert fork["lineage"]["parent_version"] == 2
    assert fork["head"]["config"]["strategy"]["name"] == "momentum_v3"


def test_a_fork_can_start_from_an_older_version(client: TestClient) -> None:
    parent = _create(client)
    client.post(
        f"{BASE}/strategies/{parent['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {"stop_loss_pct": 9.0}}},
    )
    fork = client.post(
        f"{BASE}/strategies/{parent['id']}/fork", json={"name": "from_v1", "version": 1}
    ).json()["strategy"]
    assert fork["lineage"]["parent_version"] == 1
    assert fork["head"]["config"]["exit"]["stop_loss_pct"] == 5.0


# --------------------------------------------------------------------------- versions


def test_saving_appends_the_next_version(client: TestClient) -> None:
    created = _create(client)
    changed = {**_config(), "exit": {"stop_loss_pct": 9.0}}

    response = client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": changed, "note": "wider stop"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version"]["version"] == 2
    assert body["version"]["note"] == "wider stop"
    assert body["version"]["origin"] == "edited"


def test_a_save_against_a_stale_base_is_refused(client: TestClient) -> None:
    """Last-write-wins would lose an edit while reporting success."""
    created = _create(client)
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {"stop_loss_pct": 9.0}}},
    )

    response = client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {"stop_loss_pct": 7.0}}},
    )
    assert response.status_code == 409
    assert "head is now v2" in response.json()["detail"]


def test_a_save_that_changes_nothing_creates_nothing(client: TestClient) -> None:
    """A version that differs in no respect would be noise in a history meant to explain runs."""
    created = _create(client)
    head_config = created["head"]["config"]

    response = client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": head_config},
    )
    assert response.status_code == 409
    assert "nothing changed" in response.json()["detail"]
    assert len(client.get(f"{BASE}/strategies/{created['id']}/versions").json()) == 1


def test_an_invalid_save_is_rejected_outright(client: TestClient) -> None:
    """No version is created, and the errors attach to the fields that caused them."""
    created = _create(client)
    response = client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {}}},
    )
    assert response.status_code == 422
    assert any(issue["path"] == "exit" for issue in response.json()["errors"])
    assert len(client.get(f"{BASE}/strategies/{created['id']}/versions").json()) == 1


def test_the_history_summarises_each_change(client: TestClient) -> None:
    created = _create(client)
    changed = {**_config()}
    changed["execution"] = {**changed["execution"], "commission_pct": 0.08}
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": changed, "note": "broker fee schedule changed"},
    )

    history = client.get(f"{BASE}/strategies/{created['id']}/versions").json()
    assert [entry["version"] for entry in history] == [2, 1]
    assert history[0]["head"] is True
    assert history[1]["head"] is False

    summary = history[0]["change_summary"]
    assert any(change["path"] == "execution.commission_pct" for change in summary)
    assert "execution_changed" in history[0]["flags"]


def test_restoring_appends_rather_than_rewinds(client: TestClient) -> None:
    """Nothing is deleted. The restored config arrives as a new head."""
    created = _create(client)
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": {**_config(), "exit": {"stop_loss_pct": 9.0}}},
    )

    response = client.post(f"{BASE}/strategies/{created['id']}/versions/1/restore", json={})
    assert response.status_code == 201
    restored = response.json()
    assert restored["version"] == 3
    assert restored["restored_from"] == 1
    assert restored["config"]["exit"]["stop_loss_pct"] == 5.0

    history = client.get(f"{BASE}/strategies/{created['id']}/versions").json()
    assert [entry["version"] for entry in history] == [3, 2, 1]


def test_restoring_the_head_is_refused(client: TestClient) -> None:
    created = _create(client)
    response = client.post(f"{BASE}/strategies/{created['id']}/versions/1/restore", json={})
    assert response.status_code == 409


def test_the_diff_groups_changes_and_names_the_consequence(client: TestClient) -> None:
    """Two changed numbers under execution are one fact: the cost model moved."""
    created = _create(client)
    changed = {**_config()}
    changed["execution"] = {**changed["execution"], "commission_pct": 0.08, "slippage_pct": 0.2}
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": changed},
    )

    body = client.get(f"{BASE}/strategies/{created['id']}/diff", params={"from": 1, "to": 2}).json()
    sections = {group["section"]: group for group in body["groups"]}

    assert len(sections["execution"]["changes"]) == 2
    assert "cost model" in sections["execution"]["consequence"]
    assert sections["universe"]["changes"] == []
    assert sections["universe"]["consequence"] is None
    assert body["from_yaml"] and body["to_yaml"]
    assert body["to_version"]["head"] is True


def test_a_diff_against_a_missing_version_is_404(client: TestClient) -> None:
    created = _create(client)
    response = client.get(f"{BASE}/strategies/{created['id']}/diff", params={"from": 1, "to": 99})
    assert response.status_code == 404


# --------------------------------------------------------------------------- the surface itself


def test_unknown_request_fields_are_rejected(client: TestClient) -> None:
    """The same rule the engine applies to config files: an unrecognised key is an error."""
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": "x",
            "ticker": "NVDA",
            "start_date": "2018-01-01",
            "end_date": "2025-12-31",
            "watchdog": True,
        },
    )
    assert response.status_code == 422


def test_there_is_no_way_to_delete_anything(client: TestClient) -> None:
    """Append-only is a product guarantee, so the verb must not exist on the surface."""
    created = _create(client)
    for path in (
        f"{BASE}/strategies/{created['id']}",
        f"{BASE}/strategies/{created['id']}/versions/1",
    ):
        assert client.delete(path).status_code in (404, 405)


def test_the_openapi_document_is_served(client: TestClient) -> None:
    """Generated from the same models the tests exercise, so it cannot drift from them."""
    document = client.get(f"{BASE}/openapi.json").json()
    assert f"{BASE}/strategies" in document["paths"]
    assert f"{BASE}/config/validate" in document["paths"]
