"""Phase 3: the repositories, against a real database.

The interesting behaviours are the ones the schema and the SQL share responsibility for:
numbering computed inside a transaction, the claim query that makes the database a queue, the
lease, and the translation of a database refusal into an error the layer above can act on.
Each of those is checked here rather than assumed, because each is the kind of thing that
looks right and is not.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import TupleRow

from cracktrade.api.db.uow import unit_of_work_on
from cracktrade.api.errors import ConflictError, InvariantViolationError, NotFoundError
from cracktrade.api.repos import RunRepo, SeriesRepo, StrategyRepo, VersionRepo
from cracktrade.api.repos.rows import (
    FailureCategory,
    RunKind,
    RunStatus,
    StrategyOrigin,
    Verdict,
    VersionOrigin,
)

pytestmark = pytest.mark.db

CONFIG: dict[str, Any] = {
    "strategy": {"name": "momentum_v2"},
    "universe": {"ticker": "NVDA", "start_date": "2018-01-01", "end_date": "2025-12-31"},
}


def _seed(
    db: psycopg.Connection[TupleRow], name: str = "momentum_v2", ticker: str = "NVDA"
) -> tuple[UUID, int]:
    """A strategy with one version. Returns its id and head version."""
    strategies = StrategyRepo(db)
    versions = VersionRepo(db)
    config = {**CONFIG, "universe": {**CONFIG["universe"], "ticker": ticker}}
    strategy = strategies.create(name=name, origin=StrategyOrigin.AUTHORED)
    version = versions.append(
        strategy_id=strategy.id,
        origin=VersionOrigin.CREATED,
        config=config,
        config_yaml="strategy:\n  name: momentum_v2\n",
    )
    db.commit()
    return strategy.id, version.version


# --------------------------------------------------------------------------- strategies


def test_create_and_read_back(db: psycopg.Connection[TupleRow]) -> None:
    repo = StrategyRepo(db)
    created = repo.create(name="momentum_v2", origin=StrategyOrigin.AUTHORED)
    db.commit()
    assert repo.get(created.id) == created
    assert repo.get_by_name("momentum_v2") == created
    assert repo.get(uuid4()) is None


def test_require_names_what_is_missing(db: psycopg.Connection[TupleRow]) -> None:
    with pytest.raises(NotFoundError):
        StrategyRepo(db).require(uuid4())


def test_a_duplicate_name_is_a_conflict_not_a_crash(db: psycopg.Connection[TupleRow]) -> None:
    """A race the client can act on: rename and retry. It must not surface as a 500."""
    repo = StrategyRepo(db)
    repo.create(name="momentum_v2", origin=StrategyOrigin.AUTHORED)
    db.commit()
    with pytest.raises(ConflictError, match="already exists"):
        repo.create(name="momentum_v2", origin=StrategyOrigin.AUTHORED)
    db.rollback()


def test_a_malformed_lineage_is_an_invariant_violation(db: psycopg.Connection[TupleRow]) -> None:
    """The service layer is supposed to prevent this, so reaching the CHECK is a bug report."""
    repo = StrategyRepo(db)
    with pytest.raises(InvariantViolationError):
        repo.create(name="orphan", origin=StrategyOrigin.FORKED)
    db.rollback()


def test_lineage_is_recorded_for_a_fork(db: psycopg.Connection[TupleRow]) -> None:
    parent_id, head = _seed(db)
    repo = StrategyRepo(db)
    fork = repo.create(
        name="momentum_v3",
        origin=StrategyOrigin.FORKED,
        parent_strategy_id=parent_id,
        parent_version=head,
    )
    db.commit()
    assert fork.parent_strategy_id == parent_id
    assert fork.parent_version == head


# --------------------------------------------------------------------------- versions


def test_versions_are_numbered_from_one_without_gaps(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, _ = _seed(db)
    repo = VersionRepo(db)
    second = repo.append(
        strategy_id=strategy_id,
        origin=VersionOrigin.EDITED,
        config=CONFIG,
        config_yaml="yaml",
        note="widened the RSI window",
    )
    third = repo.append(
        strategy_id=strategy_id, origin=VersionOrigin.EDITED, config=CONFIG, config_yaml="yaml"
    )
    db.commit()
    assert (second.version, third.version) == (2, 3)
    assert repo.require_head(strategy_id).version == 3
    assert [v.version for v in repo.list(strategy_id)] == [3, 2, 1]


def test_numbering_is_per_strategy(db: psycopg.Connection[TupleRow]) -> None:
    """Two strategies both start at v1; a shared sequence would be a confusing lie."""
    first_id, _ = _seed(db, name="one")
    second_id, _ = _seed(db, name="two")
    repo = VersionRepo(db)
    assert repo.require_head(first_id).version == 1
    assert repo.require_head(second_id).version == 1


def test_a_version_round_trips_its_config(db: psycopg.Connection[TupleRow]) -> None:
    """jsonb is stored and returned as written -- the config is not reshaped in transit."""
    strategy_id, _ = _seed(db)
    stored = VersionRepo(db).require(strategy_id, 1)
    assert stored.config == CONFIG
    assert stored.config_yaml.startswith("strategy:")


def test_a_missing_version_is_reported_not_guessed(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, _ = _seed(db)
    repo = VersionRepo(db)
    assert repo.get(strategy_id, 99) is None
    with pytest.raises(NotFoundError, match="no version 99"):
        repo.require(strategy_id, 99)


def test_concurrent_appends_cannot_both_win(db_url: str) -> None:
    """Two saves reading the same head: one commits, the other is told to reload and retry.

    The alternative -- renumbering the loser and accepting it -- would silently reorder a
    history whose whole value is that it records what happened.

    The second save genuinely blocks on the unique index until the first commits, so it has to
    run on another thread: awaiting it inline would deadlock against the transaction it is
    waiting for. ``lock_timeout`` is a safety net only -- it is far longer than the hand-off,
    and exists so a regression here fails the suite instead of hanging it.
    """
    with psycopg.connect(db_url) as setup:
        strategy_id, _ = _seed(setup)

    def second_save() -> None:
        with psycopg.connect(db_url) as second:
            second.execute("SET lock_timeout = '15s'")
            with unit_of_work_on(second) as work:
                VersionRepo(work.connection).append(
                    strategy_id=strategy_id,
                    origin=VersionOrigin.EDITED,
                    config=CONFIG,
                    config_yaml="b",
                )

    with psycopg.connect(db_url) as first, ThreadPoolExecutor(max_workers=1) as pool:
        with unit_of_work_on(first) as work:
            VersionRepo(work.connection).append(
                strategy_id=strategy_id,
                origin=VersionOrigin.EDITED,
                config=CONFIG,
                config_yaml="a",
            )
            pending = pool.submit(second_save)
            # Give the other thread time to reach the index and block on it, so that both
            # saves really did compute the same next version.
            time.sleep(0.3)
            assert not pending.done()

        with pytest.raises(ConflictError, match="reload and retry"):
            pending.result(timeout=20)

        assert VersionRepo(first).require_head(strategy_id).version == 2


# --------------------------------------------------------------------------- runs


def test_runs_share_one_number_sequence_per_strategy(db: psycopg.Connection[TupleRow]) -> None:
    """So that "#14" identifies a run within a strategy without naming its kind."""
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    first = repo.create(
        strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0
    )
    second = repo.create(
        strategy_id=strategy_id,
        version=head,
        kind=RunKind.OPTIMIZE,
        params={"objective": "calmar"},
        seed=7,
    )
    db.commit()
    assert (first.number, second.number) == (1, 2)
    assert second.params == {"objective": "calmar"}
    assert second.seed == 7
    assert second.status is RunStatus.QUEUED


def test_a_run_against_an_unknown_version_is_a_conflict(
    db: psycopg.Connection[TupleRow],
) -> None:
    strategy_id, _ = _seed(db)
    with pytest.raises(ConflictError, match="does not exist"):
        RunRepo(db).create(
            strategy_id=strategy_id, version=99, kind=RunKind.BACKTEST, params={}, seed=0
        )
    db.rollback()


def test_claiming_takes_the_oldest_and_marks_it_running(
    db: psycopg.Connection[TupleRow],
) -> None:
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    first = repo.create(
        strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0
    )
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.OPTIMIZE, params={}, seed=0)
    db.commit()

    claimed = repo.claim("worker-1")
    db.commit()
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status is RunStatus.RUNNING
    assert claimed.claimed_by == "worker-1"
    assert claimed.started_at is not None


def test_claiming_an_empty_queue_returns_nothing(db: psycopg.Connection[TupleRow]) -> None:
    """An idle worker must get ``None``, not an exception it would have to treat as normal."""
    assert RunRepo(db).claim("worker-1") is None


def test_two_workers_never_claim_the_same_run(db_url: str) -> None:
    """SKIP LOCKED is what makes the database a queue rather than a table two workers fight over."""
    with psycopg.connect(db_url) as first, psycopg.connect(db_url) as second:
        strategy_id, head = _seed(first)
        repo = RunRepo(first)
        repo.create(strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0)
        repo.create(strategy_id=strategy_id, version=head, kind=RunKind.OPTIMIZE, params={}, seed=0)
        first.commit()

        # Claim inside an open transaction so the row stays locked while the second worker runs.
        claimed_first = RunRepo(first).claim("worker-1")
        claimed_second = RunRepo(second).claim("worker-2")
        first.commit()
        second.commit()

        assert claimed_first is not None
        assert claimed_second is not None
        assert claimed_first.id != claimed_second.id


def test_heartbeat_refreshes_the_lease_and_reports_progress(
    db: psycopg.Connection[TupleRow],
) -> None:
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.OPTIMIZE, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None

    assert repo.heartbeat(claimed.id, progress={"stage": "generation 4 of 10", "percent": 41})
    db.commit()
    current = repo.require(claimed.id)
    assert current.progress == {"stage": "generation 4 of 10", "percent": 41}


def test_heartbeating_a_finished_run_is_refused(db: psycopg.Connection[TupleRow]) -> None:
    """A worker that lost its lease must learn so, rather than write into a run it lost."""
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None
    repo.cancel(claimed.id)
    db.commit()

    assert repo.heartbeat(claimed.id) is False


def test_succeeding_lands_result_and_promoted_columns_together(
    db: psycopg.Connection[TupleRow],
) -> None:
    """One statement, so the filterable columns cannot disagree with the result they came from."""
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.WALK_FORWARD, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None

    finished = repo.succeed(
        claimed.id,
        result={"is_credible": False, "failures": ["deflated Sharpe P=0.00"]},
        is_credible=False,
    )
    db.commit()
    assert finished.status is RunStatus.SUCCEEDED
    assert finished.is_credible is False
    assert finished.result is not None
    assert finished.result["failures"] == ["deflated Sharpe P=0.00"]
    assert finished.progress is None
    assert finished.finished_at is not None


def test_failing_preserves_the_engines_own_error_text(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The failure screen shows this verbatim, so it must not be summarised on the way in."""
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None

    message = "MarketDataError: NVDA 2018-01-01 -> 2018-03-14 returned 0 bars\n  3 retries"
    failed = repo.fail(
        claimed.id, category=FailureCategory.MARKET_DATA, exit_code=3, message=message
    )
    db.commit()
    assert failed.status is RunStatus.FAILED
    assert failed.failure_category is FailureCategory.MARKET_DATA
    assert failed.error is not None
    assert failed.error["message"] == message
    assert failed.error["exit_code"] == 3


