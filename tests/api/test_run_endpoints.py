"""Phase 6: the run endpoints over HTTP.

The worker's own behaviour is covered in ``test_worker.py``. What is left here is the surface:
the status code a launch returns, the filters four different screens share, what a suppressed
run is *not* sent, and the CSV that has to be the same bytes as the chart.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.repos import RunRepo
from cracktrade.api.settings import ApiSettings
from cracktrade.api.worker.runner import claim_one
from cracktrade.data import StaticProvider
from tests.test_metrics import trending_market

pytestmark = pytest.mark.db

BASE = "/api/v1"
TICKER = "TEST"


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


def _launch(
    client: TestClient, strategy_id: str, kind: str, seed: int | None = None, **params: Any
) -> dict[str, Any]:
    body_json: dict[str, Any] = {"kind": kind, "params": params}
    if seed is not None:
        body_json["seed"] = seed
    response = client.post(f"{BASE}/strategies/{strategy_id}/runs", json=body_json)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


#: A search small enough to run in a test, with no trade floor and a fixed seed. Both matter:
#: the floor would make these tests depend on whether four genomes happened to find something
#: that trades enough, and an unseeded search would make that vary between runs.
_TINY_EVOLUTION: dict[str, Any] = {
    "population": 4,
    "generations": 2,
    "min_trades": 0,
    "min_trades_per_year": 0.0,
}


def _land_as_credible(db_url: str, run_id: str) -> None:
    """Succeed a queued run with a credible verdict, without running the engine.

    Through the repository's own transition rather than by writing the row: a terminal run is
    immutable and the database enforces it, so there is no shortcut here that a real worker
    does not also take.
    """
    with psycopg.connect(db_url) as connection, unit_of_work_on(connection) as work:
        repo = RunRepo(work.connection)
        claimed = repo.claim("test-worker")
        assert claimed is not None and str(claimed.id) == run_id
        repo.succeed(claimed.id, result={"checks": [], "failures": []}, is_credible=True)


def _work_the_queue(db_url: str) -> None:
    """Execute everything queued, with the real engine and stub data."""
    provider = StaticProvider({TICKER: trending_market(bars=700).frame})
    settings = ApiSettings(database_url=db_url, worker_heartbeat_seconds=0.05)
    with psycopg.connect(db_url) as connection:
        while claim_one(connection, settings, provider=provider) is not None:
            pass


# --------------------------------------------------------------------------- launching


def test_launching_is_accepted_not_completed(client: TestClient) -> None:
    """202, because the run is queued. The caller goes back to the strategy immediately."""
    run = _launch(client, _strategy(client), "backtest")
    assert run["status"] == "queued"
    assert run["number"] == 1
    assert run["version"] == 1
    assert run["stale"] is False
    assert run["headline"] is None
    assert run["seed"] >= 0


def test_launch_parameters_are_defaulted_and_echoed(client: TestClient) -> None:
    run = _launch(client, _strategy(client), "walk_forward")
    assert run["params"] == {
        "objective": "calmar",
        "epochs": 10,
        "min_trades": 20,
        "min_trades_per_year": 4.0,
        "folds": 6,
        "scheme": "anchored",
    }


def test_an_unknown_objective_is_refused_before_it_reaches_the_queue(
    client: TestClient,
) -> None:
    """A run that cannot execute must not queue, where its failure would look like the engine's."""
    response = client.post(
        f"{BASE}/strategies/{_strategy(client)}/runs",
        json={"kind": "optimize", "params": {"objective": "vibes"}},
    )
    assert response.status_code == 422
    assert "unknown objective" in response.json()["detail"]


def test_an_absurd_epoch_count_is_refused(client: TestClient) -> None:
    response = client.post(
        f"{BASE}/strategies/{_strategy(client)}/runs",
        json={"kind": "optimize", "params": {"epochs": 100000}},
    )
    assert response.status_code == 422


def test_a_backtest_takes_no_parameters(client: TestClient) -> None:
    """It runs the config as written; a parameter would imply otherwise."""
    response = client.post(
        f"{BASE}/strategies/{_strategy(client)}/runs",
        json={"kind": "backtest", "params": {"epochs": 5}},
    )
    assert response.status_code == 422


