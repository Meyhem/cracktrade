"""One tick of a prospecting sweep -- spec section 19.3.

A tick is: evolve a strategy for one ticker, then move it across that ticker's family and see
whether anything survives. The result is a :class:`Candidate`, which is a record of something
worth looking at and explicitly not a verdict (section 19.2).

The two rungs are here together because the second is worthless without the first and the first
is misleading without the second. An evolved winner's holdout figure is the number the search
selected on and the number a reader most wants to believe; attaching the transfer result to it in
the same object is how the leaderboard is stopped from ever showing one without the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import yaml

from cracktrade.config import Strategy
from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.evolution import DEFAULT_HOLDOUT_FRACTION, DEFAULT_SEGMENTS, GaSettings, evolve
from cracktrade.optimize import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, TradeFloor
from cracktrade.prospect.families import family_of
from cracktrade.prospect.transfer import DataFor, TransferReport, transfer_report
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from cracktrade.evolution import Chassis

logger = logging.getLogger(__name__)

#: The search a prospecting tick runs, and it is deliberately small.
#:
#: Measured 2026-08-20 (section 19.1): the same ticker and seed with the budget scaled forty-fold
#: produced no improvement in-sample or out, and in one of the two tickers the largest search came
#: back negative and worse than the smallest. Meanwhile section 9.3's deflation threshold rises
#: with the trial count, so a bigger search raises its own bar while not raising its result. A
#: sweep's compute is therefore better spent on the next ticker than on more generations of this
#: one, and the default says so rather than leaving it to whoever fills in the form.
PROSPECT_SETTINGS = GaSettings(population=30, generations=10)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A strategy a sweep found, and everything needed to judge it later.

    Nothing here is a verdict. The holdout figures are the criteria the search *selected on* and
    are named so that no caller can mistake them for evidence; what a leaderboard ranks on is
    :attr:`transfer` now and forward performance once there are bars for it (section 19.5).

    Attributes:
        ticker: the instrument it was found on.
        discovered_at: when the search finished. Forward scoring reads bars after this, which is
            the whole reason it is recorded to the second rather than to the day.
        last_bar_seen: the final bar of the history the search could see. Forward validation
            starts strictly after it -- the discovery timestamp says when the search ran, this
            says what it had actually read, and on a stale cache those differ.
        strategy_yaml: the composed strategy, as the engine serialises it.
        composition: the blocks it was assembled from, in words.
        blocks: those block names.
        holdout_return_pct: selection criterion, not evidence.
        holdout_sharpe: selection criterion, not evidence.
        holdout_trades: closed trades behind the two figures above, so a result resting on four
            trades is visible as one.
        median_segment_return_pct: the in-sample median the fitness function selected on.
        distinct_configurations: how many strategies were considered to produce this one.
        transfer: how it travelled across its family.
        seed: the search's seed, so the tick is reproducible.
    """

    ticker: str
    discovered_at: datetime
    last_bar_seen: date
    strategy_yaml: str
    composition: str
    blocks: tuple[str, ...]
    holdout_return_pct: float
    holdout_sharpe: float
    holdout_trades: int
    median_segment_return_pct: float
    distinct_configurations: int
    transfer: TransferReport
    seed: int

    @property
    def survives_transfer(self) -> bool:
        """Whether this candidate is worth passing to the next rung of the ladder."""
        return self.transfer.survives


def prospect_once(
    chassis: Chassis,
    data_for: DataFor,
    *,
    settings: GaSettings = PROSPECT_SETTINGS,
    seed: int = 0,
    workers: int = 1,
    objective_name: str = DEFAULT_OBJECTIVE,
    trade_floor: TradeFloor = DEFAULT_TRADE_FLOOR,
    segments: int = DEFAULT_SEGMENTS,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    control: RunControl = NO_CONTROL,
    now: datetime | None = None,
) -> Candidate:
    """Evolve a strategy for ``chassis``'s ticker and transfer-test it.

    The ticker's family is resolved *before* the search runs. A candidate that cannot be
    transfer-tested would have to be ranked against candidates that were, so discovering the gap
    after paying for a search would leave the caller holding a result it must then discard.

    Raises:
        ProspectError: the chassis's ticker belongs to no family.
        EvolutionError: the history cannot support the division, or nothing cleared the floor.
        RunCancelled: the caller asked for the run to stop.
    """
    family = family_of(chassis.ticker)
    data = data_for(chassis.ticker)

    result = evolve(
        chassis,
        data,
        settings=settings,
        seed=seed,
        workers=workers,
        objective_name=objective_name,
        trade_floor=trade_floor,
        segments=segments,
        holdout_fraction=holdout_fraction,
        control=control,
    )

    # Between the two rungs: the search is the expensive half, and a caller who asked to stop
    # during it should not then pay for a transfer whose result nothing will read.
    control.raise_if_cancelled()

    # Through the full validator, not just the structural parse: the strategy is about to be
    # simulated on several instruments, and semantic checks are what make that safe.
    strategy: Strategy = build_strategy(yaml.safe_load(result.strategy_yaml))
    transfer = transfer_report(strategy, family, data_for, seed=seed)

    candidate = Candidate(
        ticker=chassis.ticker,
        discovered_at=now or datetime.now(UTC),
        last_bar_seen=data.index[-1].date(),
        strategy_yaml=result.strategy_yaml,
        composition=result.composition,
        blocks=result.blocks,
        holdout_return_pct=result.holdout_metrics.total_return_pct,
        holdout_sharpe=result.holdout_metrics.sharpe_ratio,
        holdout_trades=result.holdout_metrics.total_trades,
        median_segment_return_pct=result.median_segment_return_pct,
        distinct_configurations=result.distinct_configurations,
        transfer=transfer,
        seed=seed,
    )
    logger.info(
        "prospected %s: %d configurations, transfer %s",
        candidate.ticker,
        candidate.distinct_configurations,
        "survived" if candidate.survives_transfer else "rejected",
    )
    return candidate


