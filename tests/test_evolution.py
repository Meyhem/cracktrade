"""Evolutionary strategy generation -- spec section 16.

Three properties carry this feature, and each has a test here that fails loudly if it stops
holding.

* **Every genome renders.** The block library and ``repair`` are jointly responsible for the
  claim that the reachable space contains no invalid strategy. If one did, it would be scored
  ``INFEASIBLE`` and the search would quietly avoid a region of the library rather than
  reporting a bug in it.
* **The search is reproducible.** Same seed, same answer. An irreproducible search is defect
  D12, and it is not less of one for being evolutionary.
* **The holdout does not influence selection.** Asserted end to end rather than by inspection:
  two histories differing *only* in the holdout produce the same evolved strategy.
"""

from __future__ import annotations

import io
import math
import random
from datetime import date
from itertools import pairwise

import pandas as pd
import pytest
import yaml
from rich.console import Console

from cracktrade.cli.render import render_evolution
from cracktrade.config import parse_strategy
from cracktrade.data import MarketData
from cracktrade.domain import EvolutionResult
from cracktrade.errors import EvolutionError
from cracktrade.evolution import (
    BLOCKS,
    Chassis,
    GaSettings,
    Genome,
    Slot,
    Stop,
    configuration_digest,
    crossover,
    describe,
    evolve,
    library_warmup,
    mutate,
    random_genome,
    render,
    repair,
    run_evolution,
    split_for_evolution,
)
from cracktrade.evolution.blocks import GeneRef, instantiate
from cracktrade.indicators import registry
from cracktrade.optimize import TradeFloor
from cracktrade.serialize import to_dict
from cracktrade.signals import parse_expression
from cracktrade.strategy import required_warmup
from tests.factories import make_ohlcv

#: Enough bars for a 200-bar library warm-up, four evolution segments and a holdout.
BARS = 900


def a_market(bars: int = BARS, *, seed: int = 0, trend: float = 0.05) -> MarketData:
    return MarketData(
        ticker="TEST",
        frame=make_ohlcv(bars=bars, start="2016-01-01", trend=trend, seed=seed),
        requested_start=date(2016, 1, 1),
        requested_end=date(2020, 1, 1),
    )


def a_chassis() -> Chassis:
    return Chassis(
        name="evolved_test",
        ticker="TEST",
        start_date=date(2016, 1, 1),
        end_date=date(2020, 1, 1),
    )


# ------------------------------------------------------------------ the block library


def test_every_block_expression_parses_under_the_grammar() -> None:
    """A block whose expression the grammar rejects would fail at evaluation, not at import."""
    for block in BLOCKS:
        instance = instantiate(block, "ea", [gene.high for gene in block.genes])
        parse_expression(instance.expression)


def test_every_block_names_a_registered_indicator() -> None:
    registry_installed()
    for block in BLOCKS:
        for template in block.indicators:
            assert registry.has(template.type), f"{block.name} wants unregistered {template.type}"


def test_every_block_expression_resolves_the_names_it_uses() -> None:
    """No placeholder may reference an indicator output the block does not produce."""
    registry_installed()
    for block in BLOCKS:
        instance = instantiate(block, "ea", [gene.low for gene in block.genes])
        available = {"open", "high", "low", "close", "volume"}
        for template, rendered in zip(block.indicators, instance.indicators, strict=True):
            spec = registry.get(template.type)
            available.update(spec.output_names(str(rendered["name"])))
        assert parse_expression(instance.expression).names <= available, block.name


def test_every_gene_a_block_references_is_declared() -> None:
    declared = {block.name: {gene.name for gene in block.genes} for block in BLOCKS}
    for block in BLOCKS:
        for template in block.indicators:
            for _, value in template.params:
                if isinstance(value, GeneRef):
                    assert value.name in declared[block.name], f"{block.name}: {value.name}"


def test_block_names_are_unique() -> None:
    names = [block.name for block in BLOCKS]
    assert len(set(names)) == len(names)


def test_the_library_warmup_covers_every_block_at_its_widest() -> None:
    """The prefix every scored window gets. Sized from the library, never from one genome."""
    ceiling = library_warmup()
    assert ceiling > 0

    rng = random.Random(11)
    for _ in range(200):
        strategy = render(random_genome(rng), a_chassis())
        assert required_warmup(strategy) <= ceiling


