"""The prospecting HTTP surface.

Two things are worth testing here and the rest is plumbing.

**What the API refuses.** A universe or a search size that cannot work is rejected at the
request rather than at three in the morning, when the sweep reaches the offending ticker and
records a failure that reads like an engine fault. Each refusal below corresponds to a way a
sweep would otherwise waste a night.

**What the wire shape keeps apart.** Section 19.9 requires three kinds of number to stay
distinguishable: what the search selected on, what transfer rejected on, and what forward
evidence has accrued. A schema that flattened them into peers would let a reader rank on the
first, which is exactly what section 19.5 forbids.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from cracktrade.api.repos import ProspectRepo
from cracktrade.prospect import DEFAULT_UNIVERSE

pytestmark = pytest.mark.db

BASE = "/api/v1/prospect"


def _start(client: TestClient, **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "overnight", "universe": ["AMD", "NVDA"], **body}
    response = client.post(f"{BASE}/sessions", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _candidate(
    db: psycopg.Connection[TupleRow],
    session_id: UUID,
    *,
    ticker: str = "AMD",
    survived: bool = True,
    median: float = 0.4,
) -> UUID:
    """A candidate shaped as the worker stores one -- ``to_dict`` of a real ``Candidate``."""
    row = ProspectRepo(db).add_candidate(
        session_id=session_id,
        ticker=ticker,
        discovered_at=datetime.now(UTC),
        last_bar_seen=date(2026, 8, 19),
        seed=11,
        candidate={
            "ticker": ticker,
            "composition": "rsi oversold, then close above sma",
            "blocks": ["rsi_oversold", "close_above_sma"],
            "holdout_return_pct": 18.5,
            "holdout_sharpe": 1.4,
            "holdout_trades": 31,
            "median_segment_return_pct": 6.2,
            "distinct_configurations": 240,
            "survives_transfer": survived,
            "transfer": {
                "home": ticker,
                "family": "SEMICONDUCTORS",
                "home_sharpe": 1.4,
                "results": [
                    {"ticker": "MU", "is_control": False, "sharpe": median, "total_trades": 20},
                    {"ticker": "GLD", "is_control": True, "sharpe": -1.2, "total_trades": 9},
                ],
                "failures": [],
                "median_sibling_sharpe": median,
                "median_control_sharpe": -1.2,
                "negative_members": 0,
                "beats_controls": True,
                "survives": survived,
            },
        },
        strategy_yaml="strategy:\n  name: found_amd\n",
        survived_transfer=survived,
        transfer_median=median,
        transfer_control=-1.2,
    )
    db.commit()
    return row.id


# --------------------------------------------------------------------------- starting


def test_a_sweep_starts_running_with_the_defaults_that_were_measured(
    client: TestClient,
) -> None:
    """A user with no opinion gets the twenty instruments section 19.6 screened and the small
    search section 19.1 measured, not an empty form."""
    body = client.post(f"{BASE}/sessions", json={"name": "overnight"})
    assert body.status_code == 201, body.text
    session = body.json()

    assert session["status"] == "running"
    assert session["universe"] == list(DEFAULT_UNIVERSE)
    assert session["params"]["population"] * session["params"]["generations"] <= 400
    assert session["current_ticker"] == DEFAULT_UNIVERSE[0]
    assert (session["candidates"], session["survivors"]) == (0, 0)


def test_a_seed_is_recorded_even_when_none_was_asked_for(client: TestClient) -> None:
    """A candidate nobody can reproduce is a story rather than a result (defect D12)."""
    assert isinstance(_start(client)["seed"], int)
    assert _start(client, name="pinned", seed=7)["seed"] == 7


def test_a_ticker_with_no_family_is_refused_at_the_request(client: TestClient) -> None:
    """Not at three in the morning when the sweep reaches it and logs a failed tick."""
    response = client.post(
        f"{BASE}/sessions", json={"name": "bad", "universe": ["AMD", "WIDGETCO"]}
    )
    assert response.status_code == 422
    assert "WIDGETCO" in response.json()["detail"]
    assert "transfer-tested" in response.json()["detail"]


def test_inverse_products_are_refused_with_the_reason(client: TestClient) -> None:
    """Section 19.6's Goodhart trap: they decay structurally, so merely not holding one beats
    the benchmark for a reason that has nothing to do with the signal."""
    response = client.post(f"{BASE}/sessions", json={"name": "bad", "universe": ["SOXS"]})
    assert response.status_code == 422
    assert "SOXS" in response.json()["detail"]


def test_an_oversized_sweep_search_is_refused(client: TestClient) -> None:
    """Section 19.1 measured a forty-fold budget buying nothing, while the deflation threshold
    rises with the trial count. A sweep runs this search thousands of times."""
    response = client.post(
        f"{BASE}/sessions",
        json={"name": "greedy", "params": {"population": 400, "generations": 50}},
    )
    assert response.status_code == 422
    assert "19.1" in response.json()["detail"] or "raises its own" in response.json()["detail"]


def test_duplicate_tickers_are_collapsed_rather_than_swept_twice(client: TestClient) -> None:
    session = _start(client, universe=["amd", "AMD", " nvda "])
    assert session["universe"] == ["AMD", "NVDA"]


def test_a_duplicate_session_name_is_a_conflict(client: TestClient) -> None:
    _start(client)
    assert client.post(f"{BASE}/sessions", json={"name": "overnight"}).status_code == 409


def test_an_unknown_interval_is_refused(client: TestClient) -> None:
    response = client.post(f"{BASE}/sessions", json={"name": "bad", "params": {"interval": "1w"}})
    assert response.status_code == 422


# --------------------------------------------------------------------------- reading


def test_a_session_reports_progress_as_counts_never_as_a_percentage(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """Section 19.9: a search with no end has no percentage. What there is to say is how many
    tickers were covered and how many candidates survived."""
    session = _start(client)
    session_id = UUID(session["id"])
    _candidate(db, session_id, survived=True)
    _candidate(db, session_id, ticker="NVDA", survived=False)

    body = client.get(f"{BASE}/sessions/{session['id']}").json()

    assert "progress" not in body
    assert "percent" not in body
    assert (body["candidates"], body["survivors"]) == (2, 1)
    assert body["current_ticker"] == "AMD"


def test_the_leaderboard_keeps_the_three_kinds_of_number_apart(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """The holdout figures are nested under ``selected_on`` rather than sitting beside the
    forward ones as peers. A reader scanning a row believes the biggest number, and these are
    the ones most likely to be large and least likely to mean anything."""
    session = _start(client)
    _candidate(db, UUID(session["id"]))

    row = client.get(f"{BASE}/candidates", params={"session_id": session["id"]}).json()[
        "candidates"
    ][0]

    assert row["selected_on"]["holdout_return_pct"] == 18.5
    assert row["selected_on"]["distinct_configurations"] == 240
    assert row["transfer"]["median_sibling_sharpe"] == 0.4
    assert row["transfer"]["members"] == 1
    assert row["transfer"]["survives"] is True
    # Not measured yet -- and null, never zero. A client must render that as "not yet".
    assert row["forward"] is None
    assert row["forward_scores"] == 0
    # The holdout figure is nowhere a client could sort on it.
    assert "holdout_return_pct" not in row


def test_the_leaderboard_shows_survivors_by_default(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    session = _start(client)
    _candidate(db, UUID(session["id"]), survived=True)
    _candidate(db, UUID(session["id"]), ticker="NVDA", survived=False, median=-0.3)

    survivors = client.get(f"{BASE}/candidates", params={"session_id": session["id"]}).json()
    assert survivors["total"] == 1

    everything = client.get(
        f"{BASE}/candidates", params={"session_id": session["id"], "survivors_only": False}
    ).json()
    assert everything["total"] == 2


def test_the_leaderboard_takes_no_sort_parameter(client: TestClient) -> None:
    """The ordering is section 19.5's, not a client's to choose."""
    from cracktrade.api.app import create_app
    from cracktrade.api.settings import ApiSettings

    spec = create_app(ApiSettings()).openapi()
    parameters = spec["paths"]["/api/v1/prospect/candidates"]["get"]["parameters"]
    assert "sort" not in {parameter["name"] for parameter in parameters}
    assert "order" not in {parameter["name"] for parameter in parameters}


def test_a_candidate_detail_carries_its_whole_forward_series(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """Only the sequence tells a candidate that has decayed for six weeks from one found last
    Tuesday with the same latest figure."""
    session = _start(client)
    candidate_id = _candidate(db, UUID(session["id"]))
    repo = ProspectRepo(db)
    for last, value in ((date(2026, 8, 27), 4.0), (date(2026, 9, 3), 1.0)):
        repo.append_forward_score(
            candidate_id=candidate_id,
            first_bar=date(2026, 8, 20),
            last_bar=last,
            bars=40,
            return_pct=value,
            sharpe=value / 4,
            trades=5,
        )
    db.commit()

    body = client.get(f"{BASE}/candidates/{candidate_id}").json()

    assert [score["return_pct"] for score in body["forward_history"]] == [4.0, 1.0]
    assert body["forward"]["return_pct"] == 1.0
    assert body["strategy_yaml"].startswith("strategy:")


def test_a_missing_candidate_is_a_404(client: TestClient) -> None:
    assert client.get(f"{BASE}/candidates/{uuid4()}").status_code == 404


def test_a_missing_session_is_a_404(client: TestClient) -> None:
    assert client.get(f"{BASE}/sessions/{uuid4()}").status_code == 404


# --------------------------------------------------------------------------- stopping


def test_stopping_a_sweep_nobody_is_working_ends_it_immediately(client: TestClient) -> None:
    """Otherwise a user pressing stop with the worker down watches a state that never
    resolves."""
    session = _start(client)

    body = client.post(f"{BASE}/sessions/{session['id']}/stop").json()

    assert body["status"] == "stopped"
    assert body["stopped_at"] is not None


def test_stopping_a_sweep_a_worker_holds_asks_rather_than_kills(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """A sweep never stops mid-search: that search's compute would be paid for and its result
    thrown away. The worker lands the terminal row between ticks."""
    session = _start(client)
    ProspectRepo(db).claim_session("a-live-worker", lease_seconds=3600.0)
    db.commit()

    body = client.post(f"{BASE}/sessions/{session['id']}/stop").json()

    assert body["status"] == "running"
    assert body["stop_requested"] is True
    assert body["stopped_at"] is None


def test_stopping_a_finished_sweep_is_a_conflict(client: TestClient) -> None:
    session = _start(client)
    client.post(f"{BASE}/sessions/{session['id']}/stop")

    assert client.post(f"{BASE}/sessions/{session['id']}/stop").status_code == 409


def test_a_stopped_sweep_can_be_resumed_where_it_left_off(
    client: TestClient, db: psycopg.Connection[TupleRow]
) -> None:
    """A sweep's compute can be rebought in minutes; its forward scores accrue over days and
    cannot be. Resume keeps the ledger rather than starting a replacement."""
    session = _start(client)
    # Three positions over a two-ticker universe: one full pass, then one into the next.
    ProspectRepo(db).reserve_ordinals(UUID(session["id"]), count=3)
    db.commit()
    client.post(f"{BASE}/sessions/{session['id']}/stop")

    body = client.post(f"{BASE}/sessions/{session['id']}/resume").json()

    assert body["status"] == "running"
    assert body["stopped_at"] is None
    assert body["stop_requested"] is False
    assert body["cursor_index"] == 1
    assert body["passes_completed"] == 1


def test_resuming_a_running_sweep_is_a_conflict(client: TestClient) -> None:
    session = _start(client)

    assert client.post(f"{BASE}/sessions/{session['id']}/resume").status_code == 409


def test_resuming_a_sweep_that_does_not_exist_is_not_found(client: TestClient) -> None:
    assert client.post(f"{BASE}/sessions/{uuid4()}/resume").status_code == 404


def test_there_is_no_route_that_edits_a_running_sweep(client: TestClient) -> None:
    """A sweep whose question changed partway would make its own candidate list incomparable
    with itself, and the ranking would read the difference between the questions."""
    from cracktrade.api.app import create_app
    from cracktrade.api.settings import ApiSettings

    spec = create_app(ApiSettings()).openapi()
    prospecting = {path: set(spec["paths"][path]) for path in spec["paths"] if "prospect" in path}
    assert not any({"put", "patch", "delete"} & methods for methods in prospecting.values())


def test_there_is_no_route_that_marks_a_candidate_credible(client: TestClient) -> None:
    """Section 19.2. Credibility is a property of a strategy that has had its own
    walk-forward, and the path to one is the existing promote flow."""
    from cracktrade.api.app import create_app
    from cracktrade.api.settings import ApiSettings

    spec = create_app(ApiSettings()).openapi()
    assert not any(
        "credible" in path or "promote" in path for path in spec["paths"] if "prospect" in path
    )
