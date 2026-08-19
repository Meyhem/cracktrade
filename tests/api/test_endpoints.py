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


def test_meta_lists_every_bar_interval_with_its_provider_reach(client: TestClient) -> None:
    """The client must not restate this list. An interval it offers and the engine refuses, or
    one the engine accepts and it hides, is a drift no test on either side would catch.
    """
    intervals = client.get(f"{BASE}/meta").json()["intervals"]

    by_value = {option["value"]: option for option in intervals}
    assert set(by_value) == {"15m", "30m", "1h", "1d"}
    assert by_value["1d"]["intraday"] is False
    assert by_value["1d"]["max_lookback_days"] is None
    assert by_value["30m"]["intraday"] is True
    assert 0 < by_value["30m"]["max_lookback_days"] < by_value["1h"]["max_lookback_days"]


def test_meta_says_which_intervals_an_evolution_can_be_divided_at(client: TestClient) -> None:
    """Four segments of sixty sessions plus a holdout does not fit in fifty-five days.

    Computed from the engine's own floors rather than listed, so raising the segment count or
    the floor moves this with it rather than leaving a stale list behind. Without it the client
    would have to restate the arithmetic or let the user fill in a long form for a run that
    cannot start.
    """
    intervals = client.get(f"{BASE}/meta").json()["intervals"]

    evolvable = {option["value"]: option["evolvable"] for option in intervals}
    assert evolvable == {"15m": False, "30m": False, "1h": True, "1d": True}


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


def test_a_structural_error_in_yaml_carries_its_line_and_still_names_its_field(
    client: TestClient,
) -> None:
    """Spec 3.9 promises the offending line where recoverable; the editor marks the gutter.

    The two halves have to arrive separately. The engine renders the location as
    ``path (line N)``, and a client that got only that string would have to parse the line back
    out to know which field to attach the message to -- so a config with a line number would
    lose the field attribution a config without one keeps.
    """
    body = client.post(
        f"{BASE}/config/validate",
        json={"yaml": VALID_YAML.replace("initial_capital: 10000.0", "initial_capital: -1")},
    ).json()

    issue = next(issue for issue in body["errors"] if issue["path"] == "execution.initial_capital")
    assert issue["line"] == 8
    assert "(line" not in issue["path"]


def test_the_same_error_from_a_mapping_has_no_line_and_the_same_path(client: TestClient) -> None:
    """A mapping was never text, so there is no line to report and none is invented."""
    broken = _config()
    broken["execution"]["initial_capital"] = -1
    body = client.post(f"{BASE}/config/validate", json={"config": broken}).json()

    issue = next(issue for issue in body["errors"] if issue["path"] == "execution.initial_capital")
    assert issue["line"] is None


def test_prose_containing_a_colon_is_not_mistaken_for_a_field(client: TestClient) -> None:
    """`field_issue` splits on ": ", and the engine's own prose contains colons too."""
    from cracktrade.api.services.config import field_issue

    text = "this strategy has no optimizable parameters: every numeric field is pinned"
    issue = field_issue(text)
    assert issue.path == ""
    assert issue.message == text


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


# --------------------------------------------------------------------------- config diff


def _changes(body: dict[str, Any]) -> dict[str, Any]:
    return {
        change["path"]: (change["old"], change["new"])
        for group in body["groups"]
        for change in group["changes"]
    }


def test_config_diff_reports_moved_leaves_under_their_section(client: TestClient) -> None:
    moved = _config()
    moved["indicators"] = [{"name": "sma_long", "type": "sma", "window": 150}]
    body = client.post(
        f"{BASE}/config/diff", json={"from": {"config": _config()}, "to": {"config": moved}}
    ).json()

    assert _changes(body) == {"indicators.sma_long.window": (200.0, 150.0)}
    indicators = next(group for group in body["groups"] if group["section"] == "indicators")
    assert len(indicators["changes"]) == 1