def registry_installed() -> None:
    """Force the catalogue to register itself, as the engine does before any lookup."""
    import importlib

    importlib.import_module("cracktrade.indicators.compute").registry_installed()


# ------------------------------------------------------------------ rendering totality


def test_every_reachable_genome_renders_to_a_valid_strategy() -> None:
    """The claim the whole feature rests on. See the module docstring."""
    rng = random.Random(3)
    genome = random_genome(rng)

    for _ in range(400):
        render(genome, a_chassis())
        partner = random_genome(rng)
        genome = mutate(crossover(genome, partner, rng), rng, rate=0.4)


def test_a_rendered_strategy_never_repeats_an_indicator_name() -> None:
    """Two slots may hold the same block; the schema requires their names to differ."""
    rng = random.Random(5)
    for _ in range(200):
        strategy = render(random_genome(rng), a_chassis())
        names = [indicator.name for indicator in strategy.indicators]
        assert len(set(names)) == len(names)


def test_a_two_condition_entry_parenthesises_both_sides() -> None:
    """'&' binds tighter than a comparison, so the unparenthesised form means something else."""
    genome = repair(
        Genome(
            entry_a=Slot(block=_block_index("rsi_below_level"), values=(14.0, 30.0)),
            entry_b=Slot(block=_block_index("adx_above_level"), values=(14.0, 25.0)),
            combinator="&",
            exit_condition=None,
            stop=None,
            take_profit_pct=None,
            max_holding_days=10,
            min_holding_days=None,
        )
    )
    signal = render(genome, a_chassis()).entry.signal
    assert signal == "(ea_rsi < 30.00) & (eb_adx_adx > 25.00)"


def test_rendering_is_a_pure_function_of_the_genome() -> None:
    rng = random.Random(9)
    for _ in range(50):
        genome = random_genome(rng)
        assert configuration_digest(render(genome, a_chassis())) == configuration_digest(
            render(genome, a_chassis())
        )


# ------------------------------------------------------------------ repair


def test_repair_gives_a_genome_with_no_way_out_a_holding_cap() -> None:
    stranded = Genome(
        entry_a=Slot(block=0, values=tuple(gene.low for gene in BLOCKS[0].genes)),
        entry_b=None,
        combinator="&",
        exit_condition=None,
        stop=None,
        take_profit_pct=None,
        max_holding_days=None,
        min_holding_days=None,
    )
    assert repair(stranded).max_holding_days is not None
    render(repair(stranded), a_chassis())


def test_repair_yields_the_holding_floor_to_the_cap() -> None:
    inverted = Genome(
        entry_a=Slot(block=0, values=tuple(gene.low for gene in BLOCKS[0].genes)),
        entry_b=None,
        combinator="&",
        exit_condition=None,
        stop=None,
        take_profit_pct=None,
        max_holding_days=5,
        min_holding_days=9,
    )
    repaired = repair(inverted)
    assert repaired.max_holding_days == 5
    assert repaired.min_holding_days == 4


def test_repair_is_deterministic() -> None:
    """A repair that consulted the rng would make reproducibility depend on how often it ran."""
    rng = random.Random(2)
    for _ in range(100):
        genome = random_genome(rng)
        assert repair(genome) == repair(repair(genome))


# ------------------------------------------------------------------ the search


def a_synthetic_fitness(genome: Genome) -> float:
    """Deterministic, arbitrary, and dependent on the whole genome.

    Not a backtest: these tests are about the operators, and a real objective would make a
    divergence in the operators look like a divergence in the market data.
    """
    key = describe(genome)
    return float(len(key) % 17) - genome.entry_a.block


def test_the_same_seed_produces_the_same_search() -> None:
    settings = GaSettings(population=12, generations=5)
    fitness = a_synthetic_fitness
    first = run_evolution(fitness, settings=settings, seed=4)
    second = run_evolution(fitness, settings=settings, seed=4)

    assert first.best == second.best
    assert first.best_score == second.best_score
    assert [step.best_score for step in first.history] == [
        step.best_score for step in second.history
    ]