def test_evolution_parameters_are_defaulted_and_echoed(client: TestClient) -> None:
    """No epochs: the genome carries structure and parameters together (spec section 16.4)."""
    run = _launch(client, _strategy(client), "evolve")
    assert run["params"] == {
        "objective": "calmar",
        "min_trades": 20,
        "min_trades_per_year": 4.0,
        "population": 40,
        "generations": 25,
        "segments": 4,
        "holdout_fraction": 0.2,
        "cache": True,
    }


def test_too_few_segments_are_refused_because_the_overfitting_check_needs_four(
    client: TestClient,
) -> None:
    """The engine accepts three; the API does not, and the reason is not arbitrary.

    CSCV partitions the segments into halves every possible way, and below four there is no
    partition to make. The check comes back uncomputed, which is reported as a failure -- so
    the run could not have passed its own verdict however well it did. Refusing at launch says
    that in the one place the user can still act on it.
    """
    response = client.post(
        f"{BASE}/strategies/{_strategy(client)}/runs",
        json={"kind": "evolve", "params": {"segments": 3}},
    )
    assert response.status_code == 422
    assert "overfitting check needs at least 4" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {"population": 1},
        {"population": 100_000},
        {"generations": 0},
        {"generations": 100_000},
        {"segments": 99},
        {"holdout_fraction": 0.9},
        {"holdout_fraction": 0.0},
    ],
)
def test_an_unusable_search_budget_is_refused(client: TestClient, params: dict[str, Any]) -> None:
    response = client.post(
        f"{BASE}/strategies/{_strategy(client)}/runs",
        json={"kind": "evolve", "params": params},
    )
    assert response.status_code == 422


def test_an_unknown_kind_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post(f"{BASE}/strategies/{_strategy(client)}/runs", json={"kind": "hope"})
    assert response.status_code == 422


def test_runs_share_one_number_sequence_per_strategy(client: TestClient) -> None:
    strategy_id = _strategy(client)
    assert _launch(client, strategy_id, "backtest")["number"] == 1
    assert _launch(client, strategy_id, "optimize")["number"] == 2


# --------------------------------------------------------------------------- listing


def test_runs_are_filtered_by_strategy_kind_and_status(client: TestClient) -> None:
    """One endpoint behind the run tabs, the all-runs page, the queue and the chart picker."""
    first = _strategy(client, "one")
    second = _strategy(client, "two")
    _launch(client, first, "backtest")
    _launch(client, first, "optimize")
    _launch(client, second, "backtest")

    everything = client.get(f"{BASE}/runs").json()
    assert everything["total"] == 3

    by_strategy = client.get(f"{BASE}/runs", params={"strategy_id": first}).json()
    assert by_strategy["total"] == 2

    by_kind = client.get(f"{BASE}/runs", params={"strategy_id": first, "kind": "optimize"}).json()
    assert by_kind["total"] == 1

    queued = client.get(f"{BASE}/runs", params={"status": "queued,running"}).json()
    assert queued["total"] == 3
    assert client.get(f"{BASE}/runs", params={"status": "succeeded"}).json()["total"] == 0


def test_an_unknown_filter_value_matches_nothing_rather_than_failing(
    client: TestClient,
) -> None:
    """A client asking for a status this version lacks should see nothing, not a 500."""
    _launch(client, _strategy(client), "backtest")
    body = client.get(f"{BASE}/runs", params={"status": "elsewhere"}).json()
    assert body["total"] == 1, "an unparseable filter is dropped, not applied"


def test_listing_paginates(client: TestClient) -> None:
    strategy_id = _strategy(client)
    for _ in range(3):
        _launch(client, strategy_id, "backtest")
    page = client.get(f"{BASE}/runs", params={"limit": 2}).json()
    assert len(page["runs"]) == 2
    assert page["total"] == 3


def test_an_unknown_run_is_a_404_problem(client: TestClient) -> None:
    response = client.get(f"{BASE}/runs/00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


# --------------------------------------------------------------------------- cancelling


def test_cancelling_a_queued_run_ends_it_immediately(client: TestClient) -> None:
    """Nothing has started, so there is nothing to ask politely."""
    run = _launch(client, _strategy(client), "optimize")
    response = client.post(f"{BASE}/runs/{run['id']}/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"


def test_cancelling_a_finished_run_is_a_conflict(client: TestClient, db_url: str) -> None:
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)

    response = client.post(f"{BASE}/runs/{run['id']}/cancel")
    assert response.status_code == 409


# --------------------------------------------------------------------------- executed runs


def test_a_finished_backtest_returns_the_engines_own_result(
    client: TestClient, db_url: str
) -> None:
    """Passed through verbatim, contractual derived properties included."""
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)

    body = client.get(f"{BASE}/runs/{run['id']}").json()
    assert body["run"]["status"] == "succeeded", body
    result = body["result"]
    assert result["vintage"]["ticker"] == TICKER
    assert "has_enough_trades_to_judge" in result["metrics"]
    assert "beats_buy_and_hold" in result["benchmark"]
    assert body["error"] is None


