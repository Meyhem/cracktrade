"""Strategies: listing, creating, importing, forking, deleting, and config validation."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from cracktrade.api.dependencies import Work
from cracktrade.api.schemas.requests import (
    ConfigDiffRequest,
    CreateStrategyRequest,
    ForkStrategyRequest,
    ImportStrategyRequest,
    ValidateRequest,
)
from cracktrade.api.schemas.responses import (
    ConfigDiffResponse,
    CreatedStrategy,
    DeletedStrategy,
    Issue,
    StrategyDetail,
    StrategyList,
    StrategySummary,
    ValidateResponse,
    strategy_detail,
)
from cracktrade.api.services.config import (
    compare_configs,
    review_mapping,
    review_yaml,
    strategy_from,
)
from cracktrade.api.services.strategies import (
    Created,
    create_strategy,
    delete_strategy,
    fork_strategy,
    import_strategy,
    strategy_details,
)
from cracktrade.api.services.strategies import (
    list_strategies as list_strategies_service,
)
from cracktrade.errors import ConfigError

router = APIRouter(tags=["strategies"])


def _detail(work: Work, strategy_id: UUID) -> StrategyDetail:
    return strategy_detail(strategy_details(work, strategy_id))


def _created(work: Work, created: Created) -> CreatedStrategy:
    review = review_mapping(created.version.config)
    return CreatedStrategy(
        strategy=_detail(work, created.strategy.id),
        warnings=[Issue.of(warning) for warning in review.warnings],
    )


# --------------------------------------------------------------------------- validation


@router.post("/config/validate", response_model=ValidateResponse)
def validate_config(body: ValidateRequest) -> ValidateResponse:
    """Check a configuration without storing or running anything.

    Always 200: a config the user is halfway through typing is what this exists for, and
    invalidity is the answer rather than a transport failure.
    """
    if (body.config is None) == (body.yaml is None):
        raise ConfigError("supply exactly one of `config` or `yaml`")
    if body.yaml is not None:
        return ValidateResponse.of(review_yaml(body.yaml))
    assert body.config is not None
    return ValidateResponse.of(review_mapping(body.config))


@router.post("/config/diff", response_model=ConfigDiffResponse)
def diff_configs(body: ConfigDiffRequest) -> ConfigDiffResponse:
    """Compare two configurations, neither of which need be a stored version.

    The Promote dialog's diff. ``GET /strategies/{id}/diff`` cannot serve it: that compares two
    versions of one strategy, and the configuration a search produced is not a version of
    anything until the user adopts it. Invalid input is a 4xx here rather than the 200 that
    ``/config/validate`` returns -- there is no half-typed state to support, and a diff against
    a configuration that is not a strategy has nothing to say.
    """
    return ConfigDiffResponse.of(
        compare_configs(
            strategy_from(body.from_.config, body.from_.yaml),
            strategy_from(body.to.config, body.to.yaml),
        )
    )


# --------------------------------------------------------------------------- reading


@router.get("/strategies", response_model=StrategyList)
def list_strategies(
    work: Work,
    search: Annotated[str | None, Query(max_length=100)] = None,
    verdict: Annotated[str | None, Query()] = None,
) -> StrategyList:
    """The list screen. ``search`` matches name or ticker; ``verdict`` is the filter chips."""
    listing = list_strategies_service(work, search=search, verdict=verdict)
    return StrategyList(
        strategies=[StrategySummary.of(row) for row in listing.rows],
        totals=listing.totals,
    )


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy(strategy_id: UUID, work: Work) -> StrategyDetail:
    """The detail header and the head configuration."""
    return _detail(work, strategy_id)


# --------------------------------------------------------------------------- creating


@router.post("/strategies", response_model=CreatedStrategy, status_code=status.HTTP_201_CREATED)
def post_strategy(body: CreateStrategyRequest, work: Work) -> CreatedStrategy:
    """Create a strategy and its v1. No run is launched; it starts never-run."""
    created = create_strategy(
        work,
        name=body.name,
        ticker=body.ticker,
        start_date=body.start_date,
        end_date=body.end_date,
        minimal=body.seed == "minimal",
        interval=body.interval,
    )
    return _created(work, created)


@router.post(
    "/strategies/import", response_model=CreatedStrategy, status_code=status.HTTP_201_CREATED
)
def post_import(body: ImportStrategyRequest, work: Work) -> CreatedStrategy:
    """Import a configuration file, stored exactly as written.

    Warnings ride along on the success: they never block, so an import with a shadowed stop
    still produces the strategy the file describes.
    """
    created = import_strategy(work, yaml_text=body.yaml, filename=body.filename)
    return _created(work, created)


@router.post(
    "/strategies/{strategy_id}/fork",
    response_model=CreatedStrategy,
    status_code=status.HTTP_201_CREATED,
)
def post_fork(strategy_id: UUID, body: ForkStrategyRequest, work: Work) -> CreatedStrategy:
    """Copy a strategy at some version into a new one starting at v1."""
    created = fork_strategy(work, source_id=strategy_id, name=body.name, version=body.version)
    return _created(work, created)


# --------------------------------------------------------------------------- deleting


@router.delete("/strategies/{strategy_id}", response_model=DeletedStrategy)
def delete_one_strategy(strategy_id: UUID, work: Work) -> DeletedStrategy:
    """Delete a strategy, its versions, its runs and their captured series. **Irreversible.**

    The only endpoint in the interface that destroys anything, and the only exception to the
    no-delete guarantee of spec section 15.2 -- narrowed, not withdrawn: versions and runs
    remain undeletable in their own right, and no endpoint removes one without its strategy.

    ``409`` when a run is still queued or running, or when a fork or promotion descends from
    it; the detail names what is in the way. ``200`` carries a receipt of what went, because
    an irreversible operation that answers with an empty body leaves the caller to guess how
    much it did.
    """
    return DeletedStrategy.of(delete_strategy(work, strategy_id))