def test_a_different_seed_produces_a_different_search() -> None:
    settings = GaSettings(population=12, generations=4)
    assert (
        run_evolution(a_synthetic_fitness, settings=settings, seed=1).history
        != run_evolution(a_synthetic_fitness, settings=settings, seed=2).history
    )


def test_the_best_score_never_gets_worse() -> None:
    """What elitism is for: an unlucky recombination must not lose the best answer found."""
    outcome = run_evolution(
        a_synthetic_fitness, settings=GaSettings(population=10, generations=6), seed=8
    )
    best = [step.best_score for step in outcome.history]
    assert all(later <= earlier for earlier, later in pairwise(best))


def test_settings_reject_a_population_the_operators_cannot_work_with() -> None:
    with pytest.raises(ValueError, match="population"):
        GaSettings(population=1)
    with pytest.raises(ValueError, match="elites"):
        GaSettings(population=4, elites=4)
    with pytest.raises(ValueError, match="tournament"):
        GaSettings(population=4, tournament=9)


# ------------------------------------------------------------------ the protocol


def test_the_holdout_is_the_most_recent_stretch_and_never_overlaps_a_segment() -> None:
    data = a_market()
    regions = split_for_evolution(data, warmup=200, segments=4)

    assert regions.holdout.data.index[-1] == data.index[-1]

    first_holdout_bar = regions.holdout.data.index[regions.holdout.offset]
    for segment in regions.segments:
        last_scored = segment.data.index[-1]
        assert last_scored < first_holdout_bar


def test_every_segment_carries_a_full_warmup_prefix() -> None:
    regions = split_for_evolution(a_market(), warmup=200, segments=4)
    for segment in regions.segments:
        assert segment.offset == 200
        assert segment.scored_bars > 0


def test_the_segments_cover_the_evolution_region_without_gaps() -> None:
    data = a_market()
    regions = split_for_evolution(data, warmup=200, segments=4)

    scored = [segment.data.index[segment.offset] for segment in regions.segments]
    assert scored[0] == data.index[200]
    for earlier, later in pairwise(regions.segments):
        assert earlier.data.index[-1] < later.data.index[later.offset]
    assert regions.segments[-1].data.index[-1] == data.index[regions.evolution_bars - 1]


def test_a_history_too_short_to_divide_is_refused_with_a_reason() -> None:
    with pytest.raises(EvolutionError, match="scorable bars"):
        split_for_evolution(a_market(bars=400), warmup=200, segments=4)


def test_a_holdout_too_small_to_judge_is_refused() -> None:
    with pytest.raises(EvolutionError, match="holdout"):
        split_for_evolution(a_market(), warmup=200, segments=4, holdout_fraction=0.05)


# ------------------------------------------------------------------ end to end


@pytest.fixture(scope="module")
def evolved() -> EvolutionResult:
    return evolve(
        a_chassis(),
        a_market(),
        settings=GaSettings(population=8, generations=3),
        seed=7,
    )


def test_evolution_produces_a_strategy_that_loads_back(evolved: EvolutionResult) -> None:
    parse_strategy(yaml.safe_load(evolved.strategy_yaml))
    assert evolved.blocks
    assert evolved.composition.startswith("entry: ")


def test_no_candidate_fails_to_build(evolved: EvolutionResult) -> None:
    """A failure here is a defect in the library, not a fact about the market."""
    assert evolved.failed_candidates == 0, evolved.most_common_failure


def test_distinct_configurations_never_exceed_evaluations(evolved: EvolutionResult) -> None:
    """Elites are re-selected but not re-counted.

    Counting a repeat as a separate look would understate the deflation, which is the one
    statistic standing between a structure search and an impressive-looking coincidence.
    """
    assert 0 < evolved.distinct_configurations <= evolved.genomes_evaluated
    assert evolved.deflated.trials == evolved.distinct_configurations


def test_the_deflated_sharpe_is_a_real_number(evolved: EvolutionResult) -> None:
    """An infinite trial Sharpe used to poison the variance and report 'P=nan'.

    That reads as a failed check rather than as a broken computation, which is the wrong kind of
    wrong for a number this report treats as a verdict.
    """
    assert math.isfinite(evolved.deflated.probability)
    assert math.isfinite(evolved.deflated.threshold)


