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
    DEFAULT_OBJECTIVE,
    INFEASIBLE,
    OBJECTIVES,
    Objective,
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
    "DEFAULT_OBJECTIVE",
    "INFEASIBLE",
    "LIST_SECTIONS",
    "OBJECTIVES",
    "POPSIZE",
    "SINGLE_SECTIONS",
    "Objective",
    "Parameter",
    "SearchDiagnostics",
    "SearchOutcome",
    "Split",
    "TestWindow",
    "TrainWindow",
    "discover_parameters",
    "evaluation_budget",
    "get_objective",
    "inject",
    "optimize",
    "run_search",
    "split",
]
