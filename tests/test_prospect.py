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

import math
import random
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from cracktrade.config import Interval, Strategy
from cracktrade.control import RunControl
from cracktrade.data import MarketData, StaticProvider
from cracktrade.errors import DataUnavailableError, ProspectError, RunCancelled
from cracktrade.evolution import Chassis, GaSettings, random_genome, render
from cracktrade.prospect import (
    DAILY_LOOKBACK_DAYS,
    DEFAULT_UNIVERSE,
    FAMILIES,
    INVERSE_BUCKET,
    MIN_FORWARD_BARS,
    PROSPECT_SETTINGS,
    Candidate,
    Family,
    Rotation,
    SiblingResult,
    SweepHistory,
    SweepParams,
    TransferReport,
    family_of,
    forward_score,
    is_inverse,
    prospect_once,
    retarget,
    transfer_report,
)
from cracktrade.strategy import build_strategy
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


def a_strategy_that_never_trades(ticker: str = "AMD") -> Strategy:
    """A strategy whose entry cannot fire, which is what a rarely-trading candidate looks like
    over a short forward window."""
    return build_strategy(
        {
            "strategy": {"name": "inert"},
            "universe": {
                "ticker": ticker,
                "start_date": "2016-01-01",
                "end_date": "2020-01-01",
            },
            "execution": {
                "initial_capital": 10_000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "indicators": [{"name": "fast", "type": "sma", "window": 5}],
            "entry": {"signal": "close < 0"},
            "exit": {"signal": "close < 0"},
        }
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


def test_no_ticker_belongs_to_two_families() -> None:
    """``_BY_TICKER`` is a dict comprehension over every family in order, so a ticker listed
    twice does not raise -- the later family silently wins and the earlier one quietly loses a
    member. Which family a candidate is transfer-tested against would then depend on the order
    of a tuple.
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for family in FAMILIES:
        for ticker in family.members:
            if ticker in seen:
                duplicates.append(f"{ticker} in both {seen[ticker]} and {family.name}")
            seen[ticker] = family.name
    assert duplicates == []


def test_european_families_are_controlled_on_their_own_session() -> None:
    """Section 19.4: a control answers "is this just risk appetite?", and can only answer it if
    it is exposed to the hours the candidate traded.

    GLD and TLT trade a US afternoon against a European day, so a European family controlled on
    them would be measured on two clocks at once.
    """
    european = [f for f in FAMILIES if f.name.startswith("european ")]
    assert european, "the European families should be registered in FAMILIES"
    for family in european:
        assert set(family.controls) == {"4GLD.DE", "VGEA.DE"}, family.name
        assert all("." in member for member in family.members), family.name


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


# --------------------------------------------------------------------------- the question


def test_a_sessions_question_survives_a_round_trip_through_storage() -> None:
    """Resume restores the question exactly, or the leaderboard is comparing two questions."""
    params = SweepParams(
        interval=Interval.M15,
        population=12,
        generations=3,
        objective="sharpe",
        segments=6,
        holdout_fraction=0.25,
        min_trades=5,
        min_trades_per_year=1.5,
        slippage_pct=0.05,
        lookback_days=45,
    )
    assert SweepParams.from_dict(params.to_dict()) == params


def test_a_stored_question_missing_a_later_setting_still_resumes() -> None:
    """A session written before a field existed took days to accumulate. It is not discarded."""
    restored = SweepParams.from_dict({"interval": "1d"})
    assert restored.interval is Interval.D1
    assert restored.population == SweepParams().population


def test_the_window_comes_from_the_intervals_own_reach() -> None:
    """The provider serves about 726 days of hourly bars and 58 of 15-minute ones, and that is
    a cliff rather than a taper. Asking for more does not fail -- it silently returns what
    exists -- so the window is taken from the interval rather than guessed."""
    today = date(2026, 8, 20)
    assert SweepParams(interval=Interval.H1).window(now=today)[0] == date(2024, 8, 25)
    assert SweepParams(interval=Interval.M15).window(now=today)[0] == date(2026, 6, 23)
    # Daily bars have no provider limit, so the figure there is a deliberate choice.
    assert SweepParams(interval=Interval.D1).window_days == DAILY_LOOKBACK_DAYS


def test_the_window_rolls_forward_rather_than_being_frozen_at_creation() -> None:
    """A sweep runs for days. A window fixed when it started would still be searching last
    month's bars a fortnight later, while the leaderboard claimed to be current."""
    params = SweepParams(interval=Interval.H1)
    monday, friday = params.window(now=date(2026, 8, 17)), params.window(now=date(2026, 8, 21))
    assert friday[1] > monday[1]
    assert friday[0] > monday[0]


def test_every_ticker_in_a_sweep_is_charged_the_same() -> None:
    params = SweepParams(slippage_pct=0.25, commission_pct=0.15, initial_capital=50_000.0)
    for ticker in ("AMD", "GLD"):
        chassis = params.chassis_for(ticker, now=date(2026, 8, 20))
        assert chassis.ticker == ticker
        assert (chassis.slippage_pct, chassis.commission_pct) == (0.25, 0.15)
        assert chassis.initial_capital == 50_000.0


def test_the_history_probe_is_a_valid_strategy() -> None:
    """It exists only to carry a ticker and a window into ``load_history``, and it is never
    evaluated -- which is exactly why a schema change could break it unnoticed. Building it
    here fails fast instead of failing inside a sweep at three in the morning."""
    history = SweepHistory(params=SweepParams(interval=Interval.D1), provider=StaticProvider())
    probe = history._probe("AMD")
    assert probe.universe.ticker == "AMD"
    assert probe.universe.interval is Interval.D1


def test_a_ticker_is_fetched_once_per_tick() -> None:
    """One tick asks for the same controls repeatedly. A second fetch would not only be slow --
    it could return different bars, making the transfer report an average over two histories."""
    fetches: list[str] = []

    class Counting(StaticProvider):
        def fetch(
            self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
        ) -> pd.DataFrame:
            fetches.append(ticker)
            return super().fetch(ticker, start, end, interval)

    frame = make_ohlcv(bars=BARS, start="2016-01-01", seed=1)
    history = SweepHistory(
        params=SweepParams(interval=Interval.D1, lookback_days=4000),
        provider=Counting({"SPY": frame}),
        now=date(2020, 1, 1),
    )
    assert history("SPY") is history("SPY")
    assert fetches == ["SPY"]


# --------------------------------------------------------------------------- forward scoring


def test_a_short_forward_window_is_not_scored_at_all() -> None:
    """Section 19.3 prefers "not yet" to a Sharpe from nine bars, which would sort above one
    computed from a year."""
    data = a_market("AMD")
    strategy = a_strategy("AMD")
    nearly_the_end = data.index[-(MIN_FORWARD_BARS - 5)].date()

    assert forward_score(strategy, data, since=nearly_the_end) is None


def test_a_window_without_a_warm_up_prefix_is_not_scored_either() -> None:
    """The indicators would be cold and the strategy would trade differently, so the figure
    would measure the truncation rather than the candidate."""
    data = a_market("AMD")
    strategy = a_strategy("AMD")

    assert forward_score(strategy, data, since=data.index[3].date(), warmup=50) is None


def test_a_forward_score_covers_only_the_bars_after_discovery() -> None:
    data = a_market("AMD")
    strategy = a_strategy("AMD")
    discovered = data.index[len(data) // 2].date()

    score = forward_score(strategy, data, since=discovered, warmup=30)

    assert score is not None
    assert score.first_bar > discovered
    assert score.last_bar == data.index[-1].date()
    assert score.bars == len(data) - len(data) // 2 - 1
    assert score.trades >= 0


# --------------------------------------------------------------------------- degenerate figures


def test_a_forward_window_without_trades_reports_no_sharpe_rather_than_infinity() -> None:
    """Measured on a real sweep, 2026-08-21.

    A Sharpe is a mean over a standard deviation, so a window in which the candidate never
    traded has no spread to divide by and ``extract_metrics`` returns ``+inf``. Postgres stores
    it, the API serialises it, and the leaderboard then shows an infinite Sharpe beside a
    strategy that did nothing at all -- the best-looking figure on the board earned by sitting
    out. Section 19.3.

    The window is still reported. A candidate that stopped trading is a real and useful forward
    result; only the ratio is undefined.
    """
    data = a_market("AMD")
    strategy = a_strategy_that_never_trades()
    discovered = data.index[len(data) // 2].date()

    score = forward_score(strategy, data, since=discovered, warmup=30)

    assert score is not None
    assert score.trades == 0
    assert score.sharpe is None
    assert score.bars >= MIN_FORWARD_BARS


def test_a_forward_sharpe_that_is_a_number_is_kept() -> None:
    """The refusal above must not swallow ordinary results."""
    data = a_market("AMD")
    strategy = a_strategy("AMD")
    discovered = data.index[len(data) // 2].date()

    score = forward_score(strategy, data, since=discovered, warmup=30)

    assert score is not None
    if score.trades > 0:
        assert score.sharpe is not None
        assert math.isfinite(score.sharpe)


def test_a_sibling_with_no_usable_sharpe_is_a_failure_not_a_measurement() -> None:
    """Measured on a real sweep, 2026-08-20.

    A Sharpe is a mean over a standard deviation, so a sibling whose returns have no spread
    produces an infinity rather than a figure. The median of two values is their mean, so one
    infinity carries the whole median to infinity -- and both halves of the rejection rule then
    answer a question about a degenerate statistic instead of about the candidate.

    On the real sweep it was a *control* (AMD's TLT, seven trades), which pushed the control
    median to infinity and rejected the candidate. That direction is harmless. The same
    arithmetic on a member would carry the sibling median to infinity and let the candidate
    through on a number that means nothing, which is the direction this test exists for.
    """
    report = TransferReport(
        home="AMD",
        family="semiconductors",
        home_sharpe=1.2,
        results=(
            a_sibling("MU", float("inf")),
            a_sibling("INTC", -0.4),
            a_sibling("GLD", -1.1, is_control=True),
        ),
    )

    assert report.median_sibling_sharpe == -0.4
    assert not report.survives


def test_every_sibling_being_degenerate_is_not_a_pass() -> None:
    """Zero, not infinity -- and zero does not clear the floor, which is a strict inequality."""
    report = TransferReport(
        home="AMD",
        family="semiconductors",
        home_sharpe=1.2,
        results=(a_sibling("MU", float("inf")), a_sibling("INTC", float("nan"))),
    )

    assert report.median_sibling_sharpe == 0.0
    assert not report.survives


# --------------------------------------------------------------------------- the rotation


def test_a_second_pass_does_not_replay_the_first() -> None:
    """The defect a real sweep exposed on 2026-08-20: with the seed taken from the cursor
    index, every lap re-ran the previous lap's searches and stored bit-identical candidates.
    Nine ticks over five tickers produced four exact duplicates."""
    rotation = Rotation(("AMD", "NVDA", "SOXL"))
    ordinals = []
    for _ in range(7):
        ordinals.append(rotation.ordinal)
        rotation = rotation.advance()

    assert ordinals == [0, 1, 2, 3, 4, 5, 6]
    assert len(set(ordinals)) == len(ordinals)


def test_a_ticks_ordinal_is_a_pure_function_of_the_cursor() -> None:
    """Which is what keeps a tick reproducible after a resume: the session stores two integers
    and the ordinal follows from them, so the same position always gets the same search."""
    assert Rotation(("AMD", "NVDA"), index=1, passes=3).ordinal == 7
    assert Rotation(("AMD", "NVDA"), index=1, passes=3).ordinal == 7


def test_a_reservation_hands_out_consecutive_ordinals() -> None:
    rotation = Rotation(("AMD", "NVDA", "SOXL"))

    reserved, after = rotation.reserve(2)

    assert [(r.ticker, r.ordinal) for r in reserved] == [("AMD", 0), ("NVDA", 1)]
    assert (after.index, after.passes) == (2, 0)


def test_a_reservation_wraps_the_universe_and_counts_the_pass() -> None:
    rotation = Rotation(("AMD", "NVDA", "SOXL"), index=2)

    reserved, after = rotation.reserve(3)

    assert [(r.ticker, r.ordinal) for r in reserved] == [("SOXL", 2), ("AMD", 3), ("NVDA", 4)]
    assert (after.index, after.passes) == (2, 1)


def test_every_reserved_ordinal_is_distinct_across_two_passes() -> None:
    """The ordinal is what a tick's seed derives from, so a repeat inside one reservation would
    hand two concurrent ticks the same search -- the 2026-08-20 duplicate defect, but within a
    single pool rather than across laps."""
    reserved, _ = Rotation(("AMD", "NVDA")).reserve(4)

    assert [r.ordinal for r in reserved] == [0, 1, 2, 3]
    assert len({r.ordinal for r in reserved}) == 4


def test_a_reservation_leaves_the_rotation_where_a_walk_would_have() -> None:
    """Reserving N and advancing N times must agree, because both are used: the pool reserves,
    and Rotation.advance is what every existing caller and the spec describe."""
    start = Rotation(("AMD", "NVDA", "SOXL"), index=1, passes=2)

    _, reserved_to = start.reserve(5)
    walked = start
    for _ in range(5):
        walked = walked.advance()

    assert reserved_to == walked


def test_a_reservation_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="positive count"):
        Rotation(("AMD",)).reserve(0)
