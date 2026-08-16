"""Creating strategies, and appending to their histories.

Every workflow here is one transaction. A strategy without its v1 is a row the UI cannot
render, so the two are written together or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cracktrade.api.db.uow import UnitOfWork
from cracktrade.api.errors import ConflictError, NotFoundError, ValidationFailedError
from cracktrade.api.repos import StrategyRepo, VersionRepo
from cracktrade.api.repos.rows import (
    StrategyOrigin,
    StrategyOverviewRow,
    StrategyRow,
    Verdict,
    VersionOrigin,
    VersionRow,
)
from cracktrade.api.services.config import field_issue, strategy_from
from cracktrade.api.services.diff import summarise
from cracktrade.config import Strategy, dump_strategy
from cracktrade.errors import ConfigError, StrategyValidationError

#: A blank strategy still has to satisfy the engine: one entry rule, one exit rule with a way
#: out. Offered as the "minimal config" seed so that a new strategy is runnable immediately
#: rather than invalid until the user guesses what is missing.
MINIMAL_INDICATORS: tuple[dict[str, Any], ...] = (
    {"name": "sma_long", "type": "sma", "window": 200},
    {"name": "rsi_ind", "type": "rsi", "window": 14},
)


def _config_of(strategy: Strategy) -> dict[str, Any]:
    return dict(strategy.model_dump(mode="json"))


def seed_config(*, name: str, ticker: str, start: str, end: str, minimal: bool) -> dict[str, Any]:
    """The configuration a newly created strategy starts from."""
    indicators = list(MINIMAL_INDICATORS) if minimal else []
    entry = "(close > sma_long) & (rsi_ind < 35)" if minimal else "close > open"
    return {
        "strategy": {"name": name},
        "universe": {"ticker": ticker, "start_date": start, "end_date": end},
        "execution": {
            "initial_capital": 10000.0,
            "slippage_pct": 0.1,
            "commission_pct": 0.05,
            "risk_free_rate": 0.04,
        },
        "indicators": indicators,
        "entry": {"signal": entry},
        "exit": {"stop_loss_pct": 5.0, "take_profit_pct": 12.0},
    }


@dataclass(frozen=True, slots=True)
class Created:
    """A new strategy and the version created with it."""

    strategy: StrategyRow
    version: VersionRow


def _validated(data: dict[str, Any] | None, text: str | None) -> Strategy:
    """Validate, translating the engine's refusal into a field-addressed API failure."""
    try:
        return strategy_from(data, text)
    except StrategyValidationError as error:
        raise ValidationFailedError(
            "the configuration is invalid", [field_issue(issue) for issue in error.issues]
        ) from error
    except ConfigError as error:
        raise ValidationFailedError(str(error)) from error


def _create(
    work: UnitOfWork,
    *,
    strategy: Strategy,
    name: str,
    origin: StrategyOrigin,
    version_origin: VersionOrigin,
    yaml_text: str,
    note: str | None = None,
    parent_strategy_id: UUID | None = None,
    parent_version: int | None = None,
    origin_run_id: UUID | None = None,
    origin_not_credible: bool = False,
) -> Created:
    strategies = StrategyRepo(work.connection)
    versions = VersionRepo(work.connection)

    row = strategies.create(
        name=name,
        origin=origin,
        parent_strategy_id=parent_strategy_id,
        parent_version=parent_version,
        origin_run_id=origin_run_id,
        origin_not_credible=origin_not_credible,
    )
    version = versions.append(
        strategy_id=row.id,
        origin=version_origin,
        config=_config_of(strategy),
        config_yaml=yaml_text,
        note=note,
    )
    return Created(strategy=row, version=version)


def create_strategy(
    work: UnitOfWork,
    *,
    name: str,
    ticker: str,
    start_date: str,
    end_date: str,
    minimal: bool = True,
) -> Created:
    """Create a strategy from the New dialog. No run is launched: it starts never-run."""
    strategy = _validated(
        seed_config(name=name, ticker=ticker, start=start_date, end=end_date, minimal=minimal), None
    )
    return _create(
        work,
        strategy=strategy,
        name=name,
        origin=StrategyOrigin.AUTHORED,
        version_origin=VersionOrigin.CREATED,
        yaml_text=dump_strategy(strategy),
    )


def import_strategy(work: UnitOfWork, *, yaml_text: str, filename: str | None = None) -> Created:
    """Create a strategy from an uploaded file.

    The YAML is stored **as written**, not re-dumped. Importing a file and reading it back
    should not silently reformat the user's own document; the canonical form is available from
    ``config`` whenever a caller wants it.
    """
    strategy = _validated(None, yaml_text)
    return _create(
        work,
        strategy=strategy,
        name=strategy.strategy.name,
        origin=StrategyOrigin.IMPORTED,
        version_origin=VersionOrigin.IMPORTED,
        yaml_text=yaml_text,
        note=f"imported from {filename}" if filename else "imported",
    )