def test_config_diff_lists_unchanged_sections_too(client: TestClient) -> None:
    """ "Indicators: no changes" is information; leaving it out asks the reader to infer it."""
    body = client.post(
        f"{BASE}/config/diff", json={"from": {"config": _config()}, "to": {"config": _config()}}
    ).json()

    assert _changes(body) == {}
    assert [group["section"] for group in body["groups"]] == [
        "strategy",
        "universe",
        "execution",
        "indicators",
        "entry",
        "exit",
        "position_sizing",
    ]


def test_config_diff_flags_the_sections_that_break_comparability(client: TestClient) -> None:
    moved = _config()
    moved["execution"]["commission_pct"] = 0.2
    body = client.post(
        f"{BASE}/config/diff", json={"from": {"config": _config()}, "to": {"config": moved}}
    ).json()

    execution = next(group for group in body["groups"] if group["section"] == "execution")
    assert execution["consequence"] is not None
    unchanged = next(group for group in body["groups"] if group["section"] == "entry")
    assert unchanged["consequence"] is None


def test_config_diff_canonicalises_both_sides_before_comparing(client: TestClient) -> None:
    """The guarantee the Promote dialog rests on.

    One side is a mapping the user stored, the other is YAML a search emitted. Key order and
    defaults the user omitted differ between the two representations and are not changes. A
    dialog whose whole job is to say which numbers a machine chose must not name a field the
    search never touched.
    """
    canonical = client.post(f"{BASE}/config/validate", json={"config": _config()}).json()
    body = client.post(
        f"{BASE}/config/diff",
        json={"from": {"config": _config()}, "to": {"yaml": canonical["canonical_yaml"]}},
    ).json()

    assert _changes(body) == {}


def test_config_diff_paths_are_the_paths_the_editor_and_optimizer_use(
    client: TestClient,
) -> None:
    """One path per fact, across three surfaces that all name the same leaf.

    Pinned because it broke once already: canonicalising through the pydantic model instead of
    through the YAML writer nests indicator parameters under ``params``, and a promote dialog
    naming ``indicators.sma_long.params.window`` while the editor offers to search
    ``indicators.sma_long.window`` presents one parameter as two.
    """
    moved = _config()
    moved["indicators"] = [{"name": "sma_long", "type": "sma", "window": 150}]
    moved["exit"] = {"stop_loss_pct": 7.0}

    diff = client.post(
        f"{BASE}/config/diff", json={"from": {"config": _config()}, "to": {"config": moved}}
    ).json()
    validated = client.post(f"{BASE}/config/validate", json={"config": _config()}).json()

    searchable = {parameter["path"] for parameter in validated["searchable_parameters"]}
    assert set(_changes(diff)) == {"indicators.sma_long.window", "exit.stop_loss_pct"}
    assert set(_changes(diff)) <= searchable


def test_config_diff_refuses_an_invalid_side(client: TestClient) -> None:
    """Unlike /config/validate: there is no half-typed state to support here."""
    broken = _config()
    broken["exit"] = {}
    response = client.post(
        f"{BASE}/config/diff", json={"from": {"config": _config()}, "to": {"config": broken}}
    )
    assert response.status_code >= 400


def test_config_diff_refuses_a_side_supplying_both_forms(client: TestClient) -> None:
    response = client.post(
        f"{BASE}/config/diff",
        json={"from": {"config": _config(), "yaml": VALID_YAML}, "to": {"config": _config()}},
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


# --------------------------------------------------------------------------- bar interval


def test_a_created_strategy_defaults_to_daily_bars(client: TestClient) -> None:
    detail = _create(client)
    assert detail["head"]["config"]["universe"]["interval"] == "1d"


def test_the_new_dialog_can_ask_for_intraday_bars(client: TestClient) -> None:
    """The interval is written into the seed, not left implied by its absence.

    A field that decides what every bar count in the results means should be visible in the
    document the user is about to edit.
    """
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": "intraday",
            "ticker": "SAP.DE",
            "start_date": "2026-07-01",
            "end_date": "2026-08-18",
            "interval": "30m",
        },
    )
    assert response.status_code == 201, response.text
    detail = response.json()["strategy"]
    assert detail["head"]["config"]["universe"]["interval"] == "30m"
    assert "interval: 30m" in detail["head"]["yaml"]


