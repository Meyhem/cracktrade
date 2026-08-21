"""The compute half of a prospecting tick, as a pool child runs it -- spec section 19.7.

Separated from :mod:`cracktrade.api.worker.prospect` because everything here has to be picklable
and must not touch the database: a child receives a reservation and the frames it needs, and
returns what it found. Ownership, transactions and the cursor stay in the parent, which is what
makes a child safe to kill.

**A child never raises.** An exception crossing the pool boundary would take down every other
tick in flight, so a failure is *returned* and the parent counts it -- the same discipline
:func:`cracktrade.evolution.parallel._score_one` follows for one candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cracktrade.data import StaticProvider
from cracktrade.errors import CracktradeError
from cracktrade.indicators.catalogue import install
from cracktrade.log import get_logger
from cracktrade.optimize import TradeFloor
from cracktrade.prospect import SweepHistory, prospect_once

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import pandas as pd

    from cracktrade.prospect import Candidate, Reservation, SweepParams

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TickJob:
    """Everything one child needs, and nothing it must not have.

    No connection, no provider and no session: a child cannot write, cannot fetch, and cannot
    advance a cursor. The frames come from the parent's
    :class:`~cracktrade.prospect.prefetch.PrefetchHorizon`, which is what keeps P children off
    the provider and what makes two ticks agree about a ticker they share.

    Attributes:
        reservation: the position this tick was handed.
        params: the session's frozen question.
        seed: the session's seed plus the reservation's ordinal.
        frames: raw provider frames for the ticker's family, keyed by ticker.
        max_filled_fraction: the engine's tolerance for forward-filled bars.
    """

    reservation: Reservation
    params: SweepParams
    seed: int
    frames: dict[str, pd.DataFrame] = field(repr=False)
    max_filled_fraction: float = 1.0


@dataclass(frozen=True, slots=True)
class TickResult:
    """What a child found, or why it did not.

    Carries its reservation home because the parent lands results as they arrive, out of
    dispatch order, and has to know which position each belongs to.
    """

    reservation: Reservation
    candidate: Candidate | None
    error: str | None

    @property
    def failed(self) -> bool:
        return self.error is not None


def run_tick(job: TickJob) -> TickResult:
    """Prospect one ticker in this process. Never raises.

    The indicator catalogue is installed here because a fresh child has an empty one and a
    search against an empty catalogue would fail for a reason that has nothing to do with the
    ticker. It is idempotent, so a forked child that inherited one pays nothing.

    The history goes through :class:`~cracktrade.data.StaticProvider`, so the child runs the
    identical :func:`~cracktrade.data.loader.load_history` path the serial worker runs. What was
    prefetched is the provider's raw frame, not a loaded history, precisely so that no second
    code path can sit between the provider and a candidate.

    ``workers=1`` is load-bearing and must not be made configurable: the process pool around this
    function *is* the parallelism, and a nested pool inside each child would oversubscribe the
    machine by a factor of P. Section 19.1 also measured that larger searches produce no better
    strategies, so there is nothing to buy with the cores it would take.
    """
    install()
    history = SweepHistory(
        params=job.params,
        provider=StaticProvider(job.frames),
        max_filled_fraction=job.max_filled_fraction,
    )
    try:
        candidate = prospect_once(
            job.params.chassis_for(job.reservation.ticker),
            history,
            settings=job.params.ga_settings(),
            seed=job.seed,
            workers=1,
            objective_name=job.params.objective,
            trade_floor=TradeFloor(
                minimum=job.params.min_trades, per_year=job.params.min_trades_per_year
            ),
            segments=job.params.segments,
            holdout_fraction=job.params.holdout_fraction,
        )
    except CracktradeError as failure:
        logger.info("%s failed: %s", job.reservation.ticker, failure)
        return TickResult(reservation=job.reservation, candidate=None, error=str(failure))
    # Deliberately broad: a child never raises across the pool boundary (see module docstring).
    except Exception as failure:
        logger.exception("%s raised an unexpected error", job.reservation.ticker)
        return TickResult(
            reservation=job.reservation,
            candidate=None,
            error=f"{type(failure).__name__}: {failure}",
        )
    return TickResult(reservation=job.reservation, candidate=candidate, error=None)


__all__ = ["TickJob", "TickResult", "run_tick"]
