"""``POST /config/generate``: drafting a configuration, and refusing to invent one.

No model is called here. The drafter is a dependency precisely so these tests can replace it
with a scripted answer, which leaves the things actually worth checking: that a valid draft
comes back reviewed the way the editor reviews it, that an invalid one comes back *anyway*
rather than as an error, that a failure to reach the model is a 503 carrying what the tool
said, and that nothing was stored either way.

The app is built without a database on purpose. This route touches none, and a generation test
that needed PostgreSQL running would be skipped on the machines most likely to be trying the
feature out.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from cracktrade.api.app import create_app
from cracktrade.api.routes.authoring import drafter_of
from cracktrade.api.settings import ApiSettings
from cracktrade.authoring import Draft
from cracktrade.errors import AuthoringUnavailableError

VALID = """strategy:
  name: generated
universe:
  ticker: NVDA
  start_date: "2015-01-01"
  end_date: "2024-12-31"
execution:
  initial_capital: 10000.0
  slippage_pct: 0.1
  commission_pct: 0.05
indicators:
  - name: trend
    type: sma
    window: 200
entry:
  signal: "close > trend"
exit:
  signal: "close < trend"
  trailing_stop_pct: 8.0
"""

#: Valid, and warned about: two stops are set and only the higher-priority one is active.
SHADOWED = VALID.replace(
    "  trailing_stop_pct: 8.0\n",
    "  trailing_stop_pct: 8.0\n  stop_loss_pct: 4.0\n",
)

INVALID = """strategy:
  name: generated
universe:
  ticker: NVDA
  start_date: "2015-01-01"
entry:
  signal: "close > nothing_declared"
exit:
  signal: "close < nothing_declared"
"""


class Scripted:
    """A drafter returning prepared drafts, or raising on the way to one."""

    def __init__(self, *drafts: Draft, failure: Exception | None = None) -> None:
        self.drafts = list(drafts)
        self.failure = failure
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> Draft:
        self.prompts.append(prompt)
        if self.failure is not None:
            raise self.failure
        return self.drafts[min(len(self.prompts), len(self.drafts)) - 1]


@contextmanager
def client_for(drafter: Scripted, **settings: object) -> Iterator[TestClient]:
    """The app with its drafter replaced, and without a database it does not use."""
    application = create_app(ApiSettings(**settings))  # type: ignore[arg-type]
    application.dependency_overrides[drafter_of] = lambda: drafter
    with TestClient(application, raise_server_exceptions=False) as running:
        yield running


@pytest.fixture
def valid_client() -> Iterator[TestClient]:
    draft = Draft(yaml=VALID, notes="A trend filter and a trailing stop.")
    with client_for(Scripted(draft)) as running:
        yield running


def test_a_valid_draft_comes_back_reviewed(valid_client: TestClient) -> None:
    response = valid_client.post("/api/v1/config/generate", json={"instruction": "trend follow"})

    assert response.status_code == 200
    body = response.json()
    assert body["attempts"] == 1
    assert body["notes"] == "A trend filter and a trailing stop."
    assert body["review"]["valid"] is True
    # The same payload `/config/validate` returns, so the client can open the editor on it
    # without asking a second time -- and cannot be told two different things about one file.
    assert "close" in body["review"]["namespace"]
    assert body["review"]["canonical_yaml"] is not None


def test_generation_creates_nothing(valid_client: TestClient) -> None:
    """The whole point. A draft is text until a person adopts it."""
    before = valid_client.post("/api/v1/config/generate", json={"instruction": "trend follow"})
    assert before.status_code == 200
    # No strategy id, no version, nothing addressable was returned -- there is nothing to
    # adopt except by sending the YAML back through import or save-version.
    assert set(before.json()) == {"yaml", "notes", "attempts", "review"}


def test_a_draft_that_never_validates_is_still_returned() -> None:
    """A nearly-right file the user can fix beats an error page that shows them nothing."""
    drafter = Scripted(Draft(yaml=INVALID, notes="could not finish"))
    with client_for(drafter, generate_max_attempts=2) as client:
        response = client.post("/api/v1/config/generate", json={"instruction": "something"})

        assert response.status_code == 200
        body = response.json()
        assert body["attempts"] == 2
        assert body["review"]["valid"] is False
        assert body["review"]["errors"]
        assert body["yaml"] == INVALID
        # The second prompt carried the first draft's errors back, in the validator's words.
        assert "nothing_declared" in drafter.prompts[1]


def test_a_retry_stops_as_soon_as_the_engine_accepts_one() -> None:
    drafter = Scripted(Draft(yaml=INVALID, notes=""), Draft(yaml=VALID, notes="fixed"))
    with client_for(drafter, generate_max_attempts=4) as client:
        response = client.post("/api/v1/config/generate", json={"instruction": "something"})

        assert response.json()["attempts"] == 2
        assert len(drafter.prompts) == 2


def test_warnings_ride_along_on_a_valid_draft() -> None:
    """A shadowed stop does not block a save, so it must not block a proposal either."""
    drafter = Scripted(Draft(yaml=SHADOWED, notes=""))
    with client_for(drafter) as client:
        body = client.post("/api/v1/config/generate", json={"instruction": "x"}).json()

        assert body["review"]["valid"] is True
        assert body["attempts"] == 1
        assert any("stop_loss_pct" in issue["path"] for issue in body["review"]["warnings"])


def test_an_unreachable_model_is_a_503_carrying_what_it_said() -> None:
    """ "Generation failed" is unactionable; the CLI's own words tell the user what to do."""
    failure = AuthoringUnavailableError("OAuth session expired and could not be refreshed")
    with client_for(Scripted(failure=failure)) as client:
        response = client.post("/api/v1/config/generate", json={"instruction": "x"})

        assert response.status_code == 503
        body = response.json()
        assert body["type"].endswith("upstream-unavailable")
        assert "OAuth session expired" in body["detail"]


def test_generation_can_be_switched_off() -> None:
    with client_for(Scripted(), generate_enabled=False) as client:
        response = client.post("/api/v1/config/generate", json={"instruction": "x"})

        assert response.status_code == 503
        assert "switched off" in response.json()["detail"]


def test_a_revision_carries_the_file_it_revises() -> None:
    drafter = Scripted(Draft(yaml=VALID, notes="tightened"))
    with client_for(drafter) as client:
        response = client.post(
            "/api/v1/config/generate",
            json={"instruction": "use an ATR stop", "base_yaml": VALID},
        )

        assert response.status_code == 200
        assert "use an ATR stop" in drafter.prompts[0]
        assert VALID.strip() in drafter.prompts[0]


def test_an_empty_instruction_is_a_bad_request() -> None:
    with client_for(Scripted()) as client:
        assert client.post("/api/v1/config/generate", json={"instruction": ""}).status_code == 422
