"""Phase 2: what the schema itself guarantees.

Spec section 14 makes promises the application is not trusted to keep -- append-only history,
frozen terminal runs, a run that cannot point at another strategy's version -- and this file
checks the database actually enforces them. Every one of these is a *refusal* test: the
interesting assertion is that a write fails, because a guarantee only counts if breaking it is
impossible rather than merely discouraged.

The two views are checked here too. They compute the verdict and staleness that every screen
displays, and they are the one place where SQL encodes a product rule rather than a shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg.rows import TupleRow

pytestmark = pytest.mark.db

#: A concrete timestamp rather than the string "now()", which is not a timestamp
#: literal PostgreSQL accepts as a parameter.
FINISHED = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

STRATEGY = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000a2"

CONFIG: dict[str, Any] = {
    "strategy": {"name": "momentum_v2"},
    "universe": {"ticker": "NVDA", "start_date": "2018-01-01", "end_date": "2025-12-31"},
}


def _strategy(
    db: psycopg.Connection[TupleRow],
    strategy_id: str = STRATEGY,
    name: str = "momentum_v2",
    origin: str = "authored",
    **columns: Any,
) -> str:
    keys = ["id", "name", "origin", *columns]
    values = [strategy_id, name, origin, *columns.values()]
    placeholders = ", ".join(["%s"] * len(values))
    db.execute(f"INSERT INTO strategy ({', '.join(keys)}) VALUES ({placeholders})", values)
    return strategy_id


def _version(
    db: psycopg.Connection[TupleRow],
    strategy_id: str = STRATEGY,
    version: int = 1,
    origin: str = "created",
    config: dict[str, Any] | None = None,
) -> None:
    db.execute(
        "INSERT INTO strategy_version (strategy_id, version, origin, config, config_yaml) "
        "VALUES (%s, %s, %s, %s, %s)",
        (strategy_id, version, origin, json.dumps(config or CONFIG), "yaml"),
    )


def _run(
    db: psycopg.Connection[TupleRow],
    run_id: str,
    *,
    strategy_id: str = STRATEGY,
    version: int = 1,
    number: int = 1,
    kind: str = "backtest",
    status: str = "queued",
    **columns: Any,
) -> str:
    keys = ["id", "strategy_id", "version", "number", "kind", "status", "seed", *columns]
    values = [run_id, strategy_id, version, number, kind, status, 0, *columns.values()]
    placeholders = ", ".join(["%s"] * len(values))
    db.execute(f"INSERT INTO run ({', '.join(keys)}) VALUES ({placeholders})", values)
    return run_id


def _rejects(db: psycopg.Connection[TupleRow], action: Callable[[], object]) -> str:
    """Run something expected to fail, returning the message and leaving the tx usable."""
    with pytest.raises(psycopg.Error) as caught:
        action()
    db.rollback()
    return str(caught.value)


# --------------------------------------------------------------------------- append-only


def test_a_version_cannot_be_edited(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(db, lambda: db.execute("UPDATE strategy_version SET note = 'x'"))
    assert "append-only" in message


def test_a_version_cannot_be_deleted(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(db, lambda: db.execute("DELETE FROM strategy_version"))
    assert "append-only" in message


def test_a_series_cannot_be_edited(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    run_id = _run(db, "00000000-0000-0000-0000-0000000000b1")
    db.execute(
        "INSERT INTO run_series (run_id, name, points) VALUES (%s, 'equity', %s)",
        (run_id, json.dumps({"dates": [], "values": []})),
    )
    db.commit()
    message = _rejects(db, lambda: db.execute("UPDATE run_series SET points = '{}'"))
    assert "append-only" in message


def test_a_run_cannot_be_deleted(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    _run(db, "00000000-0000-0000-0000-0000000000b1")
    db.commit()
    message = _rejects(db, lambda: db.execute("DELETE FROM run"))
    assert "never deleted" in message


# --------------------------------------------------------------------------- guarded purge


def _declare_purge(db: psycopg.Connection[TupleRow], strategy_id: str) -> None:
    """Say, for this transaction only, which strategy is being purged."""
    db.execute("SELECT set_config('cracktrade.purge_strategy_id', %s, true)", (strategy_id,))


def _populate(db: psycopg.Connection[TupleRow], strategy_id: str, name: str, run_id: str) -> None:
    """A strategy with a version, a run, and a captured series -- one of everything."""
    _strategy(db, strategy_id, name=name)
    _version(db, strategy_id)
    _run(db, run_id, strategy_id=strategy_id)
    db.execute(
        "INSERT INTO run_series (run_id, name, points) VALUES (%s, 'equity', %s)",
        (run_id, json.dumps({"dates": [], "values": []})),
    )


def test_a_declared_purge_may_delete_its_own_rows(db: psycopg.Connection[TupleRow]) -> None:
    """The one hole in append-only, and it only opens for a transaction that names a strategy."""
    run_id = "00000000-0000-0000-0000-0000000000b1"
    _populate(db, STRATEGY, "momentum_v2", run_id)
    db.commit()

    _declare_purge(db, STRATEGY)
    db.execute("DELETE FROM run_series WHERE run_id = %s", (run_id,))
    db.execute("DELETE FROM run WHERE strategy_id = %s", (STRATEGY,))
    db.execute("DELETE FROM strategy_version WHERE strategy_id = %s", (STRATEGY,))
    db.execute("DELETE FROM strategy WHERE id = %s", (STRATEGY,))
    db.commit()

    remaining = db.execute("SELECT count(*) FROM strategy").fetchone()
    assert remaining is not None
    assert remaining[0] == 0


def test_a_purge_cannot_reach_another_strategys_rows(db: psycopg.Connection[TupleRow]) -> None:
    """Scoped to the named strategy, so a purge of one is not a window on the rest.

    The whole point of naming the strategy rather than setting a boolean: a permission that
    merely said "deleting is allowed right now" would make every other strategy's history
    deletable by a mistyped WHERE clause for the length of the transaction.
    """
    mine = "00000000-0000-0000-0000-0000000000b1"
    theirs = "00000000-0000-0000-0000-0000000000b2"
    _populate(db, STRATEGY, "momentum_v2", mine)
    _populate(db, OTHER, "other_strategy", theirs)
    db.commit()

    _declare_purge(db, STRATEGY)
    assert "append-only" in _rejects(
        db, lambda: db.execute("DELETE FROM strategy_version WHERE strategy_id = %s", (OTHER,))
    )

    _declare_purge(db, STRATEGY)
    assert "never deleted" in _rejects(
        db, lambda: db.execute("DELETE FROM run WHERE strategy_id = %s", (OTHER,))
    )

    _declare_purge(db, STRATEGY)
    assert "append-only" in _rejects(
        db, lambda: db.execute("DELETE FROM run_series WHERE run_id = %s", (theirs,))
    )


def test_an_unguarded_delete_is_refused_exactly_as_before(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The escape hatch is opt-in, so a statement that does not know about it sees no change."""
    run_id = "00000000-0000-0000-0000-0000000000b1"
    _populate(db, STRATEGY, "momentum_v2", run_id)
    db.commit()

    assert "append-only" in _rejects(db, lambda: db.execute("DELETE FROM run_series"))
    assert "never deleted" in _rejects(db, lambda: db.execute("DELETE FROM run"))
    assert "append-only" in _rejects(db, lambda: db.execute("DELETE FROM strategy_version"))


