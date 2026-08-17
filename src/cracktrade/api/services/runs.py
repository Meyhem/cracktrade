"""Launching runs, and reading them back.

Launching writes a queued row and commits; nothing here executes anything. The worker is a
separate process (spec section 14.5) precisely so a twenty-five minute walk-forward cannot sit
inside a request.

Reading is mostly passing the engine's own serialised result through untouched (section 15.1).
The one thing this module derives is the *headline* -- the handful of columns a run table
prints -- and it does that by reading the stored result rather than recomputing anything.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import yaml

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import ConflictError, NotFoundError, ValidationFailedError
from cracktrade.api.repos import RunRepo, SeriesRepo, VersionRepo
from cracktrade.api.repos.rows import RunKind, RunOverviewRow, RunRow, RunStatus
from cracktrade.api.services.config import strategy_from
from cracktrade.api.services.diff import summarise
from cracktrade.api.services.verdict import Check, checks_of
from cracktrade.config import dump_strategy
from cracktrade.domain import MIN_TRADES_TO_JUDGE
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, OBJECTIVES
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme

#: Largest search a single request may ask for. Not a safety rail against the engine -- it will
#: happily run longer -- but against a typo turning one queued run into a machine busy for days.
MAX_EPOCHS = 200
MAX_FOLDS = 20


def _normalised_params(kind: RunKind, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and complete a launch request's parameters.

    Rejected here rather than in the worker: a run that cannot possibly execute should never
    reach the queue, where its failure would look like an engine problem.
    """
    if kind is RunKind.BACKTEST:
        if params:
            raise ValidationFailedError(
                "a backtest takes no parameters: it runs the config exactly as written"
            )
        return {}

    objective = str(params.get("objective", DEFAULT_OBJECTIVE))
    if objective not in OBJECTIVES:
        raise ValidationFailedError(
            f"unknown objective {objective!r}; choose one of {', '.join(OBJECTIVES)}"
        )

    epochs = int(params.get("epochs", 10))
    if not 1 <= epochs <= MAX_EPOCHS:
        raise ValidationFailedError(f"epochs must be between 1 and {MAX_EPOCHS}")

    if kind is RunKind.OPTIMIZE:
        return {"objective": objective, "epochs": epochs, "cache": bool(params.get("cache", True))}

    folds = int(params.get("folds", DEFAULT_FOLDS))
    if not 2 <= folds <= MAX_FOLDS:
        raise ValidationFailedError(f"folds must be between 2 and {MAX_FOLDS}")
    scheme = str(params.get("scheme", FoldScheme.ANCHORED.value))
    if scheme not in {member.value for member in FoldScheme}:
        raise ValidationFailedError(f"unknown fold scheme {scheme!r}")

    return {"objective": objective, "epochs": epochs, "folds": folds, "scheme": scheme}


def launch(
    work: UnitOfWork,
    *,
    strategy_id: UUID,
    kind: RunKind,
    params: dict[str, Any] | None = None,
    seed: int | None = None,
) -> RunRow:
    """Queue a run against the strategy's current head.

    The version is pinned here rather than taken from the request. A client cannot ask to run
    an old version: a run describes what the strategy *is*, and the head at launch time is the
    only answer that stays true about which config produced the numbers.

    A seed is recorded even when the caller does not supply one, because reproducibility is a
    spec-level requirement (defect D12) and "whatever the default was that day" is not one.
    """
    head = VersionRepo(work.connection).require_head(strategy_id)
    return RunRepo(work.connection).create(
        strategy_id=strategy_id,
        version=head.version,
        kind=kind,
        params=_normalised_params(kind, params or {}),
        # 31 bits: comfortably inside every seed API the engine touches, including numpy's.
        seed=seed if seed is not None else secrets.randbelow(2**31),
    )


def require_run(work: UnitOfWork, run_id: UUID) -> RunOverviewRow:
    """One run with its strategy name and staleness, or a 404."""
    found = RunRepo(work.connection).overview(run_id)
    if found is None:
        raise NotFoundError(f"no run {run_id}")
    return found


@dataclass(frozen=True, slots=True)
class RunListing:
    """A page of runs, and how many matched in total."""

    rows: tuple[RunOverviewRow, ...]
    total: int


