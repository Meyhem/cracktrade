"""Composing strategies by evolution, rather than tuning one the user already wrote.

Normative reference: ``docs/ENGINE_SPEC.md`` section 16.

Section 9 optimizes a strategy someone believed in. This package builds one from nothing: the
user names a ticker and a genetic algorithm composes conditions from a curated block library,
choosing structure and numbers together.

That is a strictly harder problem to report honestly, and the package is shaped around the
reporting rather than around the search. Structure search reaches far more distinct strategies
than parameter search does, and the maximum of enough draws looks impressive on a random walk;
so the trial count is exact, the holdout is untouchable, and the verdict is the same strict
conjunction of section 12 checks the walk-forward report uses.
"""

from __future__ import annotations

from cracktrade.evolution.blocks import BLOCKS, library_warmup
from cracktrade.evolution.genome import Chassis, Genome, Slot, Stop, describe, render, repair
from cracktrade.evolution.protocol import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_MIN_SEGMENT_BARS,
    DEFAULT_SEGMENTS,
    Fitness,
    configuration_digest,
    split_for_evolution,
)
from cracktrade.evolution.runner import evolve
from cracktrade.evolution.search import (
    DEFAULT_GENERATIONS,
    DEFAULT_POPULATION,
    GaSettings,
    crossover,
    mutate,
    random_genome,
    run_evolution,
)

__all__ = [
    "BLOCKS",
    "DEFAULT_GENERATIONS",
    "DEFAULT_HOLDOUT_FRACTION",
    "DEFAULT_MIN_SEGMENT_BARS",
    "DEFAULT_POPULATION",
    "DEFAULT_SEGMENTS",
    "Chassis",
    "Fitness",
    "GaSettings",
    "Genome",
    "Slot",
    "Stop",
    "configuration_digest",
    "crossover",
    "describe",
    "evolve",
    "library_warmup",
    "mutate",
    "random_genome",
    "render",
    "repair",
    "run_evolution",
    "split_for_evolution",
]