def test_an_unknown_interval_is_addressed_to_its_own_field(client: TestClient) -> None:
    """Not a schema error. The engine owns the list of intervals, so it owns the message."""
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": "hourly-ish",
            "ticker": "SAP.DE",
            "start_date": "2026-07-01",
            "end_date": "2026-08-18",
            "interval": "45m",
        },
    )
    assert response.status_code == 422, response.text
    paths = [issue["path"] for issue in response.json()["errors"]]
    assert "universe.interval" in paths


def test_a_range_wider_than_the_provider_serves_is_refused_at_creation(
    client: TestClient,
) -> None:
    """Spec 3.3.1: the width check is deterministic, so it can run this early."""
    response = client.post(
        f"{BASE}/strategies",
        json={
            "name": "too-wide",
            "ticker": "SAP.DE",
            "start_date": "2020-01-01",
            "end_date": "2026-08-18",
            "interval": "30m",
        },
    )
    assert response.status_code == 422, response.text


def test_the_strategy_list_labels_every_row_with_its_interval(client: TestClient) -> None:
    _create(client, name="daily_one", ticker="NVDA")
    client.post(
        f"{BASE}/strategies",
        json={
            "name": "intraday_one",
            "ticker": "SAP.DE",
            "start_date": "2026-07-01",
            "end_date": "2026-08-18",
            "interval": "1h",
        },
    )

    rows = client.get(f"{BASE}/strategies").json()["strategies"]

    by_name = {row["name"]: row["interval"] for row in rows}
    assert by_name == {"daily_one": "1d", "intraday_one": "1h"}


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


def _with_window(config: dict[str, Any], indicator: str, window: int) -> dict[str, Any]:
    """One indicator's window moved, in the ``params``-nested form a version is stored in."""
    return {
        **config,
        "indicators": [
            {**item, "params": {**item["params"], "window": window}}
            if item["name"] == indicator
            else item
            for item in config["indicators"]
        ],
    }


def test_the_version_diff_addresses_a_parameter_the_way_every_other_surface_does(
    client: TestClient,
) -> None:
    """The fourth surface of the §15.1 rule, and the one that was breaking it.

    Versions are stored as ``model_dump``, which nests type-specific indicator settings under
    ``params``. Diffing two stored configs directly therefore named the leaf
    ``indicators.sma_long.params.window`` while ``POST /config/diff``, the editor's searchable
    parameters and the optimizer's parameter paths all name it ``indicators.sma_long.window`` --
    one field with two addresses, decided by which endpoint the reader happened to be looking at.
    """
    created = _create(client)
    seed = created["head"]["config"]
    moved = _with_window(seed, "sma_long", 150)
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": moved},
    )

    diff = client.get(f"{BASE}/strategies/{created['id']}/diff", params={"from": 1, "to": 2}).json()
    validated = client.post(f"{BASE}/config/validate", json={"config": seed}).json()
    searchable = {parameter["path"] for parameter in validated["searchable_parameters"]}

    assert set(_changes(diff)) == {"indicators.sma_long.window"}
    assert set(_changes(diff)) <= searchable


def test_the_history_summary_uses_the_same_paths_as_the_diff(client: TestClient) -> None:
    """The timeline's one-line summary is the same question, so it is the same answer."""
    created = _create(client)
    client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={
            "base_version": 1,
            "config": _with_window(created["head"]["config"], "sma_long", 150),
        },
    )

    history = client.get(f"{BASE}/strategies/{created['id']}/versions").json()
    assert [change["path"] for change in history[0]["change_summary"]] == [
        "indicators.sma_long.window"
    ]