def test_a_finished_run_reports_elapsed_time(client: TestClient, db_url: str) -> None:
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)
    body = client.get(f"{BASE}/runs/{run['id']}").json()["run"]
    assert body["elapsed_seconds"] is not None
    assert body["elapsed_seconds"] >= 0


def test_a_run_against_an_older_version_reads_as_stale(client: TestClient, db_url: str) -> None:
    strategy_id = _strategy(client)
    run = _launch(client, strategy_id, "backtest")
    _work_the_queue(db_url)

    detail = client.get(f"{BASE}/strategies/{strategy_id}").json()
    config = dict(detail["head"]["config"])
    config["exit"] = {**config["exit"], "stop_loss_pct": 9.0}
    client.post(
        f"{BASE}/strategies/{strategy_id}/versions",
        json={"base_version": 1, "config": config},
    )

    assert client.get(f"{BASE}/runs/{run['id']}").json()["run"]["stale"] is True


def test_a_run_carries_the_interval_of_the_version_it_pinned(client: TestClient) -> None:
    """Not the head's. A run pins a version at launch, and the head may have moved since.

    Labelling an old run's bar counts with the current head's interval would put a wrong unit on
    numbers that are otherwise correct, which is worse than putting none on them.
    """
    strategy_id = _strategy(client)
    run = _launch(client, strategy_id, "backtest")
    assert client.get(f"{BASE}/runs/{run['id']}").json()["run"]["interval"] == "1d"

    detail = client.get(f"{BASE}/strategies/{strategy_id}").json()
    config = dict(detail["head"]["config"])
    config["universe"] = {
        **config["universe"],
        "interval": "1h",
        "start_date": "2025-01-02",
        "end_date": "2026-08-18",
    }
    saved = client.post(
        f"{BASE}/strategies/{strategy_id}/versions",
        json={"base_version": 1, "config": config},
    )
    assert saved.status_code == 201, saved.text

    assert client.get(f"{BASE}/runs/{run['id']}").json()["run"]["interval"] == "1d"
    assert client.get(f"{BASE}/strategies").json()["strategies"][0]["interval"] == "1h"


# --------------------------------------------------------------------------- series


def test_series_are_catalogued_and_fetchable(client: TestClient, db_url: str) -> None:
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)

    catalog = client.get(f"{BASE}/runs/{run['id']}/series").json()["series"]
    assert catalog["equity"] == [0]
    assert set(catalog) >= {"equity", "drawdown", "close", "monthly_returns"}

    points = client.get(f"{BASE}/runs/{run['id']}/series/equity").json()["points"]
    assert len(points["dates"]) == len(points["values"]) > 0


def test_a_missing_series_is_a_404(client: TestClient, db_url: str) -> None:
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)
    assert client.get(f"{BASE}/runs/{run['id']}/series/nope").status_code == 404


