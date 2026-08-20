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
from datetime import date

import pytest

from cracktrade.data import MarketData
from cracktrade.errors import DataUnavailableError, ProspectError
from cracktrade.evolution import Chassis, random_genome
from cracktrade.prospect import (
    DEFAULT_UNIVERSE,
    FAMILIES,
    INVERSE_BUCKET,
    Family,
    SiblingResult,
    TransferReport,
    family_of,
    is_inverse,
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


def test_transfer_runs_the_same_genome_across_the_family() -> None:
    genome = random_genome(random.Random(7))
    family = Family(name="test", members=("AMD", "MU", "INTC"), controls=("GLD",))
    seen: list[str] = []

    def data_for(ticker: str) -> MarketData:
        seen.append(ticker)
        return a_market(ticker, seed=len(ticker))

    report = transfer_report(genome, a_chassis("AMD"), family, data_for)

    # Home, both siblings and the control -- and the home ticker never appears as a sibling.
    assert set(seen) == {"AMD", "MU", "INTC", "GLD"}
    assert {r.ticker for r in report.members} == {"MU", "INTC"}
    assert {r.ticker for r in report.controls} == {"GLD"}
    assert report.home == "AMD"
    assert report.failures == ()


def test_an_unavailable_sibling_is_recorded_not_raised() -> None:
    """A relative with no history is a fact about the data, not a reason to abort the sweep."""
    genome = random_genome(random.Random(7))
    family = Family(name="test", members=("AMD", "MU"), controls=("GLD",))

    def data_for(ticker: str) -> MarketData:
        if ticker == "MU":
            msg = "no bars for MU"
            raise DataUnavailableError(msg)
        return a_market(ticker)

    report = transfer_report(genome, a_chassis("AMD"), family, data_for)

    assert report.failures == ("MU",)
    assert {r.ticker for r in report.members} == set()
    assert not report.survives


def test_transfer_varies_only_the_ticker() -> None:
    """A transfer that also moved the window would not be measuring transfer."""
    genome = random_genome(random.Random(3))
    chassis = a_chassis("AMD")
    family = Family(name="test", members=("AMD", "MU"), controls=("GLD",))
    windows: list[tuple[date, date]] = []

    def data_for(ticker: str) -> MarketData:
        windows.append((chassis.start_date, chassis.end_date))
        return a_market(ticker)

    transfer_report(genome, chassis, family, data_for)

    assert len(set(windows)) == 1


def test_transfer_is_deterministic() -> None:
    genome = random_genome(random.Random(11))
    family = Family(name="test", members=("AMD", "MU"), controls=("GLD",))

    def data_for(ticker: str) -> MarketData:
        return a_market(ticker, seed=len(ticker))

    first = transfer_report(genome, a_chassis("AMD"), family, data_for)
    second = transfer_report(genome, a_chassis("AMD"), family, data_for)

    assert first == second


def _family_or_none(ticker: str) -> Family | None:
    try:
        return family_of(ticker)
    except ProspectError:
        return None