def test_the_diff_panes_are_written_in_one_hand(client: TestClient) -> None:
    """An import keeps its own formatting; a diff of it against a later save must not.

    ``import_strategy`` deliberately stores the uploaded document byte for byte, so serving the
    stored text as the panes puts a user's flow-style YAML beside the canonical block style of
    the next save. Every line then highlights as changed, and the structured summary above --
    correctly reporting one moved number -- corresponds to nothing the reader can find below it.
    """
    terse = (
        "strategy: {name: terse_import}\n"
        "universe: {ticker: NVDA, start_date: 2018-01-01, end_date: 2025-12-31}\n"
        "execution: {initial_capital: 10000, slippage_pct: 0.1, commission_pct: 0.05}\n"
        "indicators: [{name: sma_long, type: sma, window: 200}]\n"
        "entry: {signal: close > sma_long}\n"
        "exit: {stop_loss_pct: 5.0}\n"
    )
    imported = client.post(
        f"{BASE}/strategies/import", json={"yaml": terse, "filename": "terse.yaml"}
    ).json()["strategy"]
    edited = {**imported["head"]["config"]}
    edited["exit"] = {**edited["exit"], "stop_loss_pct": 7.0}
    client.post(
        f"{BASE}/strategies/{imported['id']}/versions",
        json={"base_version": 1, "config": edited},
    )

    diff_url = f"{BASE}/strategies/{imported['id']}/diff"
    body = client.get(diff_url, params={"from": 1, "to": 2}).json()
    before = body["from_yaml"].splitlines()
    after = body["to_yaml"].splitlines()

    assert set(_changes(body)) == {"exit.stop_loss_pct"}
    # One moved number, so exactly one line may differ between the panes.
    assert len(before) == len(after)
    assert [line for old, line in zip(before, after, strict=True) if old != line] == [
        "  stop_loss_pct: 7.0"
    ]

    # The stored document itself is untouched: only the comparison canonicalises.
    stored = client.get(f"{BASE}/strategies/{imported['id']}/versions/1").json()
    assert stored["yaml"] == terse


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


def test_a_version_has_no_delete_of_its_own(client: TestClient) -> None:
    """Append-only survived the arrival of strategy deletion, narrowed rather than withdrawn.

    A strategy can be deleted whole. Reaching into its history and removing one version cannot
    be done at any layer -- that is the operation that would let a result be explained by a
    config that no longer says what it said.
    """
    created = _create(client)
    assert client.delete(f"{BASE}/strategies/{created['id']}/versions/1").status_code in (404, 405)


# --------------------------------------------------------------------------- deleting


def test_deleting_a_strategy_removes_it_and_reports_what_went(client: TestClient) -> None:
    created = _create(client)
    response = client.delete(f"{BASE}/strategies/{created['id']}")

    assert response.status_code == 200, response.text
    assert response.json() == {"name": "momentum_v2", "versions": 1, "runs": 0, "series": 0}
    assert client.get(f"{BASE}/strategies/{created['id']}").status_code == 404
    assert client.get(f"{BASE}/strategies").json()["strategies"] == []


