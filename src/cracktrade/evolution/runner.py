"""Composing a strategy, then judging it on history it never saw.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.6.

The order of operations is the whole point, and it is the same order section 9.4 established for
the optimizer, applied to a search that chooses structure as well as numbers. Everything that
*selects* -- every genome scored, every generation, the final ranking -- happens on the evolution
segments. The holdout is touched once, by the winner, after selection is over, and its numbers
are the only ones reported as out-of-sample.

What is different is the accounting. A parameter search scores thousands of vectors of one
strategy; this scores thousands of *different strategies*, so the deflation in section 12.3 is
doing much heavier lifting and the trial count it consumes has to be exact rather than
approximately right. It comes from the number of distinct rendered configurations, which
:class:`~cracktrade.evolution.protocol.Fitness` counts by digest.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import numpy as np

from cracktrade.backtest import (
    Calendar,
    buy_and_hold_portfolio,
    extract_metrics,
    extract_trades,
    run_simulation,
)
from cracktrade.backtest.benchmark import compare
from cracktrade.backtest.series import capture
from cracktrade.config import dump_strategy
from cracktrade.control import NO_CONTROL, RunControl
from cracktrade.domain import EvolutionResult, SegmentResult
from cracktrade.errors import EvolutionError
from cracktrade.evolution.blocks import library_warmup
from cracktrade.evolution.genome import blocks_used, describe, render
from cracktrade.evolution.parallel import evolution_pool, plan_workers
from cracktrade.evolution.protocol import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_MIN_SEGMENT_BARS,
    DEFAULT_SEGMENTS,
    Fitness,
    Regions,
    configuration_digest,
    split_for_evolution,
)
from cracktrade.evolution.search import GaSettings, run_evolution
from cracktrade.history import scope_of
from cracktrade.log import get_logger
from cracktrade.optimize.discovery import discover_parameters
from cracktrade.optimize.objective import (
    DEFAULT_OBJECTIVE,
    DEFAULT_TRADE_FLOOR,
    INFEASIBLE,
    TradeFloor,
    get_objective,
)
from cracktrade.validate.costs import cost_sensitivity
from cracktrade.validate.stability import stability_surface
from cracktrade.validate.statistics import (
    block_bootstrap_interval,
    deflated_sharpe,
    probability_of_backtest_overfitting,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy.typing as npt
    import pandas as pd
    import vectorbt as vbt

    from cracktrade.config import Strategy
    from cracktrade.data import MarketData
    from cracktrade.domain import Metrics, RunSeries, StabilityReport, Trade
    from cracktrade.evolution.genome import Chassis, Genome
    from cracktrade.evolution.protocol import EvolutionWindow
    from cracktrade.optimize.windows import TestWindow

logger = get_logger(__name__)


def evolve(
    chassis: Chassis,
    data: MarketData,
    *,
    settings: GaSettings | None = None,
    seed: int = 0,
    workers: int = 1,
    objective_name: str = DEFAULT_OBJECTIVE,
    trade_floor: TradeFloor = DEFAULT_TRADE_FLOOR,
    segments: int = DEFAULT_SEGMENTS,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    min_segment_bars: int = DEFAULT_MIN_SEGMENT_BARS,
    on_generation: Callable[[int, float], None] | None = None,
    control: RunControl = NO_CONTROL,
    capture_series: bool = False,
) -> EvolutionResult:
    """Compose a strategy for ``chassis``'s ticker and report it on unseen history.

    ``workers`` is the number of processes scoring candidates, or ``-1`` to size the pool from
    the search's budget (:func:`~cracktrade.evolution.parallel.plan_workers`). It does not change
    the answer -- a generation is bred in full before any of it is scored, and the counts that
    feed the deflation are merged in genome order by
    :meth:`~cracktrade.evolution.protocol.Fitness.evaluate_batch` -- and a test asserts as much.
    The default of one process bypasses the machinery entirely.

    Raises:
        EvolutionError: the history cannot support the division, or no genome in the whole
            search cleared the trade floor.
        RunCancelled: the caller asked for the run to stop.
    """
    ga = settings or GaSettings()
    calendar = Calendar.of(data)
    warmup = library_warmup()
    regions = split_for_evolution(
        data,
        warmup=warmup,
        segments=segments,
        holdout_fraction=holdout_fraction,
        min_segment_bars=min_segment_bars,
    )

    # Resolved once, from the whole evolution region rather than per segment. A segment is a
    # fraction of the region, and section 9.3's floor applied to each of them separately would
    # reject candidates for being short of trades in a window too small to have produced them.
    min_trades = trade_floor.required(
        regions.evolution_bars, periods_per_year=calendar.periods_per_year
    )

    logger.info(
        "evolving on %s: %d segment(s) over %d bars, %d-bar holdout, requiring %d closed trades",
        chassis.ticker,
        len(regions.segments),
        regions.evolution_bars,
        regions.holdout.scored_bars,
        min_trades,
    )

    fitness = Fitness(
        chassis=chassis,
        segments=regions.segments,
        # No floor inside the per-segment objective: the floor is a single constraint on the
        # summed trade count, applied by the fitness function itself.
        objective=get_objective(objective_name, min_trades=0),
        min_trades=min_trades,
    )

    # Only on an intraday run: the search must not propose a minimum holding period a session
    # cannot satisfy, because the engine refuses such a strategy outright (spec section 7.6) and
    # every genome drawing one would be lost as a rendering failure rather than scored.
    session_bars = calendar.bars_per_session if data.interval.is_intraday else None

    started = time.perf_counter()
    # Resolved from the budget as well as the setting: a search short enough to finish before
    # its workers have imported vectorbt is faster without them.
    processes = plan_workers(workers, budget=ga.budget)
    if processes == 1:
        outcome = run_evolution(
            fitness,
            settings=ga,
            seed=seed,
            on_generation=on_generation,
            control=control,
            session_bars=session_bars,
        )
    else:
        # The pool lives only as long as the search. Everything after this -- the holdout, the
        # overfitting probability, the stability surface -- is a handful of simulations in this
        # process, and holding idle workers open through them would be waste.
        with evolution_pool(fitness, workers=processes) as score_many:
            outcome = run_evolution(
                fitness,
                settings=ga,
                seed=seed,
                on_generation=on_generation,
                control=control,
                evaluate_batch=lambda batch: fitness.evaluate_batch(batch, score_many),
                session_bars=session_bars,
            )
    elapsed = time.perf_counter() - started

    if outcome.best_score == INFEASIBLE:
        msg = (
            f"no strategy in {fitness.trials} distinct candidate(s) produced the {min_trades} "
            f"closed trades required over {regions.evolution_bars} bars. Lower --min-trades or "
            f"--min-trades-per-year, or widen the date range"
        )
        raise EvolutionError(msg)

    _warn_if_any_candidate_failed(fitness)
    control.progress("evaluating the holdout", 100.0)

    winner = render(outcome.best, chassis)
    holdout_metrics, holdout_returns = _evaluate(winner, regions.holdout)

    # One buy-and-hold portfolio, shared by the reported comparison and the chart series, for
    # the reason section 9.4 gives: a benchmark curve that disagrees with the benchmark number
    # printed under it is worse than either alone.
    hold = buy_and_hold_portfolio(
        regions.holdout.data,
        winner.execution,
        winner.position_sizing,
        start_bar=regions.holdout.offset,
        calendar=calendar,
    )

    returns = np.asarray(holdout_returns.to_numpy(), dtype=np.float64)

    result = EvolutionResult(
        history=scope_of(data, multi_window=True),
        strategy_name=winner.strategy.name,
        ticker=chassis.ticker,
        objective=objective_name,
        composition=describe(outcome.best),
        blocks=blocks_used(outcome.best),
        strategy_yaml=dump_strategy(winner),
        holdout_metrics=holdout_metrics,
        benchmark=compare(
            holdout_metrics,
            extract_metrics(
                hold,
                risk_free_rate=winner.execution.risk_free_rate,
                calendar=calendar,
                offset=regions.holdout.offset,
            ),
            strategy_returns=holdout_returns,
            benchmark_returns=hold.returns().iloc[regions.holdout.offset :],
            calendar=calendar,
        ),
        segments=_segment_results(winner, regions.segments),
        trades=_holdout_trades(winner, regions.holdout),
        deflated=deflated_sharpe(
            returns,
            trials=fitness.trials,
            trial_sharpes=_per_period(np.array(fitness.trial_sharpes, dtype=np.float64), calendar),
        ),
        overfitting=probability_of_backtest_overfitting(
            _finalist_performance_matrix(chassis, fitness, outcome.finalists)
        ),
        stability=_stability(winner, regions, objective_name, min_trades),
        costs=cost_sensitivity(winner, regions.holdout),
        mean_return_interval=block_bootstrap_interval(returns, statistic="mean", seed=seed),
        total_return_interval=block_bootstrap_interval(returns, statistic="total", seed=seed),
        population=ga.population,
        generations=ga.generations,
        genomes_evaluated=fitness.evaluations,
        distinct_configurations=fitness.trials,
        failed_candidates=fitness.failures,
        most_common_failure=fitness.most_common_failure,
        min_trades_required=min_trades,
        evolution_bars=regions.evolution_bars,
        holdout_bars=regions.holdout.scored_bars,
        seed=seed,
        elapsed_seconds=elapsed,
        best_score_by_generation=tuple(step.best_score for step in outcome.history),
        series=_capture_holdout_series(winner, regions.holdout, hold) if capture_series else None,
    )

    _log_verdict(result)
    return result


def _evaluate(strategy: Strategy, holdout: TestWindow) -> tuple[Metrics, pd.Series]:
    """Score the winner on the holdout, returning its metrics and its per-bar returns.

    The returns keep their index: the information ratio subtracts one return series from
    another, and on bare arrays that subtraction is positional rather than by date.
    """
    simulation = run_simulation(strategy, holdout.data)
    metrics = extract_metrics(
        simulation.portfolio,
        risk_free_rate=strategy.execution.risk_free_rate,
        calendar=simulation.calendar,
        offset=holdout.offset,
    )
    return metrics, simulation.portfolio.returns().iloc[holdout.offset :]


def _segment_results(
    strategy: Strategy, segments: Sequence[EvolutionWindow]
) -> tuple[SegmentResult, ...]:
    """The winner measured on each segment it was selected over. In-sample by construction."""
    results: list[SegmentResult] = []
    for index, window in enumerate(segments):
        simulation = run_simulation(strategy, window.data)
        index_dates = window.data.index
        results.append(
            SegmentResult(
                index=index,
                first_bar=index_dates[window.offset].date(),
                last_bar=index_dates[-1].date(),
                bars=window.scored_bars,
                metrics=extract_metrics(
                    simulation.portfolio,
                    risk_free_rate=strategy.execution.risk_free_rate,
                    calendar=simulation.calendar,
                    offset=window.offset,
                ),
            )
        )
    return tuple(results)


def _holdout_trades(strategy: Strategy, holdout: TestWindow) -> tuple[Trade, ...]:
    """Trades taken on the holdout, excluding any opened during its warm-up prefix."""
    simulation = run_simulation(strategy, holdout.data)
    index = holdout.data.index
    cutoff = index[holdout.offset].date()
    return tuple(
        trade for trade in extract_trades(simulation.portfolio, index) if trade.entry_date >= cutoff
    )


def _finalist_performance_matrix(
    chassis: Chassis, fitness: Fitness, finalists: Sequence[Genome]
) -> npt.NDArray[np.float64]:
    """Every surviving configuration's Sharpe on every evolution segment.

    CSCV (section 12.4) needs a slices-by-configurations matrix. The slices are the evolution
    segments and the configurations are the distinct genomes the final population held, which is
    the right set: PBO asks whether picking the in-sample best beat picking one of the available
    alternatives at random, and the alternatives are what the search still had on the table.

    Nothing is re-simulated. Every finalist was scored during the search, and the per-segment
    Sharpes were kept for exactly this.
    """
    columns: list[tuple[float, ...]] = []
    for genome in finalists:
        scored = fitness.results.get(configuration_digest(render(genome, chassis)))
        # Infeasible finalists are excluded: they could never have been selected, so including
        # them as alternatives would make the winner's rank look better than the choice was.
        if scored is not None and scored.score != INFEASIBLE and scored.segment_sharpes:
            columns.append(scored.segment_sharpes)

    if not columns:
        return np.zeros((len(fitness.segments), 0), dtype=np.float64)
    return np.array(columns, dtype=np.float64).T


def _stability(
    winner: Strategy, regions: Regions, objective_name: str, min_trades: int
) -> StabilityReport:
    """The neighbourhood around the winner's numbers.

    Scored on the evolution region as a single window, which is *not* literally the fitness
    function: fitness is the median across segments, and no single-window objective reproduces
    that. The question the surface answers is unchanged -- does a 10% nudge to a parameter
    destroy the result -- but the scores in it are objective values on the region rather than
    fitness values, and the report says so rather than implying the search minimised them.

    Only the parameters section 9.1's discovery can see are perturbed. The thresholds evolution
    chose inside a signal expression are numeric literals in a string and are not reachable, so
    the surface understates how many numbers the winner actually depends on.
    """
    parameters = discover_parameters(winner)
    return stability_surface(
        winner,
        parameters,
        [parameter.value for parameter in parameters],
        regions.region,
        get_objective(objective_name, min_trades=min_trades),
    )


def _per_period(annualised: npt.NDArray[np.float64], calendar: Calendar) -> npt.NDArray[np.float64]:
    """De-annualise Sharpe ratios for the deflation formula.

    ``Metrics.sharpe_ratio`` is annualised on the run's own calendar, but the deflated
    Sharpe mixes the ratio with the observation count, so both have to be on the per-bar
    footing. Feeding it annualised trial Sharpes inflates the luck threshold by the square
    root of the periods per year -- about sixteen daily, about sixty-five on 30-minute
    bars -- which fails every strategy regardless of merit.
    """
    return annualised / math.sqrt(calendar.periods_per_year)


def _capture_holdout_series(
    strategy: Strategy, holdout: TestWindow, hold: vbt.Portfolio
) -> RunSeries:
    """Chart series for the holdout, and only for it.

    Capturing the evolution region too would draw a curve whose whole first stretch was selected
    on, presented beside numbers that were not.
    """
    simulation = run_simulation(strategy, holdout.data)
    return capture(
        portfolio=simulation.portfolio,
        benchmark=hold,
        data=holdout.data,
        calendar=simulation.calendar,
        offset=holdout.offset,
    )


def _warn_if_any_candidate_failed(fitness: Fitness) -> None:
    """Say so when a genome could not be turned into a strategy at all.

    Not a threshold, unlike the optimizer's 20%. Every genome the operators can produce is meant
    to render and simulate, so one failure is a defect in the block library or in ``repair`` --
    and it silently biases the search away from whichever region of the library produced it.
    """
    if fitness.failures:
        logger.warning(
            "%d candidate(s) could not be built or simulated (most often %s). Every genome is "
            "meant to render to a valid strategy, so this is a defect in the block library, and "
            "the search was steered away from part of the space rather than exploring it",
            fitness.failures,
            fitness.most_common_failure,
        )


def _log_verdict(result: EvolutionResult) -> None:
    """State the conclusion plainly, including when it is negative."""
    logger.info(
        "evolved %s on %s from %d distinct candidate(s): %.1f%% on the holdout vs %.1f%% "
        "buy-and-hold, PBO %.2f, deflated Sharpe P=%.3f",
        result.composition,
        result.ticker,
        result.distinct_configurations,
        result.holdout_metrics.total_return_pct,
        result.benchmark.benchmark.total_return_pct,
        result.overfitting.probability,
        result.deflated.probability,
    )
    if not result.is_credible:
        logger.warning(
            "the evolved strategy did not clear the robustness checks: %s",
            "; ".join(result.failures),
        )
