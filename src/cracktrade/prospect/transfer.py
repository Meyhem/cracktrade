"""Sibling transfer -- spec section 19.4.

The candidate is re-targeted at each of its family's tickers and run **unchanged**.
What is recorded is the distribution of sibling Sharpes; what matters is the median, for the
reason section 16.5 gives for using a median segment rather than a mean -- one spectacular
member must not carry a candidate.

Measured 2026-08-20, a genome evolved on AMD at 1h: Sharpe 1.30 on AMD, median sibling Sharpe
-0.49, negative on six of seven relatives, and losing money on SMH -- the ETF that holds AMD. A
Sharpe of 1.30 that does not survive the move to the next semiconductor was never measuring
semiconductors, and this module is how that is found out in seconds rather than in weeks.

The unit is a :class:`~cracktrade.config.Strategy` rather than an evolved genome, which
costs nothing and buys two things: a hand-written strategy can be transfer-tested exactly like a
composed one, and this module needs no import from :mod:`cracktrade.evolution`. Re-targeting goes
through :func:`~cracktrade.config.dump_strategy` and full validation rather than a textual
substitution on the YAML -- the round trip is contractual, so a re-targeted strategy is built by
the same path as any other and cannot be malformed in a way only this module could produce.

**Transfer rejects; it does not promote.** It is demonstrated to be decisive at killing overfits
and is unproven at identifying real edges, and those are different jobs. Correlated names over
one period share market-wide moves, so a candidate can transfer by riding beta rather than by
carrying an edge -- which is what the controls in :class:`~cracktrade.prospect.families.Family`
exist to expose. A transfer failure is strong evidence; a transfer pass is the absence of one
particular kind of evidence against.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

import yaml

from cracktrade.backtest import extract_metrics, run_simulation
from cracktrade.config import dump_strategy
from cracktrade.errors import CracktradeError
from cracktrade.strategy import build_strategy

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from cracktrade.config import Strategy
    from cracktrade.data import MarketData
    from cracktrade.prospect.families import Family

logger = logging.getLogger(__name__)

#: Supplies the price history for one ticker. The library never fetches: a provider, a cache and
#: a date range are the caller's, exactly as :class:`~cracktrade.control.RunControl` keeps queues
#: and terminals out of the engine's vocabulary.
DataFor = Callable[[str], "MarketData"]

#: A candidate must make money on its relatives, not merely lose less than on unrelated ones.
#: Zero rather than a tuned figure: this is a rejection rule, and a threshold fitted to make some
#: particular candidate survive would be the search's bias moved into the validator.
TRANSFER_FLOOR = 0.0


@dataclass(frozen=True, slots=True)
class SiblingResult:
    """One ticker the candidate was moved to, and what happened.

    Attributes:
        ticker: the instrument the unchanged genome was run on.
        is_control: whether this ticker is outside the family's economic mechanism.
        sharpe: annualised Sharpe on that ticker.
        total_return_pct: total return over the same window.
        total_trades: closed trades, so a "result" from two trades is visible as such.
    """

    ticker: str
    is_control: bool
    sharpe: float
    total_return_pct: float
    total_trades: int


@dataclass(frozen=True, slots=True)
class TransferReport:
    """How a candidate held up when moved off the ticker it was found on.

    Attributes:
        home: the ticker the candidate was evolved on.
        family: that ticker's family name.
        home_sharpe: the candidate's Sharpe on its own ticker, for the contrast.
        results: every ticker it was moved to, members and controls alike.
        failures: tickers whose backtest could not be run at all, with the reason. Unlike
            section 16.6's ``failed_candidates`` this is not necessarily a defect -- a sibling
            may simply have no history over the chassis's range -- so it is reported rather than
            raised on.
    """

    home: str
    family: str
    home_sharpe: float
    results: tuple[SiblingResult, ...]
    failures: tuple[str, ...] = ()

    @property
    def members(self) -> tuple[SiblingResult, ...]:
        """Results on tickers inside the family's mechanism."""
        return tuple(r for r in self.results if not r.is_control)

    @property
    def controls(self) -> tuple[SiblingResult, ...]:
        """Results on tickers outside it."""
        return tuple(r for r in self.results if r.is_control)

    @property
    def median_sibling_sharpe(self) -> float:
        """Median Sharpe across family members. Zero when there were none to measure."""
        return _median_sharpe(self.members)

    @property
    def median_control_sharpe(self) -> float:
        """Median Sharpe across controls. Zero when there were none to measure."""
        return _median_sharpe(self.controls)

    @property
    def negative_members(self) -> int:
        """How many family members the candidate lost money on."""
        return sum(1 for r in self.members if r.sharpe < 0)

    @property
    def beats_controls(self) -> bool:
        """Whether the family did better than instruments outside it.

        False means the candidate performs as well on gold as on the semiconductors it was
        evolved across, which is a description of the market rather than of the family.
        """
        return self.median_sibling_sharpe > self.median_control_sharpe

    @property
    def survives(self) -> bool:
        """Whether the candidate is worth passing to the next rung of section 19.3's ladder.

        Two conditions, and both are rejections rather than endorsements: it must make money on
        its relatives, and it must do so by more than it does on instruments sharing none of
        their mechanism. Surviving this is not evidence the candidate works -- it is the absence
        of the cheapest available evidence that it does not.
        """
        if not self.members:
            return False
        return self.median_sibling_sharpe > TRANSFER_FLOOR and self.beats_controls