def test_deleting_takes_the_whole_history_with_it(client: TestClient) -> None:
    """Every version, not only the head -- and the count says how many."""
    created = _create(client)
    edited = {**_config(), "entry": {"signal": "close > sma_long * 1.01"}}
    saved = client.post(
        f"{BASE}/strategies/{created['id']}/versions",
        json={"base_version": 1, "config": edited},
    )
    assert saved.status_code == 201, saved.text

    response = client.delete(f"{BASE}/strategies/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["versions"] == 2


def test_deleting_an_unknown_strategy_is_a_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-0000000000ff"
    response = client.delete(f"{BASE}/strategies/{missing}")
    assert response.status_code == 404
    assert response.json()["type"].endswith("not-found")


def test_deleting_frees_the_name(client: TestClient) -> None:
    """Names are unique, so a delete that left one reserved would be a delete that half-worked."""
    created = _create(client)
    assert client.delete(f"{BASE}/strategies/{created['id']}").status_code == 200
    assert _create(client)["name"] == "momentum_v2"


def test_a_strategy_with_a_queued_run_is_not_deletable(client: TestClient) -> None:
    """The worker holds a lease on it; the caller cancels first, deliberately rather than as a
    side effect of a delete they might still reconsider."""
    created = _create(client)
    launched = client.post(f"{BASE}/strategies/{created['id']}/runs", json={"kind": "backtest"})
    assert launched.status_code == 202, launched.text

    response = client.delete(f"{BASE}/strategies/{created['id']}")
    assert response.status_code == 409
    assert "queued or running" in response.json()["detail"]
    assert client.get(f"{BASE}/strategies/{created['id']}").status_code == 200


def test_a_cancelled_run_no_longer_blocks_the_delete(client: TestClient) -> None:
    """The refusal is about runs in flight, not about a strategy having ever been run."""
    created = _create(client)
    launched = client.post(f"{BASE}/strategies/{created['id']}/runs", json={"kind": "backtest"})
    run_id = launched.json()["id"]
    # 202: a queued run is ended outright, since nothing has started to ask.
    assert client.post(f"{BASE}/runs/{run_id}/cancel").status_code == 202

    response = client.delete(f"{BASE}/strategies/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["runs"] == 1


def test_a_forked_child_blocks_deleting_its_parent(client: TestClient) -> None:
    """Its lineage is a foreign key into rows this would remove, and "forked from X v1" is a
    historical fact rather than a crumb worth pointing at nothing."""
    parent = _create(client)
    forked = client.post(f"{BASE}/strategies/{parent['id']}/fork", json={"name": "momentum_v3"})
    assert forked.status_code == 201, forked.text

    response = client.delete(f"{BASE}/strategies/{parent['id']}")
    assert response.status_code == 409
    assert "momentum_v3" in response.json()["detail"]

    # The child itself has nothing descending from it, and deleting it clears the way.
    child_id = forked.json()["strategy"]["id"]
    assert client.delete(f"{BASE}/strategies/{child_id}").status_code == 200
    assert client.delete(f"{BASE}/strategies/{parent['id']}").status_code == 200


def test_the_refusal_names_every_descendant(client: TestClient) -> None:
    parent = _create(client)
    for name in ("branch_a", "branch_b"):
        assert (
            client.post(f"{BASE}/strategies/{parent['id']}/fork", json={"name": name}).status_code
            == 201
        )

    detail = client.delete(f"{BASE}/strategies/{parent['id']}").json()["detail"]
    assert "branch_a" in detail
    assert "branch_b" in detail


def test_deleting_a_fork_leaves_its_parent_alone(client: TestClient) -> None:
    """Lineage points one way. A child is not part of its parent, and removing it takes
    nothing of the parent's with it."""
    parent = _create(client)
    forked = client.post(f"{BASE}/strategies/{parent['id']}/fork", json={"name": "momentum_v3"})
    child_id = forked.json()["strategy"]["id"]

    assert client.delete(f"{BASE}/strategies/{child_id}").status_code == 200
    parent_detail = client.get(f"{BASE}/strategies/{parent['id']}")
    assert parent_detail.status_code == 200
    assert parent_detail.json()["counts"]["versions"] == 1


def test_the_openapi_document_is_served(client: TestClient) -> None:
    """Generated from the same models the tests exercise, so it cannot drift from them."""
    document = client.get(f"{BASE}/openapi.json").json()
    assert f"{BASE}/strategies" in document["paths"]
    assert f"{BASE}/config/validate" in document["paths"]
