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
from cracktrade.evolution import DEFAULT_HOLDOUT_FRACTION, DEFAULT_SEGMENTS, GaSettings
from cracktrade.optimize.objective import DEFAULT_OBJECTIVE, DEFAULT_TRADE_FLOOR, OBJECTIVES
from cracktrade.validate.folds import DEFAULT_FOLDS, FoldScheme

#: Largest search a single request may ask for. Not a safety rail against the engine -- it will
#: happily run longer -- but against a typo turning one queued run into a machine busy for days.
MAX_EPOCHS = 200
MAX_FOLDS = 20

#: Ceilings on the evolutionary search. ``population * generations`` is the trial count the
#: deflated Sharpe divides by (spec section 16.6), so an oversized search does not merely take
#: longer -- it raises the bar its own result has to clear. These bound the typo, not the
#: method: 200 x 200 is forty thousand configurations, which is already a very high bar.
MAX_POPULATION = 200
MAX_GENERATIONS = 200
MAX_SEGMENTS = 12

#: Fewer than four segments leaves the overfitting check with nothing to partition, so this is
#: a bound on the *method* rather than on a typo. See :func:`_evolution_params`.
MIN_SEGMENTS = 4

#: The holdout has to be big enough to support a conclusion and small enough to leave a search
#: something to work with. The engine refuses a holdout below its own bar count; these are the
#: proportions either side of that.
MIN_HOLDOUT_FRACTION = 0.1
MAX_HOLDOUT_FRACTION = 0.5

#: The engine's own defaults, read rather than restated (spec section 15.3).
DEFAULT_GA = GaSettings()

#: Ceilings on the trade floor. Not engine limits either -- the engine accepts any non-negative
#: floor -- but a floor no candidate can clear turns a search into an expensive way to learn
#: that every candidate was infeasible, and these bound the typo that causes it.
MAX_MIN_TRADES = 1000
MAX_MIN_TRADES_PER_YEAR = 500.0


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

    min_trades = int(params.get("min_trades", DEFAULT_TRADE_FLOOR.minimum))
    if not 0 <= min_trades <= MAX_MIN_TRADES:
        raise ValidationFailedError(f"min_trades must be between 0 and {MAX_MIN_TRADES}")

    per_year = float(params.get("min_trades_per_year", DEFAULT_TRADE_FLOOR.per_year))
    if not 0.0 <= per_year <= MAX_MIN_TRADES_PER_YEAR:
        raise ValidationFailedError(
            f"min_trades_per_year must be between 0 and {MAX_MIN_TRADES_PER_YEAR:g}"
        )

    floor = {"min_trades": min_trades, "min_trades_per_year": per_year}

    if kind is RunKind.EVOLVE:
        return {"objective": objective, **floor, **_evolution_params(params)}

    epochs = int(params.get("epochs", 10))
    if not 1 <= epochs <= MAX_EPOCHS:
        raise ValidationFailedError(f"epochs must be between 1 and {MAX_EPOCHS}")

    if kind is RunKind.OPTIMIZE:
        return {
            "objective": objective,
            "epochs": epochs,
            **floor,
            "cache": bool(params.get("cache", True)),
        }

    folds = int(params.get("folds", DEFAULT_FOLDS))
    if not 2 <= folds <= MAX_FOLDS:
        raise ValidationFailedError(f"folds must be between 2 and {MAX_FOLDS}")
    scheme = str(params.get("scheme", FoldScheme.ANCHORED.value))
    if scheme not in {member.value for member in FoldScheme}:
        raise ValidationFailedError(f"unknown fold scheme {scheme!r}")

    return {"objective": objective, "epochs": epochs, **floor, "folds": folds, "scheme": scheme}