def test_the_report_carries_its_verdict_through_serialization(evolved: EvolutionResult) -> None:
    payload = to_dict(evolved)
    assert "is_credible" in payload
    assert "checks" in payload
    assert len(payload["checks"]) == len(evolved.checks)


def test_the_evolution_report_renders(evolved: EvolutionResult) -> None:
    console = Console(file=io.StringIO(), width=110, force_terminal=False)
    render_evolution(evolved, console)
    output = console.file.getvalue()  # type: ignore[attr-defined]

    assert "Composed of" in output
    assert "Robustness" in output
    assert "Cost sensitivity" in output
    assert ("CREDIBLE" in output) or ("NOT CREDIBLE" in output)
    # The in-sample segments must never be presentable as evidence.
    assert "in sample" in output
    assert "none of these are evidence" in output


def test_the_holdout_is_reported_on_the_bars_it_actually_covers(
    evolved: EvolutionResult,
) -> None:
    assert evolved.holdout_bars > 0
    assert evolved.holdout_metrics.bars >= evolved.holdout_bars
    assert all(trade.entry_date <= trade.exit_date for trade in evolved.trades if trade.exit_date)


def test_the_holdout_cannot_influence_which_strategy_is_chosen() -> None:
    """The property the whole protocol exists for, asserted end to end.

    Two histories identical up to the holdout and different inside it. If any part of selection
    could see the holdout -- fitness, the final ranking, the segment division -- the two runs
    would produce different strategies. They must not.
    """
    base = make_ohlcv(bars=BARS, start="2016-01-01")
    divergent = base.copy()
    holdout_start = int(BARS * 0.8)
    # A violently different holdout: the search must be indifferent to all of it.
    prices = ["Open", "High", "Low", "Close"]
    divergent.loc[divergent.index[holdout_start:], prices] *= 3.0

    settings = GaSettings(population=8, generations=2)
    first = evolve(a_chassis(), _wrap(base), settings=settings, seed=13)
    second = evolve(a_chassis(), _wrap(divergent), settings=settings, seed=13)

    assert first.strategy_yaml == second.strategy_yaml
    assert first.composition == second.composition
    assert first.distinct_configurations == second.distinct_configurations
    # ... and the holdout numbers, which are the only thing that may differ, did.
    assert first.holdout_metrics.total_return_pct != second.holdout_metrics.total_return_pct


def test_evolution_is_reproducible_end_to_end() -> None:
    settings = GaSettings(population=8, generations=2)
    first = evolve(a_chassis(), a_market(), settings=settings, seed=21)
    second = evolve(a_chassis(), a_market(), settings=settings, seed=21)

    assert first.strategy_yaml == second.strategy_yaml
    assert first.holdout_metrics == second.holdout_metrics
    assert first.distinct_configurations == second.distinct_configurations


def test_an_unreachable_trade_floor_is_refused_rather_than_answered() -> None:
    """Every candidate infeasible means there is no result, not a result nobody may believe."""
    with pytest.raises(EvolutionError, match="closed trades required"):
        evolve(
            a_chassis(),
            a_market(),
            settings=GaSettings(population=4, generations=1),
            seed=1,
            trade_floor=TradeFloor(minimum=100_000, per_year=0.0),
        )


def _wrap(frame: pd.DataFrame) -> MarketData:
    return MarketData(
        ticker="TEST",
        frame=frame,
        requested_start=date(2016, 1, 1),
        requested_end=date(2020, 1, 1),
    )


def _block_index(name: str) -> int:
    return next(index for index, block in enumerate(BLOCKS) if block.name == name)


def test_a_stop_kind_maps_onto_the_field_the_schema_expects() -> None:
    for kind, field in (
        ("fixed", "stop_loss_pct"),
        ("trailing", "trailing_stop_pct"),
        ("atr", "atr_stop_multiplier"),
    ):
        genome = Genome(
            entry_a=Slot(block=0, values=tuple(gene.low for gene in BLOCKS[0].genes)),
            entry_b=None,
            combinator="&",
            exit_condition=None,
            stop=Stop(kind=kind, value=5.0),
            take_profit_pct=None,
            max_holding_days=None,
            min_holding_days=None,
        )
        strategy = render(repair(genome), a_chassis())
        assert getattr(strategy.exit, field) == 5.0
