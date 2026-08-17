"""Turning a run's winning configuration into a strategy of its own.

Promotion is the one workflow that creates a strategy out of a *result*, and it is where the
project's central honesty rule gets its sharpest test: the numbers that make a promoted
strategy attractive were produced by a search, and a search that has not been validated
out of sample has not been shown to have found anything. So the verdict travels with the
configuration (spec section 14.7) -- the new strategy carries a snapshot saying its source was
not credible, and only its *own* passing walk-forward clears it.

Everything here happens in one transaction: strategy, v1, and a queued backtest. A promoted
strategy sitting on screen with no numbers at all invites the user to supply the missing
numbers from memory of the run it came from, which are numbers about a different config.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import ConflictError, InvariantViolationError
from cracktrade.api.repos.rows import RunKind, RunOverviewRow, RunRow, RunStatus, VersionRow
from cracktrade.api.services.runs import launch, promote_name, require_run
from cracktrade.api.services.strategies import create_promoted


def carries_warning(run: RunRow) -> bool:
    """Whether a strategy promoted from this run starts life flagged as unvalidated.

    The bar is deliberately high: only a walk-forward that *itself* returned a credible verdict
    clears it. Promoting an optimize result always warns, even when the parent strategy's own
    walk-forward passed -- the optimization moved the parameters, so the run that passed
    measured different values than the ones being promoted. A verdict is about a configuration,
    not about a lineage.
    """
    return not (run.kind is RunKind.WALK_FORWARD and run.is_credible is True)


def winning_yaml(run: RunRow) -> str:
    """The configuration the search settled on, as the engine serialised it.

    An optimize and a walk-forward both carry ``optimized_yaml``; a backtest has nothing to
    promote, which :func:`require_promotable` has already refused by the time this is called.
    """
    result = run.result or {}
    yaml_text = result.get("optimized_yaml")
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        # A succeeded optimize or walk-forward without a winning config is not a bad request:
        # it is a result the engine should never have produced, so it is reported as the
        # server's problem rather than the client's.
        raise InvariantViolationError(
            f"run {run.id} is a succeeded {run.kind.value} but carries no optimized_yaml"
        )
    return yaml_text


def require_promotable(work: UnitOfWork, run_id: UUID) -> RunOverviewRow:
    """The run, if it has a winning configuration to promote. 409 if it has not."""
    row = require_run(work, run_id)
    run = row.run
    if run.kind is RunKind.BACKTEST:
        raise ConflictError(
            "a backtest has nothing to promote: it runs the configuration exactly as written"
        )
    if run.status is not RunStatus.SUCCEEDED:
        raise ConflictError(f"run {run.number} is {run.status.value}, so it has no winning config")
    return row


@dataclass(frozen=True, slots=True)
class Promoted:
    """What a promotion produced: a strategy, its v1, and the backtest already queued for it."""

    strategy_id: UUID
    version: VersionRow
    backtest: RunRow
    carried_warning: bool


def promote(work: UnitOfWork, *, run_id: UUID, name: str | None = None) -> Promoted:
    """Create a strategy from a run's winning configuration and queue its first backtest."""
    row = require_promotable(work, run_id)
    run = row.run
    chosen = name or promote_name(row)

    created = create_promoted(
        work,
        name=chosen,
        yaml_text=winning_yaml(run),
        parent_strategy_id=run.strategy_id,
        origin_run_id=run.id,
        origin_not_credible=carries_warning(run),
        note=f"promoted from {row.strategy_name} #{run.number} ({run.kind.value})",
    )
    backtest = launch(work, strategy_id=created.strategy.id, kind=RunKind.BACKTEST)

    return Promoted(
        strategy_id=created.strategy.id,
        version=created.version,
        backtest=backtest,
        carried_warning=created.strategy.origin_not_credible,
    )