def _evolution_params(params: dict[str, Any]) -> dict[str, Any]:
    """The search settings an evolution launch asked for.

    ``GaSettings`` validates most of this itself and would raise in the worker, where the
    failure would be recorded as an engine fault rather than as the bad request it is. The
    bounds here are the API's, and one of them is stricter than the engine's on purpose --
    see ``segments``.
    """
    population = int(params.get("population", DEFAULT_GA.population))
    if not 2 <= population <= MAX_POPULATION:
        raise ValidationFailedError(f"population must be between 2 and {MAX_POPULATION}")

    generations = int(params.get("generations", DEFAULT_GA.generations))
    if not 1 <= generations <= MAX_GENERATIONS:
        raise ValidationFailedError(f"generations must be between 1 and {MAX_GENERATIONS}")

    # The engine accepts a single segment; this refuses fewer than four. CSCV splits the
    # segments into halves every possible way, and below four there is no way to split them at
    # all -- the probability of backtest overfitting comes back uncomputed, and an uncomputed
    # check is reported as failed. A run that cannot pass its own overfitting check is not a
    # cheaper run, it is a wasted one, so it is refused at launch rather than at the verdict.
    segments = int(params.get("segments", DEFAULT_SEGMENTS))
    if not MIN_SEGMENTS <= segments <= MAX_SEGMENTS:
        raise ValidationFailedError(
            f"segments must be between {MIN_SEGMENTS} and {MAX_SEGMENTS}: the overfitting "
            f"check needs at least {MIN_SEGMENTS} to have anything to partition"
        )

    holdout = float(params.get("holdout_fraction", DEFAULT_HOLDOUT_FRACTION))
    if not MIN_HOLDOUT_FRACTION <= holdout <= MAX_HOLDOUT_FRACTION:
        raise ValidationFailedError(
            f"holdout_fraction must be between {MIN_HOLDOUT_FRACTION:g} and "
            f"{MAX_HOLDOUT_FRACTION:g}"
        )

    return {
        "population": population,
        "generations": generations,
        "segments": segments,
        "holdout_fraction": holdout,
        "cache": bool(params.get("cache", True)),
    }


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

    if run.kind is RunKind.EVOLVE:
        checks = result.get("checks") or []
        holdout = result.get("holdout_metrics") or {}
        # An evolution run's headline is its holdout, and the trade floor governs it exactly as
        # it governs a backtest's: below the floor the figures are omitted from the payload
        # rather than sent with a flag asking politely that they not be rendered.
        withheld = holdout.get("has_enough_trades_to_judge") is False
        summary: dict[str, Any] = {
            "composition": result.get("composition"),
            "trials": result.get("distinct_configurations"),
            "trades": holdout.get("total_trades"),
            "is_credible": result.get("is_credible"),
            "failed_checks": sum(1 for check in checks if not check.get("passed")),
            "suppressed": withheld,
            "trade_floor": MIN_TRADES_TO_JUDGE,
        }
        if not withheld:
            summary |= {
                "holdout_return_pct": holdout.get("total_return_pct"),
                "benchmark_return_pct": (
                    (result.get("benchmark") or {}).get("benchmark") or {}
                ).get("total_return_pct"),
                "max_drawdown_pct": holdout.get("max_drawdown_pct"),
                "profitable_segments": result.get("profitable_segments"),
            }
        return summary

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
        summary = {
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

    An evolution run has neither, and gets an empty list on purpose. Its chassis contributed a
    ticker and some costs; every indicator and both signals are new. A diff would render that
    as a wall of additions against a base the run never treated as a starting point, which
    reads as "look how much was changed" when the truth is "none of this was there". The run
    view shows the composed configuration whole instead.
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
    """Whether this run has a winning configuration to promote.

    An evolution run always does, and its configuration is the *only* place that composition
    exists: the chassis it ran against holds a ticker and a set of costs, not the strategy the
    search built. Promoting is how an evolved strategy becomes a thing that can be edited,
    backtested and validated on its own terms.
    """
    return run.status is RunStatus.SUCCEEDED and run.kind in {
        RunKind.OPTIMIZE,
        RunKind.WALK_FORWARD,
        RunKind.EVOLVE,
    }


#: What each promotable kind is called in a default strategy name.
_ABBREVIATION: dict[RunKind, str] = {
    RunKind.OPTIMIZE: "opt",
    RunKind.WALK_FORWARD: "wf",
    RunKind.EVOLVE: "evo",
}


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