def fork_strategy(
    work: UnitOfWork, *, source_id: UUID, name: str, version: int | None = None
) -> Created:
    """Copy a strategy at some version into a new one.

    A fork starts a fresh history at v1 and does not inherit the parent's versions. It records
    which version it came from, so the lineage is a fact rather than a memory.
    """
    versions = VersionRepo(work.connection)
    StrategyRepo(work.connection).require(source_id)
    source = (
        versions.require_head(source_id)
        if version is None
        else versions.require(source_id, version)
    )

    renamed = dict(source.config)
    renamed["strategy"] = {**renamed.get("strategy", {}), "name": name}
    strategy = _validated(renamed, None)

    return _create(
        work,
        strategy=strategy,
        name=name,
        origin=StrategyOrigin.FORKED,
        version_origin=VersionOrigin.FORKED,
        yaml_text=dump_strategy(strategy),
        note=f"forked from v{source.version}",
        parent_strategy_id=source_id,
        parent_version=source.version,
    )


def save_version(
    work: UnitOfWork,
    *,
    strategy_id: UUID,
    base_version: int,
    config: dict[str, Any] | None = None,
    yaml_text: str | None = None,
    note: str | None = None,
) -> VersionRow:
    """Append an edited configuration as the next version.

    Optimistic concurrency: the caller states which version it edited, and the save is refused
    if the head has moved. Last-write-wins on an append-only history would lose someone's edit
    while reporting success, which is the worst of both.

    A save that changes nothing is refused too. Versions are the unit in which runs are made
    comparable, and one that differs from its parent in no respect would only add noise to a
    history whose job is to explain results.
    """
    versions = VersionRepo(work.connection)
    head = versions.require_head(strategy_id)
    if head.version != base_version:
        raise ConflictError(
            f"this edit was based on v{base_version}, but the head is now v{head.version}. "
            f"Reload and reapply the change."
        )

    strategy = _validated(config, yaml_text)
    new_config = _config_of(strategy)
    if not summarise(head.config, new_config):
        raise ConflictError("nothing changed, so no version was created")

    return versions.append(
        strategy_id=strategy_id,
        origin=VersionOrigin.EDITED,
        config=new_config,
        config_yaml=yaml_text if yaml_text is not None else dump_strategy(strategy),
        note=note,
    )


def restore_version(
    work: UnitOfWork, *, strategy_id: UUID, version: int, note: str | None = None
) -> VersionRow:
    """Append a copy of an older version at the head.

    Nothing is rewound and nothing is deleted. Runs against the previous head stay attached to
    it and simply become stale, which is the honest record: they did happen, against a config
    that is no longer current.
    """
    versions = VersionRepo(work.connection)
    head = versions.require_head(strategy_id)
    target = versions.require(strategy_id, version)
    if target.version == head.version:
        raise ConflictError(f"v{version} is already the head")

    return versions.append(
        strategy_id=strategy_id,
        origin=VersionOrigin.RESTORED,
        config=target.config,
        config_yaml=target.config_yaml,
        note=note or f"restored the complete config of v{version}",
        restored_from=version,
    )


def require_strategy(work: UnitOfWork, strategy_id: UUID) -> StrategyRow:
    """The strategy, or a 404 naming it."""
    found = StrategyRepo(work.connection).get(strategy_id)
    if found is None:
        raise NotFoundError(f"no strategy {strategy_id}")
    return found


@dataclass(frozen=True, slots=True)
class StrategyListing:
    """The list screen's rows and the totals behind its filter chips."""

    rows: tuple[StrategyOverviewRow, ...]
    totals: dict[str, int]


def list_strategies(
    work: UnitOfWork, *, search: str | None = None, verdict: str | None = None
) -> StrategyListing:
    """The strategy list, filtered as the header's search box and chips ask."""
    repo = StrategyRepo(work.connection)
    chosen = Verdict(verdict) if verdict else None
    rows = repo.list_overview(search=search, verdict=chosen)
    counts = repo.count_by_verdict()
    return StrategyListing(
        rows=tuple(rows),
        totals={"all": sum(counts.values()), **{k.value: v for k, v in counts.items()}},
    )


@dataclass(frozen=True, slots=True)
class StrategyDetails:
    """Everything the detail header and the config tab render."""

    strategy: StrategyRow
    overview: StrategyOverviewRow
    head: VersionRow


def strategy_details(work: UnitOfWork, strategy_id: UUID) -> StrategyDetails:
    """The strategy, its derived overview, and its head version."""
    row = require_strategy(work, strategy_id)
    overview = StrategyRepo(work.connection).overview(strategy_id)
    head = VersionRepo(work.connection).require_head(strategy_id)
    # A strategy is always written with its v1, so a head implies an overview row.
    assert overview is not None
    return StrategyDetails(strategy=row, overview=overview, head=head)
