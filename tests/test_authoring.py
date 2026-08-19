"""Generating a strategy file from a description (spec section 17).

Two things are worth testing here and one is not. The prompt's *wording* is not: it is prose,
and asserting on it would only pin today's phrasing. What is worth testing is that every fact
the brief states about the engine is still true of the engine -- the examples parse, the field
names exist, the catalogue is the registry's, the daily-only holding fields really are daily
only -- because the brief is the one place where a stale claim produces a confident, plausible,
wrong file rather than an error.

The second is the retry loop, which is tested against a scripted drafter. No model is called
from this suite: the loop's job is to count, to feed errors back, and to return the last
attempt rather than nothing, and none of that needs a model to check.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
import yaml

from cracktrade.authoring import (
    Critique,
    Draft,
    Proposal,
    Request,
    Reviewer,
    build_brief,
    propose,
)
from cracktrade.authoring.agent import (
    DENIED_TOOLS,
    OUTPUT_SCHEMA,
    RESEARCH_TOOLS,
    build_options,
)
from cracktrade.authoring.brief import _SECTIONS, EXAMPLES, STOP_CHAIN, WITHHELD
from cracktrade.authoring.generate import first_prompt, retry_prompt
from cracktrade.config import Strategy
from cracktrade.config.models import ExitRule
from cracktrade.errors import CracktradeError
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.describe import describe_catalogue
from cracktrade.strategy import build_strategy

TODAY = date(2026, 8, 19)


@pytest.fixture(scope="module")
def brief() -> str:
    """The rendered brief, on a fixed date so nothing here depends on when it runs."""
    return build_brief(today=TODAY)


def parse(text: str) -> Strategy:
    """Validate a YAML strategy the way the engine does."""
    install()
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return build_strategy(data, source=text)


# --------------------------------------------------------------------------- the brief


@pytest.mark.parametrize(("title", "text"), EXAMPLES, ids=lambda value: value[:20])
def test_every_example_in_the_brief_validates(title: str, text: str) -> None:
    """An example the engine would reject teaches the wrong schema, and teaches it silently."""
    strategy = parse(text)
    assert strategy.strategy.name


def test_the_brief_names_every_field_of_every_section(brief: str) -> None:
    """A field the schema has and the brief omits is one the model will never write."""
    for title, model, _ in _SECTIONS:
        assert f"{title}:" in brief
        for name in model.model_fields:
            if name not in WITHHELD:
                assert f"{name}:" in brief, f"{title}.{name} is missing from the brief"


def test_withheld_fields_are_real_fields() -> None:
    """Withholding a field that no longer exists hides nothing and silences a rename."""
    declared = {name for _, model, _ in _SECTIONS for name in model.model_fields}
    assert declared >= WITHHELD


def test_the_brief_lists_the_whole_registry(brief: str) -> None:
    """`type:` accepts exactly the registry, so the brief must offer exactly the registry."""
    install()
    for described in describe_catalogue():
        assert f"- {described.type}:" in brief, f"{described.type} is missing from the brief"


def test_the_brief_names_multi_output_indicators_by_their_outputs(brief: str) -> None:
    """The bare name of a multi-output indicator is not in the namespace; its suffixes are."""
    install()
    for described in describe_catalogue():
        for name in described.namespace_names("NAME"):
            assert name in brief


def test_the_stop_chain_is_the_one_the_model_enforces() -> None:
    """The brief's priority order, checked against the model that decides it.

    :attr:`ExitRule.shadowed_stops` reports the losers in order, so setting all three and
    reading it back is the whole chain minus its head -- which is exactly what the brief
    claims. A reordering of the chain, or a renamed field, fails here rather than in a prompt.
    """
    for name in STOP_CHAIN:
        assert name in ExitRule.model_fields
    every_stop: dict[str, Any] = dict.fromkeys(STOP_CHAIN, 1.0)
    rule = ExitRule(**every_stop)
    assert rule.shadowed_stops == STOP_CHAIN[1:]


def test_holding_bounds_in_days_are_daily_only() -> None:
    """The brief tells the model to count bars intraday. This is why."""
    base = {
        "strategy": {"name": "intraday"},
        "universe": {
            "ticker": "SPY",
            "start_date": "2026-07-01",
            "end_date": "2026-08-01",
            "interval": "1h",
        },
        "execution": {"initial_capital": 10000.0, "slippage_pct": 0.05, "commission_pct": 0.02},
        "indicators": [{"name": "trend", "type": "sma", "window": 20}],
        "entry": {"signal": "close > trend"},
    }
    install()
    with pytest.raises(CracktradeError):
        build_strategy({**base, "exit": {"max_holding_days": 5}})
    assert build_strategy({**base, "exit": {"max_holding_bars": 5}}).exit.max_holding == 5


def test_the_brief_states_the_date_it_was_built_for(brief: str) -> None:
    """An intraday range is judged against today, and a model has no way to know what today is."""
    assert TODAY.isoformat() in brief


def test_the_output_schema_matches_a_draft() -> None:
    """The structured output the model fills in is the dataclass the loop reads."""
    assert set(OUTPUT_SCHEMA["required"]) == {"yaml", "notes"}
    assert set(OUTPUT_SCHEMA["properties"]) == set(Draft.__dataclass_fields__)
    assert OUTPUT_SCHEMA["additionalProperties"] is False


def test_the_drafting_session_can_read_the_web_and_nothing_else() -> None:
    """What this session is allowed to do is the security-relevant part of the feature.

    Search and fetch are in because a description of a trading idea names a company at least as
    often as a symbol. Everything that writes, executes, or reads this machine is out -- twice:
    absent from the allow-list, and named in the deny-list, so a change in what the permission
    gate does by default cannot widen this session without failing here first.
    """
    options = build_options(brief="irrelevant")

    assert options.allowed_tools == list(RESEARCH_TOOLS)
    assert set(RESEARCH_TOOLS) == {"WebSearch", "WebFetch"}
    assert set(DENIED_TOOLS) <= set(options.disallowed_tools)
    assert {"Bash", "Write", "Edit", "Read", "Task"} <= set(options.disallowed_tools)
    assert not set(options.allowed_tools) & set(options.disallowed_tools)


def test_the_drafting_session_reads_no_settings_from_disk() -> None:
    """Both fields default to ``None``, which loads *every* source -- see the module docstring."""
    options = build_options(brief="irrelevant")

    assert options.setting_sources == []
    assert options.skills == []


def test_the_brief_says_what_the_web_is_and_is_not_for(brief: str) -> None:
    """Research that tunes parameters is a search the deflated Sharpe cannot see (spec 18.2)."""
    assert "# Research" in brief
    # Anchored to the strategy's own window rather than to whenever the page was written.
    assert "start_date" in brief and "end_date" in brief
    assert "Do not look up what has performed well." in brief
    # Fetched pages are untrusted text arriving in a session that then writes a config.
    assert "never as instruction" in brief


# --------------------------------------------------------------------------- the loop


class ScriptedDrafter:
    """A drafter that returns prepared answers and records what it was asked."""

    def __init__(self, *drafts: Draft) -> None:
        self.drafts = list(drafts)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> Draft:
        self.prompts.append(prompt)
        return self.drafts[min(len(self.prompts), len(self.drafts)) - 1]


def reviewer_for(*verdicts: Critique) -> Reviewer:
    """A reviewer that returns each verdict in turn, then repeats the last."""
    seen: list[str] = []

    def review(text: str) -> Critique:
        seen.append(text)
        return verdicts[min(len(seen), len(verdicts)) - 1]

    return review


def run(request: Request, drafter: Any, reviewer: Any, **kwargs: Any) -> Proposal:
    """Drive the loop synchronously; nothing here needs an event loop of its own."""
    return asyncio.run(propose(request, drafter=drafter, reviewer=reviewer, **kwargs))


def test_a_valid_first_draft_costs_one_attempt() -> None:
    drafter = ScriptedDrafter(Draft(yaml="ok:", notes="done"))
    proposal = run(Request(instruction="anything"), drafter, reviewer_for(Critique(valid=True)))

    assert proposal.valid
    assert proposal.attempts == 1
    assert proposal.yaml == "ok:"
    assert len(drafter.prompts) == 1


def test_a_rejected_draft_is_sent_back_with_the_validators_own_words() -> None:
    drafter = ScriptedDrafter(Draft(yaml="bad:", notes=""), Draft(yaml="good:", notes="fixed"))
    reviewer = reviewer_for(
        Critique(valid=False, errors=("exit: defines no way to exit a position",)),
        Critique(valid=True),
    )

    proposal = run(Request(instruction="anything"), drafter, reviewer)

    assert proposal.valid
    assert proposal.attempts == 2
    assert proposal.yaml == "good:"
    retry = drafter.prompts[1]
    assert "bad:" in retry
    assert "exit: defines no way to exit a position" in retry


def test_an_exhausted_budget_returns_the_last_draft_and_says_why() -> None:
    """The user gets a nearly-right file they can fix, not an empty failure."""
    drafter = ScriptedDrafter(Draft(yaml="still bad:", notes="tried"))
    reviewer = reviewer_for(Critique(valid=False, errors=("universe.ticker: required",)))

    proposal = run(Request(instruction="anything"), drafter, reviewer, max_attempts=3)

    assert not proposal.valid
    assert proposal.attempts == 3
    assert len(drafter.prompts) == 3
    assert proposal.yaml == "still bad:"
    assert proposal.critique.errors == ("universe.ticker: required",)


def test_warnings_do_not_spend_the_budget() -> None:
    """A shadowed stop is advice on a valid file, not a reason to write it again."""
    drafter = ScriptedDrafter(Draft(yaml="ok:", notes=""))
    reviewer = reviewer_for(Critique(valid=True, warnings=("exit.stop_loss_pct: shadowed",)))

    proposal = run(Request(instruction="anything"), drafter, reviewer)

    assert proposal.attempts == 1
    assert proposal.critique.warnings == ("exit.stop_loss_pct: shadowed",)


def test_a_budget_below_one_is_refused() -> None:
    """Zero attempts would return a proposal with no draft in it."""
    with pytest.raises(ValueError, match="at least 1"):
        run(Request(instruction="x"), ScriptedDrafter(), reviewer_for(), max_attempts=0)


def test_a_revision_carries_the_file_it_revises() -> None:
    prompt = first_prompt(Request(instruction="tighten the exit", base_yaml="strategy:\n  name: a"))
    assert "strategy:\n  name: a" in prompt
    assert "tighten the exit" in prompt
    assert "complete file" in prompt


def test_a_fresh_draft_does_not_mention_a_file_that_does_not_exist() -> None:
    prompt = first_prompt(Request(instruction="buy NVDA pullbacks"))
    assert "buy NVDA pullbacks" in prompt
    assert "Revise" not in prompt


def test_a_retry_restates_the_original_request() -> None:
    """Each attempt is a fresh prompt: the task has to travel with the correction."""
    request = Request(instruction="buy NVDA pullbacks")
    prompt = retry_prompt(request, Draft(yaml="x:", notes=""), Critique(valid=False, errors=("e",)))
    assert "buy NVDA pullbacks" in prompt