def _median_sharpe(results: tuple[SiblingResult, ...]) -> float:
    """Median Sharpe over ``results``, or zero when the sequence is empty.

    Non-finite values are dropped, as defence in depth: nothing should reach here carrying one,
    because :func:`transfer_report` records such a sibling as a failure instead. But the median
    of two values is their mean, so a single infinity is enough to make the whole figure
    infinite -- and the two comparisons in :attr:`TransferReport.survives` would then be
    answering a question about a degenerate statistic rather than about the candidate. Measured
    on a real sweep 2026-08-20: an AMD candidate's TLT control produced an infinite Sharpe over
    seven trades, which carried the control median to infinity and rejected the candidate.
    Rejection was the harmless direction; the same arithmetic on a *member* would have carried
    the sibling median to infinity and passed the candidate on a statistic that means nothing.
    """
    finite = [result.sharpe for result in results if math.isfinite(result.sharpe)]
    if not finite:
        return 0.0
    return float(median(finite))


def retarget(strategy: Strategy, ticker: str) -> Strategy:
    """The same strategy pointed at a different instrument.

    Built through the serialisation round trip and full validation rather than by mutating the
    model, so the result is constructed by exactly the path every other strategy takes. Only the
    ticker moves: the date range, interval, costs and every rule are the strategy's own, because
    a transfer that also changed the window would not be measuring transfer.

    Raises:
        StrategyValidationError: the re-targeted strategy is not valid, which would be a defect
            in the round trip rather than anything about ``ticker``.
    """
    payload = yaml.safe_load(dump_strategy(strategy))
    payload["universe"]["ticker"] = ticker
    return build_strategy(payload)


def transfer_report(
    strategy: Strategy,
    family: Family,
    data_for: DataFor,
    *,
    seed: int = 0,
) -> TransferReport:
    """Run ``strategy`` unchanged across ``family`` and report how it travelled.

    Args:
        strategy: the candidate, exactly as the search left it. Its own ticker is the home.
        family: the relatives and controls to move it to.
        data_for: supplies each ticker's history.
        seed: passed through to the simulation.
    """
    home_ticker = strategy.universe.ticker
    home = _score(strategy, home_ticker, data_for, seed=seed)
    results: list[SiblingResult] = []
    failures: list[str] = []

    targets = [(t, False) for t in family.siblings_of(home_ticker)]
    targets += [(t, True) for t in family.controls if t != home_ticker]

    for ticker, is_control in targets:
        scored = _score(strategy, ticker, data_for, seed=seed)
        if scored is None:
            failures.append(ticker)
            continue
        if not math.isfinite(scored.sharpe):
            # A Sharpe is a mean divided by a standard deviation, so a run whose returns have
            # no spread -- a handful of trades, or none -- produces an infinity rather than a
            # measurement. Recorded as a failure, which is what it is: this sibling did not
            # answer the question. Averaging it in would let one degenerate instrument decide
            # a verdict about eight.
            failures.append(f"{ticker} (no usable Sharpe over {scored.total_trades} trades)")
            continue
        results.append(
            SiblingResult(
                ticker=ticker,
                is_control=is_control,
                sharpe=scored.sharpe,
                total_return_pct=scored.total_return_pct,
                total_trades=scored.total_trades,
            )
        )

    report = TransferReport(
        home=home_ticker,
        family=family.name,
        home_sharpe=home.sharpe if home is not None else 0.0,
        results=tuple(results),
        failures=tuple(failures),
    )
    logger.info(
        "transfer for %s (%s): home %.2f, median sibling %.2f, controls %.2f -- %s",
        report.home,
        report.family,
        report.home_sharpe,
        report.median_sibling_sharpe,
        report.median_control_sharpe,
        "survives" if report.survives else "rejected",
    )
    return report


@dataclass(frozen=True, slots=True)
class _Scored:
    """The three figures transfer reads off one simulation."""

    sharpe: float
    total_return_pct: float
    total_trades: int


def _score(
    strategy: Strategy,
    ticker: str,
    data_for: DataFor,
    *,
    seed: int,
) -> _Scored | None:
    """Simulate ``strategy`` on ``ticker``, or return None if that could not be done.

    Every engine failure is caught and reported as a missing sibling rather than propagated. A
    relative with no history over the strategy's range is a fact about the data, and letting it
    abort the sweep would make the loop's progress depend on the least available instrument in
    each family.
    """
    try:
        targeted = retarget(strategy, ticker)
        data = data_for(ticker)
        simulation = run_simulation(targeted, data, seed=seed)
        metrics = extract_metrics(
            simulation.portfolio,
            risk_free_rate=targeted.execution.risk_free_rate,
            calendar=simulation.calendar,
        )
    except CracktradeError as exc:
        logger.info("transfer to %s failed: %s: %s", ticker, type(exc).__name__, exc)
        return None
    return _Scored(
        sharpe=metrics.sharpe_ratio,
        total_return_pct=metrics.total_return_pct,
        total_trades=metrics.total_trades,
    )


__all__ = [
    "TRANSFER_FLOOR",
    "DataFor",
    "SiblingResult",
    "TransferReport",
    "retarget",
    "transfer_report",
]