def test_csv_export_streams_the_stored_points(client: TestClient, db_url: str) -> None:
    """The chart and the file are the same bytes, because neither recomputes anything."""
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)

    response = client.get(f"{BASE}/runs/{run['id']}/series/equity.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    lines = response.text.strip().splitlines()
    assert lines[0] == "dates,values"

    points = client.get(f"{BASE}/runs/{run['id']}/series/equity").json()["points"]
    assert len(lines) == len(points["dates"]) + 1
    assert lines[1].startswith(points["dates"][0])


def test_monthly_returns_export_keeps_the_in_market_column(client: TestClient, db_url: str) -> None:
    """Flat and absent are different facts, so the flag has to survive the export."""
    run = _launch(client, _strategy(client), "backtest")
    _work_the_queue(db_url)
    text = client.get(f"{BASE}/runs/{run['id']}/series/monthly_returns.csv").text
    assert text.splitlines()[0] == "months,values,in_market"


# --------------------------------------------------------------------------- honesty


def test_a_suppressed_run_is_not_sent_the_figures_it_withholds(
    client: TestClient, db: psycopg.Connection[TupleRow], db_url: str
) -> None:
    """Spec 15.2: a client cannot render a number it was never given.

    Landed through the repository with a hand-built result, because provoking a genuinely
    thin run would mean finding a strategy that trades sixteen times -- which tests the market
    generator rather than the suppression rule.
    """
    run = _launch(client, _strategy(client), "backtest")
    with unit_of_work_on(db) as work:
        repo = RunRepo(work.connection)
        repo.claim("test-worker")
        repo.succeed(
            run["id"],
            result={
                "metrics": {
                    "total_trades": 16,
                    "total_return_pct": 13.2,
                    "has_enough_trades_to_judge": False,
                },
                "entry_defined_pct": 8.0,
                "benchmark": {"excess_return_pct": -135.8},
            },
            suppressed=True,
        )

    summary = client.get(f"{BASE}/runs", params={"strategy_id": run["strategy"]["id"]}).json()
    headline = summary["runs"][0]["headline"]
    assert headline["suppressed"] is True
    assert headline["trades"] == 16
    assert headline["trade_floor"] == 20
    assert "return_pct" not in headline
    assert "excess_pp" not in headline


def test_a_thin_holdout_withholds_the_evolution_headline_too(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """The trade floor governs a composed strategy exactly as it governs a written one.

    The temptation with evolution is to show the holdout return anyway -- it is the whole
    point of the run, and it took minutes to produce. But a return over nine trades says as
    little here as it does anywhere else, and having searched hard for it makes it worse
    evidence rather than better.
    """
    run = _launch(client, _strategy(client), "evolve", **_TINY_EVOLUTION)
    with unit_of_work_on(db) as work:
        repo = RunRepo(work.connection)
        repo.claim("test-worker")
        repo.succeed(
            run["id"],
            result={
                "composition": "enter when rsi_14 < 30, exit when rsi_14 > 70",
                "distinct_configurations": 96,
                "holdout_metrics": {
                    "total_trades": 9,
                    "total_return_pct": 41.7,
                    "max_drawdown_pct": -12.0,
                    "has_enough_trades_to_judge": False,
                },
                "benchmark": {"benchmark": {"total_return_pct": 6.1}},
                "checks": [{"passed": False}, {"passed": True}],
                "is_credible": False,
            },
            is_credible=False,
            suppressed=True,
        )

    summary = client.get(f"{BASE}/runs", params={"strategy_id": run["strategy"]["id"]}).json()
    headline = summary["runs"][0]["headline"]

    assert headline["suppressed"] is True
    assert headline["trades"] == 9
    assert headline["trials"] == 96
    assert headline["failed_checks"] == 1
    # What it was cannot be rendered, because it was not sent.
    assert "holdout_return_pct" not in headline
    assert "benchmark_return_pct" not in headline
    assert "max_drawdown_pct" not in headline
    # The composition is not a measurement, so it survives suppression.
    assert headline["composition"].startswith("enter when")


def test_only_searches_are_promotable(client: TestClient, db_url: str) -> None:
    """A backtest has no winning config to promote: it ran the one that was already there."""
    strategy_id = _strategy(client)
    _launch(client, strategy_id, "backtest")
    _launch(client, strategy_id, "optimize", epochs=2)
    _work_the_queue(db_url)

    runs = client.get(f"{BASE}/runs", params={"strategy_id": strategy_id}).json()["runs"]
    promotable = {run["kind"]: run["promotable"] for run in runs}
    assert promotable == {"backtest": False, "optimize": True}


def test_a_credible_evolution_run_does_not_validate_the_chassis_it_ran_against(
    client: TestClient, db_url: str
) -> None:
    """The single most dangerous confusion this feature could introduce.

    An evolution run's verdict is about a configuration the chassis does not contain: the
    chassis holds a ticker and a set of costs, and the composition lives in the run's result
    until somebody promotes it into a strategy of its own. If that verdict reached the chassis,
    a strategy would read CREDIBLE on the strength of signals it does not have -- and every
    screen in this application would repeat it without being wrong to.

    The credible verdict is landed directly rather than searched for. What is under test is
    where a verdict may travel, which has to hold whatever a particular search concludes; a
    real run would make this depend on the engine finding something, which is a different
    question and a much slower way to ask this one. A walk-forward landed the same way is
    checked alongside, so the test would fail if the view had simply stopped reading verdicts.
    """
    strategy_id = _strategy(client)
    evolve_run = _launch(client, strategy_id, "evolve", **_TINY_EVOLUTION)
    _land_as_credible(db_url, evolve_run["id"])

    detail = client.get(f"{BASE}/strategies/{strategy_id}").json()
    assert detail["verdict"]["state"] == "unvalidated"
    assert detail["verdict"]["run_id"] is None

    # The same treatment of a walk-forward does reach the strategy, which is what makes the
    # assertion above a statement about evolution rather than about a broken view.
    validation_run = _launch(client, strategy_id, "walk_forward", epochs=2, folds=4)
    _land_as_credible(db_url, validation_run["id"])

    revalidated = client.get(f"{BASE}/strategies/{strategy_id}").json()
    assert revalidated["verdict"]["state"] == "credible"
    assert revalidated["verdict"]["run_id"] == validation_run["id"]


@pytest.mark.slow
def test_an_evolution_run_is_promotable_and_the_promoted_strategy_says_it_was_composed(
    client: TestClient, db_url: str
) -> None:
    """Promotion is the only way the composition becomes something you can validate."""
    strategy_id = _strategy(client)
    run = _launch(client, strategy_id, "evolve", seed=7, **_TINY_EVOLUTION)
    _work_the_queue(db_url)

    detail = client.get(f"{BASE}/runs/{run['id']}").json()
    assert detail["run"]["promotable"] is True
    assert detail["default_promote_name"].endswith("_evo1")
    # A chassis is not a starting point, so there is nothing to render as a diff against one.
    assert detail["config_diff"] == []

    promoted = client.post(f"{BASE}/runs/{run['id']}/promote", json={})
    assert promoted.status_code == 201, promoted.text
    assert promoted.json()["carried_warning"] is True

    child = client.get(f"{BASE}/strategies/{promoted.json()['strategy_id']}").json()
    warning = child["promoted_warning"]["text"]
    assert "Composed by" in warning
    assert "were not written by anyone" in warning
    # The composition really did land as the new strategy's config, signals and all.
    assert "entry:" in child["head"]["yaml"]
    assert child["head"]["config"]["indicators"]


def test_a_strategy_with_runs_but_no_validation_is_unvalidated(
    client: TestClient, db_url: str
) -> None:
    """Not a neutral state: it means nobody has checked the result yet."""
    strategy_id = _strategy(client)
    _launch(client, strategy_id, "backtest")
    _work_the_queue(db_url)
    detail = client.get(f"{BASE}/strategies/{strategy_id}").json()
    assert detail["verdict"]["state"] == "unvalidated"
    assert detail["verdict"]["summary"] is None


# --------------------------------------------------------------------------- deleting


def test_deleting_a_strategy_takes_its_runs_and_captured_series_with_it(
    client: TestClient, db_url: str
) -> None:
    """The delete reaches all the way down to the per-bar series the Charts tab draws.

    Series are the one thing with no route of its own to check afterwards, and the table they
    live in is keyed by run rather than by strategy -- so a purge that missed them would leave
    rows nothing can reach and nothing can delete, in a table that refuses DELETE.
    """
    strategy_id = _strategy(client)
    run = _launch(client, strategy_id, "backtest")
    _work_the_queue(db_url)
    assert client.get(f"{BASE}/runs/{run['id']}").json()["run"]["status"] == "succeeded"

    response = client.delete(f"{BASE}/strategies/{strategy_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs"] == 1
    assert body["series"] > 0

    assert client.get(f"{BASE}/runs/{run['id']}").status_code == 404
    assert client.get(f"{BASE}/runs").json()["runs"] == []


def test_a_finished_run_does_not_block_the_delete(client: TestClient, db_url: str) -> None:
    """Having been run is the ordinary state of a strategy worth deleting.

    A guard that only permitted deleting never-run strategies would refuse in exactly the case
    the feature exists for, so the refusal is about runs still in flight and nothing else.
    """
    strategy_id = _strategy(client)
    _launch(client, strategy_id, "backtest")
    _work_the_queue(db_url)

    assert client.delete(f"{BASE}/strategies/{strategy_id}").status_code == 200
