"""Continuous prospecting -- spec section 19.

Two things carry this package and each has tests here.

* **Every ticker the sweep can reach has a family.** Transfer is the cheapest rejection the
  ladder has, and a candidate that skipped it would be ranked against candidates that did not.
  A universe entry with no family is therefore a configuration error, asserted rather than
  discovered when the sweep reaches that ticker at three in the morning.
* **Transfer rejects for the right reasons.** The rule is two rejections, not an endorsement:
  the candidate must make money on its relatives *and* do so by more than on instruments
  sharing none of their mechanism. The second half is what separates "this works across
  semiconductors" from "this is long the market", and it is the half most easily lost.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime

import pytest

from cracktrade.config import Strategy
from cracktrade.control import RunControl
from cracktrade.data import MarketData
from cracktrade.errors import DataUnavailableError, ProspectError, RunCancelled
from cracktrade.evolution import Chassis, GaSettings, random_genome, render
from cracktrade.prospect import (
    DEFAULT_UNIVERSE,
    FAMILIES,
    INVERSE_BUCKET,
    PROSPECT_SETTINGS,
    Candidate,
    Family,
    Rotation,
    SiblingResult,
    TransferReport,
    family_of,
    is_inverse,
    prospect_once,
    retarget,
    transfer_report,
)
from tests.factories import make_ohlcv

BARS = 900


def a_market(ticker: str, *, seed: int = 0, trend: float = 0.05) -> MarketData:
    return MarketData(
        ticker=ticker,
        frame=make_ohlcv(bars=BARS, start="2016-01-01", trend=trend, seed=seed),
        requested_start=date(2016, 1, 1),
        requested_end=date(2020, 1, 1),
    )


def a_chassis(ticker: str = "AMD") -> Chassis:
    return Chassis(
        name="prospect_test",
        ticker=ticker,
        start_date=date(2016, 1, 1),
        end_date=date(2020, 1, 1),
    )


def a_strategy(ticker: str = "AMD", *, seed: int = 7) -> Strategy:
    """An evolved strategy, which is what a candidate actually is by the time it reaches here."""
    return render(random_genome(random.Random(seed)), a_chassis(ticker))


def a_sibling(ticker: str, sharpe: float, *, is_control: bool = False) -> SiblingResult:
    return SiblingResult(
        ticker=ticker,
        is_control=is_control,
        sharpe=sharpe,
        total_return_pct=sharpe * 10.0,
        total_trades=40,
    )


def a_report(members: list[float], controls: list[float]) -> TransferReport:
    results = [a_sibling(f"M{i}", s) for i, s in enumerate(members)]
    results += [a_sibling(f"C{i}", s, is_control=True) for i, s in enumerate(controls)]
    return TransferReport(
        home="AMD", family="semiconductors", home_sharpe=1.30, results=tuple(results)
    )


# --------------------------------------------------------------------------- the universe


def test_every_universe_ticker_has_a_family() -> None:
    """A universe entry with no family cannot be transfer-tested, so it must not exist."""
    unmapped = [t for t in DEFAULT_UNIVERSE if _family_or_none(t) is None]
    assert unmapped == []


def test_the_universe_is_twenty_distinct_tickers() -> None:
    assert len(DEFAULT_UNIVERSE) == 20
    assert len(set(DEFAULT_UNIVERSE)) == 20


def test_the_inverse_bucket_is_disjoint_from_the_universe() -> None:
    """Section 19.5: decaying instruments are ranked separately, never alongside."""
    assert not set(INVERSE_BUCKET) & set(DEFAULT_UNIVERSE)
    assert all(is_inverse(t) for t in INVERSE_BUCKET)
    assert not any(is_inverse(t) for t in DEFAULT_UNIVERSE)


def test_every_family_has_members_and_controls() -> None:
    """A family without controls cannot tell a real effect from market beta."""
    for family in FAMILIES:
        assert len(family.members) >= 2, family.name
        assert family.controls, family.name
        assert not set(family.members) & set(family.controls), family.name


def test_family_of_refuses_an_unmapped_ticker() -> None:
    with pytest.raises(ProspectError, match="belongs to no family"):
        family_of("NOT_A_TICKER")


def test_siblings_exclude_the_home_ticker() -> None:
    family = family_of("AMD")
    assert "AMD" not in family.siblings_of("AMD")
    assert "MU" in family.siblings_of("AMD")


# --------------------------------------------------------------------------- the rejection rule


def test_a_candidate_that_travels_survives() -> None:
    report = a_report(members=[0.6, 0.4, 0.8], controls=[-0.1, 0.0])
    assert report.median_sibling_sharpe == pytest.approx(0.6)
    assert report.beats_controls
    assert report.survives


def test_a_candidate_that_loses_on_its_relatives_is_rejected() -> None:
    """The measured AMD case: excellent at home, negative across the family."""
    report = a_report(members=[0.18, -0.08, -0.15, -0.64, -0.95], controls=[-0.5, -1.1])
    assert report.median_sibling_sharpe < 0
    assert report.negative_members == 4
    assert not report.survives


def test_a_candidate_matching_its_controls_is_rejected_as_beta() -> None:
    """Doing as well on gold as on semiconductors describes the market, not the family."""
    report = a_report(members=[0.5, 0.5], controls=[0.5, 0.6])
    assert report.median_sibling_sharpe > 0
    assert not report.beats_controls
    assert not report.survives


def test_a_report_with_no_members_never_survives() -> None:
    """Absent evidence is not evidence, so an empty family is a rejection and not a pass."""
    report = a_report(members=[], controls=[0.9])
    assert report.median_sibling_sharpe == 0.0
    assert not report.survives


def test_controls_are_never_counted_among_members() -> None:
    report = a_report(members=[1.0], controls=[-1.0])
    assert [r.ticker for r in report.members] == ["M0"]
    assert [r.ticker for r in report.controls] == ["C0"]
    assert report.median_sibling_sharpe == pytest.approx(1.0)
    assert report.median_control_sharpe == pytest.approx(-1.0)


# --------------------------------------------------------------------------- running it


def test_transfer_runs_the_same_strategy_across_the_family() -> None:
    strategy = a_strategy("AMD")
    family = Family(name="test", members=("AMD", "MU", "INTC"), controls=("GLD",))
    seen: list[str] = []

    def data_for(ticker: str) -> MarketData:
        seen.append(ticker)
        return a_market(ticker, seed=len(ticker))

    report = transfer_report(strategy, family, data_for)

    # Home, both siblings and the control -- and the home ticker never appears as a sibling.
    assert set(seen) == {"AMD", "MU", "INTC", "GLD"}
    assert {r.ticker for r in report.members} == {"MU", "INTC"}
    assert {r.ticker for r in report.controls} == {"GLD"}
    assert report.home == "AMD"
    assert report.failures == ()


def test_an_unavailable_sibling_is_recorded_not_raised() -> None:
    """A relative with no history is a fact about the data, not a reason to abort the sweep."""
    strategy = a_strategy("AMD")
    family = Family(name="test", members=("AMD", "MU"), controls=("GLD",))

    def data_for(ticker: str) -> MarketData:
        if ticker == "MU":
            msg = "no bars for MU"
            raise DataUnavailableError(msg)
        return a_market(ticker)

    report = transfer_report(strategy, family, data_for)

    assert report.failures == ("MU",)
    assert {r.ticker for r in report.members} == set()
    assert not report.survives


def test_transfer_is_deterministic() -> None:
    strategy = a_strategy("AMD")
    family = Family(name="test", members=("AMD", "MU"), controls=("GLD",))

    def data_for(ticker: str) -> MarketData:
        return a_market(ticker, seed=len(ticker))

    assert transfer_report(strategy, family, data_for) == transfer_report(
        strategy, family, data_for
    )


# --------------------------------------------------------------------------- retargeting


def test_retarget_moves_only_the_ticker() -> None:
    """A transfer that also changed the window would not be measuring transfer."""
    strategy = a_strategy("AMD")
    moved = retarget(strategy, "MU")

    assert moved.universe.ticker == "MU"
    assert moved.universe.start_date == strategy.universe.start_date
    assert moved.universe.end_date == strategy.universe.end_date
    assert moved.universe.interval == strategy.universe.interval
    assert moved.execution == strategy.execution
    assert moved.entry == strategy.entry
    assert moved.exit == strategy.exit
    assert moved.indicators == strategy.indicators


def test_retarget_round_trips_through_validation() -> None:
    """Re-targeting builds by the same path as any other strategy, so it cannot be malformed."""
    strategy = a_strategy("AMD")
    assert retarget(retarget(strategy, "MU"), "AMD") == strategy


def _family_or_none(ticker: str) -> Family | None:
    try:
        return family_of(ticker)
    except ProspectError:
        return None


# --------------------------------------------------------------------------- rotation


def test_rotation_visits_every_ticker_before_repeating() -> None:
    rotation = Rotation(("A", "B", "C"))
    seen = []
    for _ in range(3):
        seen.append(rotation.current)
        rotation = rotation.advance()
    assert seen == ["A", "B", "C"]
    assert rotation.passes == 1
    assert rotation.current == "A"


def test_rotation_counts_completed_passes() -> None:
    rotation = Rotation(("A", "B"))
    for _ in range(5):
        rotation = rotation.advance()
    assert rotation.passes == 2
    assert rotation.current == "B"


def test_rotation_is_a_value_so_a_session_can_persist_it() -> None:
    """Resuming a sweep is restoring this; an iterator's position is not writable to a row."""
    rotation = Rotation(("A", "B", "C"), index=2, passes=4)
    assert Rotation(("A", "B", "C"), index=2, passes=4) == rotation
    assert rotation.advance() == Rotation(("A", "B", "C"), index=0, passes=5)


