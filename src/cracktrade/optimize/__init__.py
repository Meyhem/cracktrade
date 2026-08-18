"""Parameter search: discovery, objectives, the DE driver, and the train/test protocol."""

from __future__ import annotations

from cracktrade.optimize.discovery import (
    LIST_SECTIONS,
    SINGLE_SECTIONS,
    Parameter,
    discover_parameters,
    inject,
)
from cracktrade.optimize.objective import (
    DEFAULT_MIN_TRADES,
    DEFAULT_MIN_TRADES_PER_YEAR,
    DEFAULT_OBJECTIVE,
    DEFAULT_TRADE_FLOOR,
    INFEASIBLE,
    OBJECTIVES,
    Objective,
    ScoreFunction,
    TradeFloor,
    get_objective,
)
from cracktrade.optimize.runner import optimize
from cracktrade.optimize.search import (
    POPSIZE,
    SearchDiagnostics,
    SearchOutcome,
    evaluation_budget,
    run_search,
)
from cracktrade.optimize.windows import Split, TestWindow, TrainWindow, split

__all__ = [
    "DEFAULT_MIN_TRADES",
    "DEFAULT_MIN_TRADES_PER_YEAR",
    "DEFAULT_OBJECTIVE",
    "DEFAULT_TRADE_FLOOR",
    "INFEASIBLE",
    "LIST_SECTIONS",
    "OBJECTIVES",
    "POPSIZE",
    "SINGLE_SECTIONS",
    "Objective",
    "Parameter",
    "ScoreFunction",
    "SearchDiagnostics",
    "SearchOutcome",
    "Split",
    "TestWindow",
    "TradeFloor",
    "TrainWindow",
    "discover_parameters",
    "evaluation_budget",
    "get_objective",
    "inject",
    "optimize",
    "run_search",
    "split",
]