def test_a_finished_run_cannot_finish_twice(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None
    repo.succeed(claimed.id, result={}, suppressed=False)
    db.commit()

    with pytest.raises(ConflictError):
        repo.succeed(claimed.id, result={})
    db.rollback()
    with pytest.raises(ConflictError):
        repo.cancel(claimed.id)
    db.rollback()


def test_cancellation_is_requested_then_recorded(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.OPTIMIZE, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None

    assert repo.request_cancel(claimed.id).cancel_requested is True
    cancelled = repo.cancel(claimed.id)
    db.commit()
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.result is None
    assert cancelled.error is None


def test_expired_leases_are_reported_for_the_caller_to_fail(
    db: psycopg.Connection[TupleRow],
) -> None:
    """Reported, never re-queued: a re-run refetches data and measures something else."""
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    repo.create(strategy_id=strategy_id, version=head, kind=RunKind.OPTIMIZE, params={}, seed=0)
    db.commit()
    claimed = repo.claim("worker-1")
    assert claimed is not None
    db.commit()

    assert repo.expired_leases(3600) == []
    db.execute("UPDATE run SET heartbeat_at = now() - interval '10 minutes'")
    db.commit()
    expired = repo.expired_leases(60)
    assert [run.id for run in expired] == [claimed.id]


def test_listing_filters_and_paginates(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, head = _seed(db)
    repo = RunRepo(db)
    for kind in (RunKind.BACKTEST, RunKind.OPTIMIZE, RunKind.OPTIMIZE):
        repo.create(strategy_id=strategy_id, version=head, kind=kind, params={}, seed=0)
    db.commit()

    everything = repo.list_overview(strategy_id=strategy_id)
    assert len(everything) == 3
    assert everything[0].strategy_name == "momentum_v2"
    assert everything[0].stale is False

    optimizations = repo.list_overview(strategy_id=strategy_id, kinds=[RunKind.OPTIMIZE])
    assert len(optimizations) == 2
    assert repo.count(strategy_id=strategy_id, kinds=[RunKind.OPTIMIZE]) == 2
    assert len(repo.list_overview(strategy_id=strategy_id, limit=1)) == 1
    assert repo.list_overview(statuses=[RunStatus.RUNNING]) == []


def test_a_run_against_an_older_version_reads_as_stale(
    db: psycopg.Connection[TupleRow],
) -> None:
    strategy_id, head = _seed(db)
    runs = RunRepo(db)
    old = runs.create(
        strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0
    )
    VersionRepo(db).append(
        strategy_id=strategy_id, origin=VersionOrigin.EDITED, config=CONFIG, config_yaml="yaml"
    )
    db.commit()
    overview = runs.overview(old.id)
    assert overview is not None
    assert overview.stale is True


# --------------------------------------------------------------------------- series


def test_series_round_trip_and_catalog(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, head = _seed(db)
    run = RunRepo(db).create(
        strategy_id=strategy_id, version=head, kind=RunKind.WALK_FORWARD, params={}, seed=0
    )
    repo = SeriesRepo(db)
    points = {"dates": ["2018-01-02", "2018-01-03"], "values": [10000.0, 10120.5]}
    repo.put(run_id=run.id, name="equity", points=points)
    repo.put(run_id=run.id, name="equity", points=points, fold=1)
    repo.put(run_id=run.id, name="drawdown", points=points)
    db.commit()

    assert repo.require(run.id, "equity").points == points
    assert repo.catalog(run.id) == {"drawdown": [0], "equity": [0, 1]}
    assert repo.get(run.id, "nope") is None


def test_a_series_cannot_be_overwritten(db: psycopg.Connection[TupleRow]) -> None:
    """Captured once, at run time. A second write would mean two versions of one measurement.

    Reported as an invariant violation rather than a conflict, and deliberately so: only the
    worker writes series, once, while landing a run. A duplicate is therefore a worker that
    landed twice -- a defect above this layer -- and not a race a client could retry its way
    out of. This is the translation layer's default doing its job: an unlisted constraint is
    treated as a bug, which is the safe direction to be wrong in.
    """
    strategy_id, head = _seed(db)
    run = RunRepo(db).create(
        strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0
    )
    repo = SeriesRepo(db)
    repo.put(run_id=run.id, name="equity", points={"dates": [], "values": []})
    db.commit()
    with pytest.raises(InvariantViolationError, match="run_series_pkey"):
        repo.put(run_id=run.id, name="equity", points={"dates": [], "values": []})
    db.rollback()


# --------------------------------------------------------------------------- the overview


def test_the_overview_carries_the_verdict_and_counts(db: psycopg.Connection[TupleRow]) -> None:
    strategy_id, head = _seed(db)
    runs = RunRepo(db)
    runs.create(strategy_id=strategy_id, version=head, kind=RunKind.BACKTEST, params={}, seed=0)
    db.commit()
    claimed = runs.claim("worker-1")
    assert claimed is not None
    runs.succeed(claimed.id, result={}, suppressed=False)
    db.commit()

    overview = StrategyRepo(db).overview(strategy_id)
    assert overview is not None
    assert overview.verdict is Verdict.UNVALIDATED
    assert overview.ticker == "NVDA"
    assert overview.head_version == 1
    assert (overview.backtest_runs, overview.optimize_runs) == (1, 0)


def test_search_matches_name_or_ticker(db: psycopg.Connection[TupleRow]) -> None:
    _seed(db, name="momentum_v2", ticker="NVDA")
    _seed(db, name="gap_fade_spy", ticker="SPY")
    repo = StrategyRepo(db)

    assert [s.name for s in repo.list_overview(search="momentum")] == ["momentum_v2"]
    assert [s.name for s in repo.list_overview(search="spy")] == ["gap_fade_spy"]
    assert len(repo.list_overview()) == 2


def test_verdict_counts_include_the_empty_ones(db: psycopg.Connection[TupleRow]) -> None:
    """The filter chips show every verdict, so a zero must be a zero rather than a gap."""
    _seed(db)
    counts = StrategyRepo(db).count_by_verdict()
    assert counts[Verdict.NEVER_RUN] == 1
    assert counts[Verdict.CREDIBLE] == 0
    assert set(counts) == set(Verdict)


# --------------------------------------------------------------------------- unit of work


def test_a_failed_workflow_leaves_nothing_behind(db_url: str) -> None:
    """The reason the unit of work exists: a half-applied workflow is worse than none."""
    with psycopg.connect(db_url) as connection:
        with pytest.raises(ConflictError), unit_of_work_on(connection) as work:
            strategies = StrategyRepo(work.connection)
            strategies.create(name="first", origin=StrategyOrigin.AUTHORED)
            # Same name inside the same transaction: the whole unit must roll back.
            strategies.create(name="first", origin=StrategyOrigin.AUTHORED)

        assert StrategyRepo(connection).get_by_name("first") is None