def list_runs(
    work: UnitOfWork,
    *,
    strategy_id: UUID | None = None,
    kinds: tuple[RunKind, ...] | None = None,
    statuses: tuple[RunStatus, ...] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RunListing:
    """One query behind the run tabs, the all-runs page, the queue and the chart picker."""
    repo = RunRepo(work.connection)
    return RunListing(
        rows=tuple(
            repo.list_overview(
                strategy_id=strategy_id,
                kinds=kinds,
                statuses=statuses,
                limit=limit,
                offset=offset,
            )
        ),
        total=repo.count(strategy_id=strategy_id, kinds=kinds, statuses=statuses),
    )


def request_cancellation(work: UnitOfWork, run_id: UUID) -> RunRow:
    """Ask a live run to stop.

    A queued run is ended immediately -- nothing has started, so there is nothing to ask. A
    running one is flagged, and its worker observes the flag between generations or folds.
    """
    repo = RunRepo(work.connection)
    run = repo.get(run_id)
    if run is None:
        raise NotFoundError(f"no run {run_id}")
    if run.status.is_terminal:
        raise ConflictError(f"run {run.number} has already finished")
    if run.status is RunStatus.QUEUED:
        return repo.cancel(run_id)
    return repo.request_cancel(run_id)


@dataclass(frozen=True, slots=True)
class RunDetails:
    """One run's page: the run, its verbatim result, and the two things derived from it.

    ``checks`` and ``moves`` are assembled here rather than left to the client so that the
    "Every check" table and the Resulting-config pane read the same stored result the headline
    does. A client deriving either itself would be a second implementation of the verdict.
    """

    row: RunOverviewRow
    checks: tuple[Check, ...]
    moves: tuple[ParameterMove, ...]
    default_promote_name: str | None


def run_details(work: UnitOfWork, run_id: UUID) -> RunDetails:
    """Everything the run page renders."""
    row = require_run(work, run_id)
    run = row.run
    base = VersionRepo(work.connection).require(run.strategy_id, run.version).config
    return RunDetails(
        row=row,
        checks=checks_of(run.result),
        moves=config_diff(run, base),
        default_promote_name=promote_name(row) if is_promotable(run) else None,
    )


def series_catalog(work: UnitOfWork, run_id: UUID) -> dict[str, list[int]]:
    """Which chart series a run captured, and for which folds."""
    require_run(work, run_id)
    return SeriesRepo(work.connection).catalog(run_id)


def series_points(work: UnitOfWork, run_id: UUID, name: str, fold: int) -> dict[str, Any]:
    """One captured series, exactly as it was stored."""
    require_run(work, run_id)
    return SeriesRepo(work.connection).require(run_id, name, fold).points


# --------------------------------------------------------------------------- headlines


def _suppressed(result: dict[str, Any]) -> bool:
    metrics = result.get("metrics") or {}
    enough = metrics.get("has_enough_trades_to_judge")
    return enough is False


def headline(run: RunRow) -> dict[str, Any] | None:
    """The columns a run table prints, read from the stored result.

    Suppression is honest here too. Below the trade floor the withheld figures are **not
    included**, so a client cannot render a number it was never given -- rather than being sent
    the numbers with a flag politely asking it not to show them (spec section 15.2).
    """
    if run.status is not RunStatus.SUCCEEDED or run.result is None:
        return None
    result = run.result

    if run.kind is RunKind.WALK_FORWARD:
        checks = result.get("checks") or []
        return {
            "folds": len(result.get("folds") or []),
            "scheme": result.get("scheme"),
            "combined_oos_pct": result.get("combined_return_pct"),
            "benchmark_pct": (result.get("benchmark") or {}).get("total_return_pct"),
            "profitable_folds": result.get("profitable_folds"),
            "oos_trades": result.get("total_trades"),
            "is_credible": result.get("is_credible"),
            "failed_checks": sum(1 for check in checks if not check.get("passed")),
        }

    suppressed = _suppressed(result)
    metrics = result.get("metrics") or {}

    if run.kind is RunKind.BACKTEST:
        benchmark = result.get("benchmark") or {}
        summary: dict[str, Any] = {
            "trades": metrics.get("total_trades"),
            "entry_defined_pct": result.get("entry_defined_pct"),
            "suppressed": suppressed,
            "trade_floor": MIN_TRADES_TO_JUDGE,
        }
        if not suppressed:
            summary |= {
                "return_pct": metrics.get("total_return_pct"),
                "benchmark_return_pct": (benchmark.get("benchmark") or {}).get("total_return_pct"),
                "excess_pp": benchmark.get("excess_return_pct"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            }
        return summary

    test_metrics = result.get("test_metrics") or {}
    suppressed = test_metrics.get("has_enough_trades_to_judge") is False
    summary = {
        "trials": result.get("trials"),
        "trades": test_metrics.get("total_trades"),
        "suppressed": suppressed,
        "trade_floor": MIN_TRADES_TO_JUDGE,
    }
    if not suppressed:
        summary |= {
            "oos_return_pct": test_metrics.get("total_return_pct"),
            "improvement_pct": result.get("improvement_pct"),
            "overfitting_gap_pct": result.get("overfitting_gap_pct"),
            "test_cagr_pct": test_metrics.get("cagr_pct"),
        }
    return summary


@dataclass(frozen=True, slots=True)
class ParameterMove:
    """One parameter the search moved, with the range it was allowed to move within.

    ``low`` and ``high`` are ``None`` for a walk-forward, whose result records the winning
    config but not the bounds each fold searched -- the folds re-optimize independently, so
    there is no single range to report. Saying so beats inventing one.
    """

    path: str
    old: Any
    new: Any
    low: float | None
    high: float | None
    at_bound: bool


def config_diff(run: RunRow, base: dict[str, Any]) -> tuple[ParameterMove, ...]:
    """What the winning configuration changed, against the run's own base version.

    An optimize result already carries this as structured ``changes``, bounds included, so it
    is read rather than recomputed. A walk-forward does not, so its winning config is diffed
    against the base -- the same comparison the version diff makes, minus the bounds.
    """
    result = run.result
    if run.status is not RunStatus.SUCCEEDED or result is None:
        return ()

    changes = result.get("changes")
    if isinstance(changes, list):
        return tuple(
            ParameterMove(
                path=str(change.get("path", "")),
                old=change.get("old_value"),
                new=change.get("new_value"),
                low=change.get("low"),
                high=change.get("high"),
                at_bound=bool(change.get("at_bound")),
            )
            for change in changes
            if isinstance(change, dict)
        )

    yaml_text = result.get("optimized_yaml")
    if not isinstance(yaml_text, str):
        return ()
    # The winning config renames itself after the run's strategy; a name is not a parameter.
    return tuple(
        ParameterMove(
            path=change.path, old=change.old, new=change.new, low=None, high=None, at_bound=False
        )
        for change in summarise(_flattened(base), _flattened(yaml_text))
        if not change.path.startswith("strategy.")
    )


def _flattened(config: dict[str, Any] | str) -> dict[str, Any]:
    """A configuration in the shape the engine addresses its parameters by.

    ``model_dump`` collects an indicator's type-specific settings under ``params``, so a window
    is ``indicators.rsi_ind.params.window`` there but ``indicators.rsi_ind.window`` in an
    optimize result's ``changes``. Both sides of this diff go through the YAML form, which is
    the flattened one, so a client gets *one* address per field regardless of which kind of run
    it is looking at.
    """
    strategy = (
        strategy_from(config, None) if isinstance(config, dict) else strategy_from(None, config)
    )
    loaded = yaml.safe_load(dump_strategy(strategy))
    return dict(loaded) if isinstance(loaded, dict) else {}


def is_promotable(run: RunRow) -> bool:
    """Whether this run has a winning configuration to promote."""
    return run.status is RunStatus.SUCCEEDED and run.kind in {
        RunKind.OPTIMIZE,
        RunKind.WALK_FORWARD,
    }


#: What each promotable kind is called in a default strategy name.
_ABBREVIATION: dict[RunKind, str] = {RunKind.OPTIMIZE: "opt", RunKind.WALK_FORWARD: "wf"}


def promote_name(row: RunOverviewRow) -> str:
    """The name the promote dialog offers, e.g. ``momentum_v2_opt22``.

    Only a suggestion, and only ever a suggestion. If it is taken, the unique constraint
    refuses the promotion as a conflict rather than the server quietly picking something else:
    a strategy appearing under a name nobody chose is worse than being asked to choose again.
    """
    return f"{row.strategy_name}_{_ABBREVIATION[row.run.kind]}{row.run.number}"


def parse_kinds(values: str | None) -> tuple[RunKind, ...] | None:
    """Parse a comma-separated ``kind`` filter.

    Unknown members are dropped rather than rejected: a client asking for a kind this version
    does not have should match nothing, not provoke a 500. Parsing lives here rather than in
    the route so that routes never need the storage enums.
    """
    return _parse(values, RunKind)


def parse_statuses(values: str | None) -> tuple[RunStatus, ...] | None:
    """Parse a comma-separated ``status`` filter. Same tolerance as :func:`parse_kinds`."""
    return _parse(values, RunStatus)


def _parse[T: (RunKind, RunStatus)](values: str | None, enum: type[T]) -> tuple[T, ...] | None:
    if not values:
        return None
    members: list[T] = []
    for value in values.split(","):
        try:
            members.append(enum(value.strip()))
        except ValueError:
            continue
    return tuple(members) or None


def kind_named(value: str) -> RunKind:
    """The run kind a request named. The DTO has already restricted it to a known value."""
    return RunKind(value)