def test_the_purge_permission_dies_with_its_transaction(db: psycopg.Connection[TupleRow]) -> None:
    """``set_config(..., true)`` is transaction-local, which is why it is safe on a pool.

    A permission that survived COMMIT would be inherited by whichever request borrowed the
    connection next, and that request would be free to delete a strategy nobody asked about.
    """
    run_id = "00000000-0000-0000-0000-0000000000b1"
    _populate(db, STRATEGY, "momentum_v2", run_id)
    _declare_purge(db, STRATEGY)
    db.commit()

    assert "append-only" in _rejects(
        db, lambda: db.execute("DELETE FROM strategy_version WHERE strategy_id = %s", (STRATEGY,))
    )


def test_a_purge_still_cannot_edit_anything(db: psycopg.Connection[TupleRow]) -> None:
    """Deletion was made reachable; immutability was not touched.

    A stored result is a record of a measurement rather than a cache of one (spec 14.1). That
    holds during a purge too: the rows can go, but not one of them can be rewritten first.
    """
    run_id = "00000000-0000-0000-0000-0000000000b1"
    _populate(db, STRATEGY, "momentum_v2", run_id)
    db.commit()

    _declare_purge(db, STRATEGY)
    assert "append-only" in _rejects(db, lambda: db.execute("UPDATE strategy_version SET note='x'"))

    _declare_purge(db, STRATEGY)
    assert "append-only" in _rejects(db, lambda: db.execute("UPDATE run_series SET points='{}'"))


