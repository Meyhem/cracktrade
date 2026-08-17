"""The verdict as every screen renders it, and the warning a promoted strategy carries.

Nothing here decides anything. The verdict *state* is computed by ``strategy_overview`` in
SQL, and the checks and failures behind it are read straight out of the stored result of the
run that produced them. This module assembles those into the shape a banner needs and formats
a one-line summary -- formatting numbers, never judging them.

That restraint is the point. :class:`cracktrade.domain.ValidationReport` holds the single
definition of the verdict (spec section 12.8), and a second implementation here would be a
second verdict that eventually disagrees with the first in public.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.repos import RunRepo, StrategyRepo
from cracktrade.api.repos.rows import RunRow, StrategyOverviewRow, Verdict


@dataclass(frozen=True, slots=True)
class Check:
    """One robustness check as the "Every check" table renders it."""

    name: str
    label: str
    passed: bool
    plain: str
    stat: str
    detail: str


@dataclass(frozen=True, slots=True)
class VerdictBlock:
    """The verdict banner: the state, what produced it, and what failed.

    ``run_id`` is ``None`` for ``unvalidated`` and ``never_run`` -- those states are the
    *absence* of a walk-forward against the head, so there is nothing to point at.
    """

    state: Verdict
    run_id: UUID | None
    run_number: int | None
    version: int | None
    finished_at: str | None
    summary: str | None
    failures: tuple[str, ...]
    checks: tuple[Check, ...]
    meta: str | None


def checks_of(result: dict[str, Any] | None) -> tuple[Check, ...]:
    """The structured checks a walk-forward result carries.

    Read from the stored result, whatever version of the engine wrote it. Missing keys default
    rather than raise: an old result with fewer fields should render as much as it has, not
    take a page down.
    """
    raw = (result or {}).get("checks") or []
    return tuple(
        Check(
            name=str(entry.get("name", "")),
            label=str(entry.get("label", "")),
            passed=bool(entry.get("passed")),
            plain=str(entry.get("plain", "")),
            stat=str(entry.get("stat", "")),
            detail=str(entry.get("detail", "")),
        )
        for entry in raw
        if isinstance(entry, dict)
    )


def _summary(result: dict[str, Any]) -> str:
    """One line of the numbers behind the verdict. Formatting only."""
    combined = result.get("combined_return_pct")
    benchmark = (result.get("benchmark") or {}).get("total_return_pct")
    profitable = result.get("profitable_folds")
    total = len(result.get("folds") or [])

    parts: list[str] = []
    if isinstance(combined, int | float) and isinstance(benchmark, int | float):
        parts.append(f"out-of-sample {combined:+.1f}% vs {benchmark:+.1f}% buy-and-hold")
    if isinstance(profitable, int) and total:
        parts.append(f"profitable in {profitable}/{total} folds")
    return ", ".join(parts)


def _meta(run: RunRow, result: dict[str, Any]) -> str:
    folds = len(result.get("folds") or [])
    scheme = result.get("scheme") or run.params.get("scheme")
    objective = result.get("objective") or run.params.get("objective")
    return " · ".join(str(part) for part in (f"{folds} folds", scheme, objective) if part)


def block(work: UnitOfWork, overview: StrategyOverviewRow) -> VerdictBlock:
    """The verdict banner for one strategy.

    The state comes from the view, which applies the head-only rule (spec section 14.4): a
    credible walk-forward against a version that has since been edited describes a config that
    no longer exists, so the strategy reads as unvalidated rather than credible.
    """
    if overview.verdict_run_id is None:
        return VerdictBlock(
            state=overview.verdict,
            run_id=None,
            run_number=None,
            version=None,
            finished_at=None,
            summary=None,
            failures=(),
            checks=(),
            meta=None,
        )

    run = RunRepo(work.connection).require(overview.verdict_run_id)
    result = run.result or {}
    failures = tuple(str(entry) for entry in result.get("failures") or [])

    return VerdictBlock(
        state=overview.verdict,
        run_id=run.id,
        run_number=run.number,
        version=run.version,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        summary=_summary(result) or None,
        failures=failures,
        checks=checks_of(result),
        meta=_meta(run, result),
    )


@dataclass(frozen=True, slots=True)
class PromotedWarning:
    """Why a promoted strategy is still flagged, in the terms the banner needs."""

    origin_run_id: UUID
    origin_run_number: int
    parent_name: str | None
    text: str


def promoted_warning(work: UnitOfWork, overview: StrategyOverviewRow) -> PromotedWarning | None:
    """The banner a promoted strategy shows until its own validation passes.

    Derived, never stored. Two facts have to hold: the snapshot taken at promotion time said
    the source was not credible, and this strategy has not since produced a credible
    walk-forward against its own current head. Only the second can change, and only forwards --
    the snapshot records something that was true when it was taken, so editing the past does
    not clear the warning and neither does the source run's strategy later being validated.
    """
    if not overview.origin_not_credible or overview.verdict is Verdict.CREDIBLE:
        return None

    origin_run_id = overview.origin_run_id
    # `origin_not_credible` is settable only on a promoted strategy, and the lineage CHECK
    # requires a promoted strategy to name the run it came from.
    assert origin_run_id is not None

    origin = RunRepo(work.connection).require(origin_run_id)
    parent = (
        StrategyRepo(work.connection).get(overview.parent_strategy_id)
        if overview.parent_strategy_id
        else None
    )

    source = f"{parent.name} #{origin.number}" if parent else f"run #{origin.number}"
    return PromotedWarning(
        origin_run_id=origin_run_id,
        origin_run_number=origin.number,
        parent_name=parent.name if parent else None,
        text=(
            f"Promoted from {source}, which had not been shown to be credible. These parameters "
            f"came out of a search, and a search that has not been validated out of sample has "
            f"not been shown to have found anything. Run a walk-forward against this strategy "
            f"before treating its numbers as evidence."
        ),
    )