@dataclass(frozen=True, slots=True)
class Reservation:
    """One tick's claim on a position in the sweep.

    Handed out *before* the search runs (section 19.7). That ordering is what lets several ticks
    be in flight at once: the cursor advance becomes one cheap statement taken up front, rather
    than something a multi-minute search has to hold a transaction open across.

    The ordinal is what the tick's seed derives from, so it has to survive the round trip through
    the database unchanged -- two ticks holding the same ordinal would run the same search twice
    and store it twice, which is the duplicate-candidate defect a real sweep exposed on
    2026-08-20, reappearing inside one pool rather than across laps.

    Attributes:
        ticker: the instrument to prospect.
        ordinal: this tick's position across the whole sweep.
    """

    ticker: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class Rotation:
    """Where a sweep has got to in its universe.

    Round-robin, and deliberately so (section 19.7). Allocating more compute to tickers that have
    scored well is fitting the ticker choice to the sample -- the selection bias section 12 is
    about -- and unlike the genome count nothing in the engine would be counting it. Boring and
    defensible beats clever and unmeasured.

    A value rather than an iterator because it is what a session persists: resuming a sweep is
    restoring this, and an iterator's position is not a thing that can be written to a row.

    Attributes:
        universe: the tickers to sweep, in order.
        index: position of the next ticker to prospect.
        passes: completed sweeps of the whole universe.
    """

    universe: tuple[str, ...]
    index: int = 0
    passes: int = 0

    def __post_init__(self) -> None:
        if not self.universe:
            msg = "a rotation needs at least one ticker"
            raise ValueError(msg)
        if not 0 <= self.index < len(self.universe):
            msg = f"index must be within the universe, got {self.index}"
            raise ValueError(msg)

    @property
    def current(self) -> str:
        """The ticker to prospect next."""
        return self.universe[self.index]

    @property
    def ordinal(self) -> int:
        """How many ticks this sweep has run before this one.

        What a tick's seed is derived from, and it must be this rather than :attr:`index`.
        Deriving from the index alone makes the seed repeat every lap, so the second pass
        re-runs the first pass's searches exactly and stores bit-identical candidates -- which
        is the whole value of leaving a sweep running overnight, spent on rediscovering what it
        already knew. Measured on a real sweep 2026-08-20: nine ticks over five tickers
        produced four exact duplicates.

        The ordinal is unique across the whole sweep and is a pure function of the cursor, so a
        tick stays exactly reproducible from the session's seed and its position.
        """
        return self.passes * len(self.universe) + self.index

    def advance(self) -> Rotation:
        """The rotation after prospecting :attr:`current`."""
        following = self.index + 1
        if following < len(self.universe):
            return Rotation(self.universe, following, self.passes)
        return Rotation(self.universe, 0, self.passes + 1)

    def reserve(self, count: int) -> tuple[tuple[Reservation, ...], Rotation]:
        """``count`` consecutive positions, and the rotation that follows them.

        What a pool of concurrent ticks takes before it dispatches: each reservation carries the
        ticker to prospect and the ordinal its seed comes from, so P ticks running at once are
        still exactly the P searches a serial sweep would have run, in the same order.

        Built by repeated :meth:`advance` rather than by arithmetic on the ordinal, so the
        wrap-around is defined in exactly one place.
        :meth:`~cracktrade.api.repos.ProspectRepo.reserve_ordinals` performs the same advance in
        SQL because it has to be atomic across workers, and a test asserts the two agree across a
        wrap.

        Raises:
            ValueError: ``count`` is not positive.
        """
        if count < 1:
            msg = f"a reservation needs a positive count, got {count}"
            raise ValueError(msg)
        reserved: list[Reservation] = []
        rotation = self
        for _ in range(count):
            reserved.append(Reservation(ticker=rotation.current, ordinal=rotation.ordinal))
            rotation = rotation.advance()
        return tuple(reserved), rotation


__all__ = [
    "PROSPECT_SETTINGS",
    "Candidate",
    "Reservation",
    "Rotation",
    "prospect_once",
]