# --------------------------------------------------------------------------- run lifecycle


def test_a_terminal_run_is_frozen(db: psycopg.Connection[TupleRow]) -> None:
    """A finished run is a record of a measurement, not a row that can be revised."""
    _strategy(db)
    _version(db)
    _run(
        db,
        "00000000-0000-0000-0000-0000000000b1",
        status="succeeded",
        finished_at=FINISHED,
        result=json.dumps({"metrics": {}}),
    )
    db.commit()
    message = _rejects(db, lambda: db.execute("UPDATE run SET result = '{}'"))
    assert "immutable" in message


def test_identity_fields_cannot_change_after_launch(db: psycopg.Connection[TupleRow]) -> None:
    """The version a run measured is the point of the row; a re-pointed run is a lie."""
    _strategy(db)
    _version(db)
    _version(db, version=2, origin="edited")
    _run(db, "00000000-0000-0000-0000-0000000000b1")
    db.commit()
    message = _rejects(db, lambda: db.execute("UPDATE run SET version = 2"))
    assert "identity fields are immutable" in message


def test_progress_and_lease_may_be_updated_while_running(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The mutable half: a live run reports progress and refreshes its lease."""
    _strategy(db)
    _version(db)
    _run(db, "00000000-0000-0000-0000-0000000000b1", status="running", started_at=FINISHED)
    db.commit()
    db.execute(
        "UPDATE run SET progress = %s, claimed_by = 'worker-1', heartbeat_at = now()",
        (json.dumps({"stage": "optimize", "percent": 41}),),
    )
    db.commit()
    row = db.execute("SELECT progress ->> 'stage', claimed_by FROM run").fetchone()
    assert row is not None
    assert row[0] == "optimize"
    assert row[1] == "worker-1"


def test_a_cancellation_cannot_be_withdrawn(db: psycopg.Connection[TupleRow]) -> None:
    """The worker may already have acted on it, so un-asking would resume a stopped run."""
    _strategy(db)
    _version(db)
    _run(db, "00000000-0000-0000-0000-0000000000b1", status="running", started_at=FINISHED)
    db.commit()
    db.execute("UPDATE run SET cancel_requested = true")
    db.commit()
    message = _rejects(db, lambda: db.execute("UPDATE run SET cancel_requested = false"))
    assert "cannot be withdrawn" in message


def test_a_queued_run_cannot_carry_a_result(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(
        db,
        lambda: _run(db, "00000000-0000-0000-0000-0000000000b1", result=json.dumps({})),
    )
    assert "run_result_iff_succeeded" in message


def test_a_failed_run_needs_a_category(db: psycopg.Connection[TupleRow]) -> None:
    """The failure screen leads with the category, so a failure without one cannot render."""
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(
        db,
        lambda: _run(
            db,
            "00000000-0000-0000-0000-0000000000b1",
            status="failed",
            finished_at=FINISHED,
            error=json.dumps({"message": "boom"}),
        ),
    )
    assert "run_category_iff_failed" in message


def test_only_a_walk_forward_carries_a_verdict(db: psycopg.Connection[TupleRow]) -> None:
    """Credibility comes from validation alone (spec section 12); a backtest has no verdict."""
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(
        db,
        lambda: _run(db, "00000000-0000-0000-0000-0000000000b1", is_credible=True),
    )
    assert "run_credible_only_wf" in message


# --------------------------------------------------------------------------- referential shape


def test_a_run_cannot_reference_a_missing_version(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(
        db,
        lambda: _run(db, "00000000-0000-0000-0000-0000000000b1", version=99),
    )
    assert "run_version_exists" in message


def test_a_run_cannot_borrow_another_strategys_version(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The composite key is what makes this impossible rather than merely unlikely."""
    _strategy(db)
    _version(db)
    _strategy(db, OTHER, name="other")
    _version(db, OTHER, version=1)
    _version(db, OTHER, version=2, origin="edited")
    db.commit()
    message = _rejects(
        db,
        lambda: _run(db, "00000000-0000-0000-0000-0000000000b1", version=2),
    )
    assert "run_version_exists" in message


def test_duplicate_strategy_names_are_refused(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    db.commit()
    message = _rejects(db, lambda: _strategy(db, OTHER, name="momentum_v2"))
    assert "strategy_name_unique" in message


def test_a_fork_needs_a_parent(db: psycopg.Connection[TupleRow]) -> None:
    message = _rejects(db, lambda: _strategy(db, OTHER, name="orphan_fork", origin="forked"))
    assert "strategy_lineage_shape" in message


def test_version_one_cannot_be_an_edit(db: psycopg.Connection[TupleRow]) -> None:
    """v1 origins say how a strategy began; only later versions can be edits or restores."""
    _strategy(db)
    db.commit()
    message = _rejects(db, lambda: _version(db, version=1, origin="edited"))
    assert "version_origin_matches_position" in message


def test_a_restore_must_point_backwards(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    message = _rejects(
        db,
        lambda: db.execute(
            "INSERT INTO strategy_version "
            "(strategy_id, version, origin, restored_from, config, config_yaml) "
            "VALUES (%s, 2, 'restored', 5, %s, 'yaml')",
            (STRATEGY, json.dumps(CONFIG)),
        ),
    )
    assert "version_restore_shape" in message


# --------------------------------------------------------------------------- the views


def test_a_strategy_with_no_runs_has_never_run(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    db.commit()
    row = db.execute("SELECT verdict, head_version, ticker FROM strategy_overview").fetchone()
    assert row is not None
    assert row[0] == "never_run"
    assert row[1] == 1
    assert row[2] == "NVDA"


def test_runs_without_validation_are_unvalidated(db: psycopg.Connection[TupleRow]) -> None:
    """Not a neutral state: it means nobody has checked the result yet."""
    _strategy(db)
    _version(db)
    _run(
        db,
        "00000000-0000-0000-0000-0000000000b1",
        status="succeeded",
        finished_at=FINISHED,
        result=json.dumps({}),
    )
    db.commit()
    row = db.execute("SELECT verdict FROM strategy_overview").fetchone()
    assert row is not None
    assert row[0] == "unvalidated"


def test_a_credible_walk_forward_against_the_head_validates(
    db: psycopg.Connection[TupleRow],
) -> None:
    _strategy(db)
    _version(db)
    _run(
        db,
        "00000000-0000-0000-0000-0000000000b1",
        kind="walk_forward",
        status="succeeded",
        finished_at=FINISHED,
        result=json.dumps({"is_credible": True}),
        is_credible=True,
    )
    db.commit()
    row = db.execute("SELECT verdict FROM strategy_overview").fetchone()
    assert row is not None
    assert row[0] == "credible"


def test_editing_past_a_validation_returns_to_unvalidated(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The rule with teeth (spec section 14.4).

    A credible run against v1 of a strategy now at v2 describes a config that no longer
    exists. Carrying its verdict forward would put a green chip on numbers nothing has
    checked, which is the single most expensive thing this schema can get wrong.
    """
    _strategy(db)
    _version(db)
    _run(
        db,
        "00000000-0000-0000-0000-0000000000b1",
        kind="walk_forward",
        status="succeeded",
        finished_at=FINISHED,
        result=json.dumps({"is_credible": True}),
        is_credible=True,
    )
    db.commit()
    before = db.execute("SELECT verdict FROM strategy_overview").fetchone()
    assert before is not None
    assert before[0] == "credible"

    _version(db, version=2, origin="edited")
    db.commit()
    after = db.execute("SELECT verdict, head_version FROM strategy_overview").fetchone()
    assert after is not None
    assert after[0] == "unvalidated"
    assert after[1] == 2


def test_a_run_against_an_older_version_is_stale(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    _run(db, "00000000-0000-0000-0000-0000000000b1")
    _version(db, version=2, origin="edited")
    _run(db, "00000000-0000-0000-0000-0000000000b2", version=2, number=2)
    db.commit()
    rows = db.execute(
        "SELECT number, stale, strategy_name FROM run_overview ORDER BY number"
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [(1, True), (2, False)]
    assert rows[0][2] == "momentum_v2"


def test_counts_are_per_kind(db: psycopg.Connection[TupleRow]) -> None:
    _strategy(db)
    _version(db)
    _run(db, "00000000-0000-0000-0000-0000000000b1", kind="backtest", number=1)
    _run(db, "00000000-0000-0000-0000-0000000000b2", kind="optimize", number=2)
    _run(db, "00000000-0000-0000-0000-0000000000b3", kind="optimize", number=3)
    db.commit()
    row = db.execute(
        "SELECT backtest_runs, optimize_runs, walk_forward_runs, versions FROM strategy_overview"
    ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 2, 0, 1)