def test_rotation_refuses_an_empty_universe() -> None:
    with pytest.raises(ValueError, match="at least one ticker"):
        Rotation(())


def test_rotation_refuses_an_index_outside_the_universe() -> None:
    with pytest.raises(ValueError, match="within the universe"):
        Rotation(("A",), index=3)


# --------------------------------------------------------------------------- a whole tick


def test_prospect_once_produces_a_candidate_carrying_its_transfer() -> None:
    """The two rungs arrive together: a holdout figure alone is what section 19.2 forbids."""
    candidate = prospect_once(
        a_chassis("AMD"),
        lambda ticker: a_market(ticker, seed=len(ticker)),
        settings=GaSettings(population=4, generations=2),
    )

    assert isinstance(candidate, Candidate)
    assert candidate.ticker == "AMD"
    assert candidate.strategy_yaml
    assert candidate.transfer.home == "AMD"
    assert candidate.transfer.family == "semiconductors"
    assert candidate.distinct_configurations > 0


def test_prospect_once_records_what_the_search_could_see() -> None:
    """Forward validation starts after the last bar read, not after the wall clock."""
    market = a_market("AMD")
    candidate = prospect_once(
        a_chassis("AMD"),
        lambda _: market,
        settings=GaSettings(population=4, generations=2),
        now=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )

    assert candidate.discovered_at == datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    assert candidate.last_bar_seen == market.index[-1].date()


def test_prospect_once_refuses_a_ticker_with_no_family_before_searching() -> None:
    """Paying for a search and then discarding it is worse than refusing up front."""
    searched = False

    def data_for(ticker: str) -> MarketData:
        nonlocal searched
        searched = True
        return a_market(ticker)

    with pytest.raises(ProspectError, match="belongs to no family"):
        prospect_once(a_chassis("NOT_A_TICKER"), data_for)

    assert not searched


def test_prospect_once_is_cancellable() -> None:
    control = RunControl(should_stop=lambda: True)
    with pytest.raises(RunCancelled):
        prospect_once(
            a_chassis("AMD"),
            lambda ticker: a_market(ticker),
            settings=GaSettings(population=4, generations=2),
            control=control,
        )


def test_the_default_search_is_small_on_purpose() -> None:
    """Section 19.1: forty times the budget bought nothing, so the default does not spend it."""
    assert PROSPECT_SETTINGS.population * PROSPECT_SETTINGS.generations <= 400
