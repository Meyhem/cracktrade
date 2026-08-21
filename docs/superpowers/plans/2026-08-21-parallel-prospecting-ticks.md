# Parallel Prospecting Ticks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run P prospecting ticks concurrently in one worker process, raising sweep throughput from one core to P, without changing any number a tick produces.

**Architecture:** The session cursor becomes a *reservation*: an atomic SQL advance that hands out `(ticker, ordinal)` pairs before the work runs, so a 22-second search no longer shares a transaction with the cursor write. A parent process keeps P ticks in flight in a `ProcessPoolExecutor`, refilling one slot at a time (no batch barrier), and prefetches every child's price history itself over a rolling horizon so P children never hit the provider directly.

**Tech Stack:** Python 3.13, psycopg 3, pandas, `concurrent.futures`, pytest (`db`-marked tests run against a real Postgres from `docker-compose.yml`), mypy strict, ruff full rule set.

**Spec:** `docs/superpowers/specs/2026-08-21-prospecting-parallelism-design.md`

## Global Constraints

- `uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest` must be clean before any commit. mypy runs **strict**; ruff has the **full** rule set.
- `./scripts/check.sh` runs both the Python and web gates. Run it before pushing. **Never push a red tree.**
- `docs/ENGINE_SPEC.md` is normative. Code and spec must never disagree silently; any behaviour change lands with its spec edit **in the same commit**.
- Commit messages: short imperative subject, body explaining *why* including designs tried and rejected, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **No look-ahead bias.** Do not weaken any of the four enforcement layers (spec §2). Exactly one `causal_shift` exists in the engine; a repo test asserts no other module calls `.shift`. `uv run pytest -m causality` must stay green untouched.
- Nothing in this plan may change a computed result. Task 8's determinism test is the gate on that claim.
- Tests that need a database carry `pytestmark = pytest.mark.db` and take the `db` fixture.

---

### Task 1: Measure scaling and determinism before building anything

The design's P, and its central claim that parallelism is observationally invisible, both rest on numbers nobody has taken. This task takes them. The script is throwaway; the recorded numbers are the deliverable.

**Files:**
- Create (throwaway, not committed): `/tmp/claude-1000/-home-meyhem-dev-cracktrade/bb629d42-d9d3-43f0-ac0c-53bf65a23630/scratchpad/scale_probe.py`
- Modify: `docs/superpowers/specs/2026-08-21-prospecting-parallelism-design.md` (section 8)

**Interfaces:**
- Consumes: nothing.
- Produces: a measured value for `DEFAULT_PARALLELISM` used in Task 7, and a yes/no on bit-identity across processes used in Task 8.

- [ ] **Step 1: Write the probe script**

Uses `StaticProvider` so the measurement is pure compute with no network variance.

```python
"""Throwaway: how does prospecting scale across processes, and is it still deterministic?"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from cracktrade.config import Interval
from cracktrade.data import StaticProvider
from cracktrade.evolution import GaSettings
from cracktrade.indicators.catalogue import install
from cracktrade.optimize import TradeFloor
from cracktrade.prospect import SweepParams, SweepHistory, family_of, prospect_once

HOME = "AMD"
FAMILY = family_of(HOME)
TICKERS = (*FAMILY.members, *FAMILY.controls)
PARAMS = SweepParams(
    interval=Interval.D1, population=50, generations=10, min_trades=0,
    min_trades_per_year=0.0, lookback_days=1200,
)


def _frame(bars: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, bars)))
    index = pd.DatetimeIndex(
        pd.date_range(end=pd.Timestamp.now().normalize(), periods=bars, freq="B").to_numpy(),
        name="Date",
    )
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": np.full(bars, 1_000_000.0)},
        index=index,
    )


FRAMES = {t: _frame(700, i) for i, t in enumerate(TICKERS)}


def one_tick(seed: int) -> str:
    install()
    history = SweepHistory(params=PARAMS, provider=StaticProvider(FRAMES))
    candidate = prospect_once(
        PARAMS.chassis_for(HOME),
        history,
        settings=GaSettings(population=PARAMS.population, generations=PARAMS.generations),
        seed=seed,
        workers=1,
        objective_name=PARAMS.objective,
        trade_floor=TradeFloor(minimum=0, per_year=0.0),
        segments=PARAMS.segments,
        holdout_fraction=PARAMS.holdout_fraction,
    )
    return candidate.strategy_yaml


if __name__ == "__main__":
    install()
    for n in (1, 4, 8, 16):
        started = time.monotonic()
        with ProcessPoolExecutor(max_workers=n) as pool:
            list(pool.map(one_tick, range(n)))
        elapsed = time.monotonic() - started
        print(f"P={n:2d}  wall={elapsed:6.1f}s  per-tick={elapsed / n:6.1f}s")

    serial = [one_tick(s) for s in range(4)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        parallel = list(pool.map(one_tick, range(4)))
    print("deterministic:", serial == parallel)
```

The probe is throwaway and deliberately does not use `SweepParams.ga_settings()` — that method does not exist until Task 6. If the call signature has drifted, check `src/cracktrade/prospect/sweep.py:prospect_once` and adjust; getting it to run matters more than its shape.

- [ ] **Step 2: Run it**

```bash
uv run python "/tmp/claude-1000/-home-meyhem-dev-cracktrade/bb629d42-d9d3-43f0-ac0c-53bf65a23630/scratchpad/scale_probe.py"
```

Expected: four timing lines and a `deterministic:` line.

- [ ] **Step 3: If `deterministic: False`, pin thread counts and re-run**

Add to the top of `one_tick`, before the import of anything numeric takes effect — set the environment in the child initializer instead if this does not take:

```python
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
```

If pinning fixes it, `threadpoolctl` becomes a dependency and Task 6 must pin in the pool initializer. Record which.

- [ ] **Step 4: Record the numbers in the design doc**

Replace section 8's questions 2 and 3 with the measured answers. State the per-tick time at each P and the resulting recommended `DEFAULT_PARALLELISM` (the largest P whose per-tick time has not visibly degraded). Do not round in your favour; if scaling stops at 6, say 6.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-21-prospecting-parallelism-design.md
git commit -m "Measure prospecting's cross-process scaling before parallelising it"
```

---

### Task 2: `Rotation.reserve` — hand out N ordinals as a value

**Files:**
- Modify: `src/cracktrade/prospect/sweep.py` (the `Rotation` dataclass, around line 168)
- Test: `tests/test_prospect.py`

**Interfaces:**
- Consumes: the existing `Rotation(universe, index, passes)` value and its `current`, `ordinal`, `advance` members.
- Produces: `Reservation(ticker: str, ordinal: int)` frozen dataclass, exported from `cracktrade.prospect`; `Rotation.reserve(count: int) -> tuple[tuple[Reservation, ...], Rotation]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prospect.py`:

```python
def test_a_reservation_hands_out_consecutive_ordinals() -> None:
    rotation = Rotation(("AMD", "NVDA", "SOXL"))

    reserved, after = rotation.reserve(2)

    assert [(r.ticker, r.ordinal) for r in reserved] == [("AMD", 0), ("NVDA", 1)]
    assert (after.index, after.passes) == (2, 0)


def test_a_reservation_wraps_the_universe_and_counts_the_pass() -> None:
    rotation = Rotation(("AMD", "NVDA", "SOXL"), index=2)

    reserved, after = rotation.reserve(3)

    assert [(r.ticker, r.ordinal) for r in reserved] == [
        ("SOXL", 2), ("AMD", 3), ("NVDA", 4),
    ]
    assert (after.index, after.passes) == (2, 1)


def test_every_reserved_ordinal_is_distinct_across_two_passes() -> None:
    # The ordinal is what a tick's seed derives from, so a repeat would make a whole pass a
    # bit-identical replay of the one before it.
    reserved, _ = Rotation(("AMD", "NVDA")).reserve(4)

    assert len({r.ordinal for r in reserved}) == 4


def test_a_reservation_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="positive count"):
        Rotation(("AMD",)).reserve(0)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_prospect.py -k reserv -v
```

Expected: FAIL, `AttributeError: 'Rotation' object has no attribute 'reserve'`.

- [ ] **Step 3: Implement**

In `src/cracktrade/prospect/sweep.py`, above `class Rotation`:

```python
@dataclass(frozen=True, slots=True)
class Reservation:
    """One tick's claim on a position in the sweep.

    Handed out *before* the work runs (section 19.7 as amended). The ordinal is what the tick's
    seed derives from, so it must survive the round trip through the database unchanged: two
    ticks holding the same ordinal would run the same search twice and store it twice.

    Attributes:
        ticker: the instrument to prospect.
        ordinal: this tick's position across the whole sweep.
    """

    ticker: str
    ordinal: int
```

And as a method on `Rotation`:

```python
    def reserve(self, count: int) -> tuple[tuple[Reservation, ...], Rotation]:
        """``count`` consecutive positions, and the rotation that follows them.

        Built by repeated :meth:`advance` rather than by arithmetic, so the wrap-around lives in
        exactly one place. :meth:`~cracktrade.api.repos.ProspectRepo.reserve_ordinals` does the
        same advance in SQL because it must be atomic; a test asserts the two agree.

        Raises:
            ValueError: ``count`` is not positive.
        """
        if count < 1:
            msg = f"a reservation needs a positive count, got {count}"
            raise ValueError(msg)
        reserved: list[Reservation] = []
        rotation = self
        for _ in range(count):
            reserved.append(Reservation(ticker=rotation.current, ordinal=rotation.ordinal))
            rotation = rotation.advance()
        return tuple(reserved), rotation
```

Add `"Reservation"` to `__all__` in `sweep.py` and re-export it from `src/cracktrade/prospect/__init__.py` alongside `Rotation`.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/test_prospect.py -k reserv -v
```

Expected: 4 passed.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add src/cracktrade/prospect/sweep.py src/cracktrade/prospect/__init__.py tests/test_prospect.py
git commit -m "Let a rotation hand out several positions at once"
```

---

### Task 3: `reserve_ordinals` — advance the cursor atomically, before the work

**Files:**
- Modify: `src/cracktrade/api/repos/prospect.py`
- Test: `tests/api/test_prospect_repo.py`

**Interfaces:**
- Consumes: `Reservation` and `Rotation` from Task 2; `_SESSION_COLUMNS`, `_session`, `ConflictError` already in the module.
- Produces: `ProspectRepo.reserve_ordinals(session_id: UUID, *, count: int, worker: str | None = None) -> tuple[tuple[Reservation, ...], ProspectSessionRow]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_prospect_repo.py`:

```python
def test_a_reservation_advances_the_cursor_past_what_it_handed_out(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    reserved, after = repo.reserve_ordinals(session_id, count=2)

    assert [(r.ticker, r.ordinal) for r in reserved] == [("AMD", 0), ("NVDA", 1)]
    assert (after.cursor_index, after.passes_completed) == (2, 0)


def test_the_sql_advance_agrees_with_the_rotation_over_a_wraparound(
    db: psycopg.Connection[TupleRow],
) -> None:
    # The arithmetic exists twice -- in SQL because it must be atomic, in Rotation because that
    # is where wrap-around is defined. This is the test that stops them drifting apart.
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.reserve_ordinals(session_id, count=2)

    reserved, after = repo.reserve_ordinals(session_id, count=4)

    expected, rotation = Rotation(UNIVERSE, index=2, passes=0).reserve(4)
    assert reserved == expected
    assert (after.cursor_index, after.passes_completed) == (rotation.index, rotation.passes)


def test_two_concurrent_reservations_never_share_an_ordinal(
    db_url: str,
) -> None:
    # The whole point of reserving in SQL. Two connections, two reservations, no overlap.
    with psycopg.connect(db_url) as one, psycopg.connect(db_url) as two:
        session_id = ProspectRepo(one).create_session(
            name="race", universe=UNIVERSE, params=PARAMS, seed=7
        ).id
        one.commit()

        first, _ = ProspectRepo(one).reserve_ordinals(session_id, count=3)
        one.commit()
        second, _ = ProspectRepo(two).reserve_ordinals(session_id, count=3)
        two.commit()

    ordinals = [r.ordinal for r in (*first, *second)]
    assert sorted(ordinals) == [0, 1, 2, 3, 4, 5]


def test_an_abandoned_reservation_skips_its_tickers_and_the_next_pass_recovers_them(
    db: psycopg.Connection[TupleRow],
) -> None:
    # Section 19.7 as amended: a dead worker's reserved positions are not retried. What stops
    # that being data loss is that round-robin comes back around, so the tickers are skipped for
    # one pass rather than dropped from the sweep.
    repo = ProspectRepo(db)
    session_id = _session(db)

    abandoned, _ = repo.reserve_ordinals(session_id, count=2)  # nothing lands for these
    following, _ = repo.reserve_ordinals(session_id, count=3)

    assert [r.ticker for r in abandoned] == ["AMD", "NVDA"]
    # The next reservation carries on past them rather than retrying them...
    assert [r.ordinal for r in following] == [2, 3, 4]
    # ...and both abandoned tickers come round again within one further pass.
    assert {"AMD", "NVDA"} <= {r.ticker for r in following}


def test_a_reservation_is_refused_once_the_session_has_moved_on(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.claim_session("worker-a", lease_seconds=60.0)

    with pytest.raises(ConflictError):
        repo.reserve_ordinals(session_id, count=1, worker="worker-b")
```

Import `Rotation` in the test module: `from cracktrade.prospect import Rotation`.

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/test_prospect_repo.py -k reserv -v
```

Expected: FAIL, `AttributeError: 'ProspectRepo' object has no attribute 'reserve_ordinals'`.

- [ ] **Step 3: Implement**

In `src/cracktrade/api/repos/prospect.py`, add the import `from cracktrade.prospect import Reservation, Rotation` and the method next to `record_tick`:

```python
    def reserve_ordinals(
        self, session_id: UUID, *, count: int, worker: str | None = None
    ) -> tuple[tuple[Reservation, ...], ProspectSessionRow]:
        """Take the next ``count`` positions in the sweep, advancing the cursor past them.

        The cursor moves *before* the work rather than after it, which is what lets several
        ticks run at once: reservation is the serialisation point, and it is one cheap
        statement, so a 22-second search no longer has to hold a transaction open across it.

        The consequence is section 19.7's amended invariant -- a worker that dies abandons the
        positions it reserved, and those tickers are skipped for the current pass rather than
        retried. Round-robin brings them back on the next one.

        The advance is arithmetic on the flattened ordinal, done in SQL because it must be
        atomic under concurrent workers. :meth:`~cracktrade.prospect.Rotation.reserve` performs
        the same advance as a value, and a test asserts the two agree across a wrap-around.

        Raises:
            ValueError: ``count`` is not positive.
            ConflictError: the session is not running, or is no longer held by ``worker``.
        """
        if count < 1:
            msg = f"a reservation needs a positive count, got {count}"
            raise ValueError(msg)
        row = self._fetch_one(
            f"""
            WITH before AS (
              SELECT id,
                     passes_completed * cardinality(universe) + cursor_index AS start_ordinal,
                     cardinality(universe) AS size
              FROM prospect_session
              WHERE id = %s AND status = 'running'
                AND (%s::text IS NULL OR claimed_by = %s)
              FOR UPDATE
            )
            UPDATE prospect_session s SET
              cursor_index = (before.start_ordinal + %s) %% before.size,
              passes_completed = (before.start_ordinal + %s) / before.size,
              heartbeat_at = clock_timestamp()
            FROM before
            WHERE s.id = before.id
            RETURNING before.start_ordinal, {_SESSION_COLUMNS_QUALIFIED}
            """,
            (session_id, worker, worker, count, count),
        )
        if row is None:
            raise ConflictError(
                f"prospecting session {session_id} is not running, or is no longer held by "
                f"{worker or 'this worker'}"
            )
        start_ordinal = int(row[0])
        session = _session(row[1:])
        size = len(session.universe)
        rotation = Rotation(
            session.universe, index=start_ordinal % size, passes=start_ordinal // size
        )
        reserved, _ = rotation.reserve(count)
        return reserved, session
```

`RETURNING` needs the session columns qualified with the `s` alias because `before` is joined in. Add next to `_SESSION_COLUMNS`:

```python
_SESSION_COLUMNS_QUALIFIED = ", ".join(
    f"s.{column.strip()}" for column in _SESSION_COLUMNS.replace("\n", " ").split(",")
)
```

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/api/test_prospect_repo.py -k reserv -v
```

Expected: 5 passed.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add src/cracktrade/api/repos/prospect.py tests/api/test_prospect_repo.py
git commit -m "Reserve cursor positions before the search rather than after it"
```

---

### Task 4: Split the cursor out of `record_tick`

`record_tick` currently advances the cursor *and* counts the tick. Task 3 took over the advance, so the counting half must stop writing a cursor or it will undo reservations made by ticks still in flight.

**Files:**
- Modify: `src/cracktrade/api/repos/prospect.py` (`record_tick`, lines 228-281)
- Modify: `src/cracktrade/api/worker/prospect.py` (the `record_tick` call, around line 167)
- Test: `tests/api/test_prospect_repo.py`, `tests/api/test_prospect_worker.py`

**Interfaces:**
- Consumes: `reserve_ordinals` from Task 3.
- Produces: `ProspectRepo.count_tick(session_id: UUID, *, failed: bool = False, worker: str | None = None) -> ProspectSessionRow`. `record_tick` is **removed**, not deprecated.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_prospect_repo.py`:

```python
def test_counting_a_tick_does_not_move_the_cursor(
    db: psycopg.Connection[TupleRow],
) -> None:
    # Counting must not touch the cursor: other ticks are in flight against positions this
    # session has already reserved, and rewriting the cursor here would hand them out twice.
    repo = ProspectRepo(db)
    session_id = _session(db)
    repo.reserve_ordinals(session_id, count=3)

    after = repo.count_tick(session_id)

    assert (after.cursor_index, after.passes_completed) == (0, 1)
    assert (after.ticks_completed, after.ticks_failed) == (1, 0)


def test_a_failed_tick_is_counted_separately(db: psycopg.Connection[TupleRow]) -> None:
    repo = ProspectRepo(db)
    session_id = _session(db)

    after = repo.count_tick(session_id, failed=True)

    assert (after.ticks_completed, after.ticks_failed) == (0, 1)
```

Note the first assertion: `UNIVERSE` has 3 tickers, so reserving 3 wraps to index 0, pass 1 — and counting must leave that alone.

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/test_prospect_repo.py -k count_tick -v
```

Expected: FAIL, `AttributeError: 'ProspectRepo' object has no attribute 'count_tick'`.

- [ ] **Step 3: Implement**

Replace `record_tick` in `src/cracktrade/api/repos/prospect.py` with:

```python
    def count_tick(
        self, session_id: UUID, *, failed: bool = False, worker: str | None = None
    ) -> ProspectSessionRow:
        """Count a finished tick against the session's tallies.

        Deliberately does **not** touch the cursor. Positions are handed out by
        :meth:`reserve_ordinals` before the work starts, and several ticks are in flight at
        once; writing a cursor here would rewind past reservations other ticks are still
        working, and hand those tickers out a second time.

        A failed tick is counted separately and is not fatal: one ticker whose history the
        provider cannot supply must not stop the sweep from reaching the other nineteen.

        ``worker`` is the ownership check, and it closes the same race it always did. A search
        holds a child process for minutes; if the heartbeat connection is down long enough for
        the lease to lapse, another worker takes the session over while this one is still
        computing. The candidate insert shares this transaction, so a refused count leaves
        nothing behind.

        Raises:
            ConflictError: the session is not running, or is no longer held by ``worker``.
        """
        row = self._fetch_one(
            f"""
            UPDATE prospect_session SET
              ticks_completed = ticks_completed + %s,
              ticks_failed = ticks_failed + %s,
              heartbeat_at = clock_timestamp()
            WHERE id = %s AND status = 'running'
              AND (%s::text IS NULL OR claimed_by = %s)
            RETURNING {_SESSION_COLUMNS}
            """,
            (0 if failed else 1, 1 if failed else 0, session_id, worker, worker),
        )
        if row is None:
            raise ConflictError(
                f"prospecting session {session_id} is not running, or is no longer held by "
                f"{worker or 'this worker'}"
            )
        return _session(row)
```

In `src/cracktrade/api/worker/prospect.py`, `tick` no longer computes `advanced`. Replace the reservation and record block so `tick` takes its position rather than deriving it — change its signature to accept a `Reservation`:

```python
def tick(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    reservation: Reservation,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    control: RunControl | None = None,
    owner: str | None = None,
) -> TickOutcome:
```

Inside, replace `rotation = Rotation(...)` / `ticker = rotation.current` with `ticker = reservation.ticker`, replace `seed=session.seed + rotation.ordinal` with `seed=session.seed + reservation.ordinal`, delete `advanced = rotation.advance()`, and replace the `repo.record_tick(...)` call with:

```python
        repo.count_tick(session.id, failed=candidate is None, worker=owner)
```

Update `claim_and_tick`'s call site to reserve one position first:

```python
        with unit_of_work_on(connection) as work:
            reserved, _ = ProspectRepo(work.connection).reserve_ordinals(
                claimed.id, count=1, worker=worker_name()
            )
        outcome = tick(connection, claimed, reserved[0], provider=provider, ...)
```

- [ ] **Step 4: Fix the existing tests that assert the old behaviour**

`tests/api/test_prospect_worker.py` has tests asserting `tick` advances the cursor. Their intent survives; their mechanism moves. Update:
- `test_a_tick_stores_what_it_found_and_advances_the_cursor` — reserve first, pass the reservation, and assert `cursor_index == 1` after the *reservation*, with `ticks_completed == 1` after the tick.
- `test_the_cursor_wraps_and_counts_a_pass` — drive it through `reserve_ordinals` rather than repeated `tick` calls.
- `test_each_ticker_in_a_pass_gets_its_own_seed` — assert distinct `reservation.ordinal` values feed distinct candidate seeds.
- `test_a_takeover_mid_tick_leaves_nothing_behind` — unchanged in intent; the refusal now comes from `count_tick`.

Do not weaken any assertion to make it pass. If a test's intent no longer applies, delete it and say so in the commit body.

- [ ] **Step 5: Run the full prospecting suite**

```bash
uv run pytest tests/api/test_prospect_repo.py tests/api/test_prospect_worker.py tests/test_prospect.py -v
```

Expected: all pass.

- [ ] **Step 6: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add -A
git commit -m "Stop counting a tick from rewriting the cursor"
```

---

### Task 5: The prefetch horizon

P children fetching their own histories would put ~320 calls/min on the provider and could hand two ticks different bars for the same ticker. The parent fetches instead, deduplicated, over a rolling horizon with a TTL.

**Files:**
- Create: `src/cracktrade/prospect/prefetch.py`
- Modify: `src/cracktrade/prospect/__init__.py` (export)
- Test: `tests/test_prefetch.py`

**Interfaces:**
- Consumes: `MarketDataProvider` protocol (`fetch(ticker, start, end, interval) -> pd.DataFrame`).
- Produces: `PrefetchHorizon(provider, *, interval, ttl_seconds=60.0, max_threads=8)` with `frames_for(tickers: Iterable[str], start: date, end: date) -> dict[str, pd.DataFrame]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prefetch.py`:

```python
"""The parent-side fetch horizon -- spec section 19.7 as amended.

What matters here is that P concurrent ticks cost the provider one fetch per ticker rather than
P, and that a long sweep never searches against bars frozen at its first tick.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cracktrade.config import Interval
from cracktrade.prospect.prefetch import PrefetchHorizon

START = date(2024, 1, 1)
END = date(2024, 3, 1)


class CountingProvider:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame:
        self.calls.append(ticker)
        index = pd.DatetimeIndex(pd.date_range(start, end, freq="B"), name="Date")
        return pd.DataFrame(
            {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=index
        )


def test_a_ticker_asked_for_twice_is_fetched_once() -> None:
    provider = CountingProvider()
    horizon = PrefetchHorizon(provider, interval=Interval.D1)

    horizon.frames_for(["AMD", "SPY"], START, END)
    horizon.frames_for(["NVDA", "SPY"], START, END)

    assert provider.calls == ["AMD", "SPY", "NVDA"]


def test_every_requested_ticker_comes_back() -> None:
    horizon = PrefetchHorizon(CountingProvider(), interval=Interval.D1)

    frames = horizon.frames_for(["AMD", "NVDA", "SPY"], START, END)

    assert set(frames) == {"AMD", "NVDA", "SPY"}


def test_an_entry_older_than_the_ttl_is_fetched_again() -> None:
    # A sweep runs for days. A cache with no TTL would keep prospecting a history that stopped
    # at the session's first tick while the leaderboard claimed to be current.
    provider = CountingProvider()
    horizon = PrefetchHorizon(provider, interval=Interval.D1, ttl_seconds=0.0)

    horizon.frames_for(["AMD"], START, END)
    horizon.frames_for(["AMD"], START, END)

    assert provider.calls == ["AMD", "AMD"]


def test_a_ticker_the_provider_refuses_is_omitted_rather_than_fatal() -> None:
    # One delisting must not stop the other eleven ticks in flight.
    class Refusing(CountingProvider):
        def fetch(self, ticker, start, end, interval=Interval.D1):  # type: ignore[no-untyped-def]
            if ticker == "GONE":
                raise DataUnavailableError("nothing for GONE")
            return super().fetch(ticker, start, end, interval)

    horizon = PrefetchHorizon(Refusing(), interval=Interval.D1)

    frames = horizon.frames_for(["AMD", "GONE"], START, END)

    assert set(frames) == {"AMD"}
```

Import `DataUnavailableError` from `cracktrade.errors`.

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_prefetch.py -v
```

Expected: FAIL, `ModuleNotFoundError: No module named 'cracktrade.prospect.prefetch'`.

- [ ] **Step 3: Implement**

Create `src/cracktrade/prospect/prefetch.py`:

```python
"""Parent-side price history for a pool of prospecting ticks -- spec section 19.7.

A tick fetches its home ticker plus every sibling and control in its family, about eleven for
semiconductors. Run P of them at once and each fetches the same eleven, so twelve concurrent
semiconductor ticks would ask the provider for 132 histories to use 11 -- and
:class:`~cracktrade.prospect.session.SweepHistory` warns that a second fetch of one ticker can
return *different bars*, which would make two ticks disagree about the same instrument.

The parent fetches instead. Children receive frames and never touch the network, so there is
one rate-limit budget and one place to throttle.

**Raw frames, not loaded histories.** What is cached is exactly what the provider returned, and
each child still runs the identical :func:`~cracktrade.data.loader.load_history` path over it.
Caching the loaded result would put a second code path between the provider and a candidate,
and the whole claim of this design is that parallelism changes no number.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from threading import Lock
from typing import TYPE_CHECKING

import pandas as pd

from cracktrade.errors import CracktradeError
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from cracktrade.config import Interval
    from cracktrade.data import MarketDataProvider

logger = get_logger(__name__)

#: How long a fetched frame may be reused before it is fetched again.
#:
#: A sweep runs for days and a cache without a bound would keep searching a history that stopped
#: at the session's first tick, while ``last_bar_seen`` and the leaderboard both claimed to be
#: current -- the hazard :class:`~cracktrade.data.cache.FrameCache` carries and the reason it is
#: off by default. Sixty seconds is comfortably under one tick's median, so consecutive ticks in
#: a family still share their fetches.
DEFAULT_TTL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _Entry:
    frame: pd.DataFrame
    fetched_at: float


@dataclass(slots=True)
class PrefetchHorizon:
    """Fetches, deduplicates and ages out the histories a pool of ticks needs.

    Threads rather than processes: fetching is I/O-bound, and the CPU-bound half is what the
    process pool is for.

    Attributes:
        provider: where bars come from.
        interval: the session's frozen bar width.
        ttl_seconds: how long an entry may be reused.
        max_threads: concurrent fetches, which is also the provider's rate-limit budget.
    """

    provider: MarketDataProvider
    interval: Interval
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    max_threads: int = 8
    _entries: dict[str, _Entry] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def frames_for(
        self, tickers: Iterable[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """Every requested ticker that could be fetched, keyed by ticker.

        A ticker the provider refuses is *omitted* rather than raised: one delisting must not
        stop the other ticks in flight. The child that needed it fails its own tick and is
        counted as a failure, exactly as it would be if it had fetched for itself.
        """
        wanted = list(dict.fromkeys(tickers))
        missing = [ticker for ticker in wanted if self._stale(ticker)]
        if missing:
            self._fetch_all(missing, start, end)
        with self._lock:
            return {
                ticker: self._entries[ticker].frame
                for ticker in wanted
                if ticker in self._entries
            }

    def _stale(self, ticker: str) -> bool:
        with self._lock:
            entry = self._entries.get(ticker)
        return entry is None or (time.monotonic() - entry.fetched_at) >= self.ttl_seconds

    def _fetch_all(self, tickers: list[str], start: date, end: date) -> None:
        workers = min(self.max_threads, len(tickers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ticker, frame in zip(
                tickers,
                pool.map(lambda t: self._fetch_one(t, start, end), tickers),
                strict=True,
            ):
                if frame is None:
                    continue
                with self._lock:
                    self._entries[ticker] = _Entry(frame=frame, fetched_at=time.monotonic())

    def _fetch_one(self, ticker: str, start: date, end: date) -> pd.DataFrame | None:
        try:
            return self.provider.fetch(ticker, start, end, self.interval)
        except CracktradeError as failure:
            logger.info("prefetch of %s failed: %s: %s", ticker, type(failure).__name__, failure)
            return None


__all__ = ["DEFAULT_TTL_SECONDS", "PrefetchHorizon"]
```

Export `PrefetchHorizon` from `src/cracktrade/prospect/__init__.py`.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/test_prefetch.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add src/cracktrade/prospect/prefetch.py src/cracktrade/prospect/__init__.py tests/test_prefetch.py
git commit -m "Fetch a pool's histories once in the parent, not P times in the children"
```

---

### Task 6: The child entry point

A pool child must be a module-level function taking picklable arguments. It runs one tick against prefetched frames and returns a result rather than raising, so one bad ticker cannot take down the batch.

**Files:**
- Create: `src/cracktrade/api/worker/pool.py`
- Test: `tests/api/test_prospect_pool.py`

**Interfaces:**
- Consumes: `Reservation` (Task 2), `SweepParams`, `SweepHistory`, `prospect_once`, `StaticProvider`.
- Produces: `TickJob` and `TickResult` frozen dataclasses, and `run_tick(job: TickJob) -> TickResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_prospect_pool.py`:

```python
"""One tick, run the way a pool child runs it.

No database here: this is the compute half, and what matters is that it produces the same
candidate it would in the parent and never raises across the pool boundary.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cracktrade.api.worker.pool import TickJob, run_tick
from cracktrade.config import Interval
from cracktrade.indicators.catalogue import install
from cracktrade.prospect import Reservation, SweepParams, family_of

HOME = "AMD"
FAMILY = family_of(HOME)
TICKERS = (*FAMILY.members, *FAMILY.controls)
PARAMS = SweepParams(
    interval=Interval.D1, population=4, generations=2, min_trades=0,
    min_trades_per_year=0.0, lookback_days=1200,
)


@pytest.fixture(scope="module", autouse=True)
def _installed() -> None:
    install()


def _frame(bars: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, bars)))
    index = pd.DatetimeIndex(
        pd.date_range(end=pd.Timestamp.now().normalize(), periods=bars, freq="B").to_numpy(),
        name="Date",
    )
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": np.full(bars, 1_000_000.0)},
        index=index,
    )


def _job(*, ordinal: int = 0, frames: dict[str, pd.DataFrame] | None = None) -> TickJob:
    return TickJob(
        reservation=Reservation(ticker=HOME, ordinal=ordinal),
        params=PARAMS,
        seed=11 + ordinal,
        frames=frames if frames is not None else {t: _frame(700, i) for i, t in enumerate(TICKERS)},
        max_filled_fraction=1.0,
    )


def test_a_child_produces_a_candidate_for_its_reservation() -> None:
    result = run_tick(_job())

    assert result.error is None
    assert result.candidate is not None
    assert result.candidate.ticker == HOME
    assert result.reservation.ordinal == 0


def test_a_child_returns_a_failure_rather_than_raising() -> None:
    # An exception crossing the pool boundary would take down every other tick in flight.
    result = run_tick(_job(frames={}))

    assert result.candidate is None
    assert result.error is not None


def test_the_same_reservation_produces_the_same_candidate() -> None:
    # Parallelism must be observationally invisible; this is that claim at the unit level.
    first = run_tick(_job(ordinal=3))
    second = run_tick(_job(ordinal=3))

    assert first.candidate is not None
    assert second.candidate is not None
    assert first.candidate.strategy_yaml == second.candidate.strategy_yaml
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/test_prospect_pool.py -v
```

Expected: FAIL, `ModuleNotFoundError: No module named 'cracktrade.api.worker.pool'`.

- [ ] **Step 3: Implement**

Create `src/cracktrade/api/worker/pool.py`:

```python
"""The compute half of a prospecting tick, as a pool child runs it -- spec section 19.7.

Separated from :mod:`cracktrade.api.worker.prospect` because everything here must be picklable
and must not touch the database: a child receives a reservation and the frames it needs, and
returns what it found. Ownership, transactions and the cursor stay in the parent.

**A child never raises.** An exception crossing the pool boundary would take down every other
tick in flight, so a failure is *returned* and the parent counts it -- the same discipline
:func:`cracktrade.evolution.parallel._score_one` follows for one candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cracktrade.data import StaticProvider
from cracktrade.errors import CracktradeError
from cracktrade.indicators.catalogue import install
from cracktrade.log import get_logger
from cracktrade.prospect import SweepHistory, prospect_once
from cracktrade.optimize import TradeFloor

if TYPE_CHECKING:
    import pandas as pd

    from cracktrade.prospect import Candidate, Reservation, SweepParams

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TickJob:
    """Everything one child needs, and nothing it must not have.

    No connection, no provider and no session: a child cannot write, cannot fetch, and cannot
    advance a cursor. The frames come from the parent's
    :class:`~cracktrade.prospect.prefetch.PrefetchHorizon`, which is what keeps P children off
    the provider.
    """

    reservation: Reservation
    params: SweepParams
    seed: int
    frames: dict[str, pd.DataFrame] = field(repr=False)
    max_filled_fraction: float = 1.0


@dataclass(frozen=True, slots=True)
class TickResult:
    """What a child found, or why it did not."""

    reservation: Reservation
    candidate: Candidate | None
    error: str | None

    @property
    def failed(self) -> bool:
        return self.error is not None


def run_tick(job: TickJob) -> TickResult:
    """Prospect one ticker in this process. Never raises.

    The indicator catalogue is installed here because a fresh child has an empty one, and a
    search against an empty catalogue would fail for a reason that has nothing to do with the
    ticker.

    The history goes through :class:`~cracktrade.data.StaticProvider`, so the child runs the
    identical :func:`~cracktrade.data.loader.load_history` path the serial worker runs. What was
    prefetched is the provider's raw frame, not a loaded history, precisely so that no second
    code path can sit between the provider and a candidate.
    """
    install()
    history = SweepHistory(
        params=job.params,
        provider=StaticProvider(job.frames),
        max_filled_fraction=job.max_filled_fraction,
    )
    try:
        candidate = prospect_once(
            job.params.chassis_for(job.reservation.ticker),
            history,
            settings=job.params.ga_settings(),
            seed=job.seed,
            workers=1,
            objective_name=job.params.objective,
            trade_floor=TradeFloor(
                minimum=job.params.min_trades, per_year=job.params.min_trades_per_year
            ),
            segments=job.params.segments,
            holdout_fraction=job.params.holdout_fraction,
        )
    except CracktradeError as failure:
        logger.info("%s failed: %s", job.reservation.ticker, failure)
        return TickResult(reservation=job.reservation, candidate=None, error=str(failure))
    except Exception as failure:  # noqa: BLE001 - see module docstring
        logger.exception("%s raised an unexpected error", job.reservation.ticker)
        return TickResult(
            reservation=job.reservation,
            candidate=None,
            error=f"{type(failure).__name__}: {failure}",
        )
    return TickResult(reservation=job.reservation, candidate=candidate, error=None)
```

`workers=1` is deliberate and must not be made configurable: the process pool is the parallelism, and a nested pool inside each child would oversubscribe the machine by P times. Note this in the commit body.

**`ga_settings` must be moved, not duplicated.** `sweep_settings(params)` in
`src/cracktrade/api/worker/prospect.py:81` builds this today, but `pool.py` cannot import from
`prospect.py` — `prospect.py` imports `pool.py` in Task 7, and the cycle would not resolve. Move it
onto `SweepParams` in `src/cracktrade/prospect/session.py`, where the session's frozen question
already lives:

```python
    def ga_settings(self) -> GaSettings:
        """The search one tick runs, from this session's frozen question."""
        return GaSettings(population=self.population, generations=self.generations)
```

Then delete `sweep_settings` from `api/worker/prospect.py` and update its one caller and the test
`test_the_search_size_comes_from_the_sessions_frozen_question` in
`tests/api/test_prospect_worker.py` to call `params.ga_settings()`.

If Task 1 found determinism required pinned threads, add a pool initializer here that sets `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/`MKL_NUM_THREADS` to `1`, and reference Task 1's measurement in its docstring.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/api/test_prospect_pool.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add src/cracktrade/api/worker/pool.py tests/api/test_prospect_pool.py
git commit -m "Give a prospecting tick a picklable, database-free child entry point"
```

---

### Task 7: `claim_and_sweep` — the sliding window

**Files:**
- Modify: `src/cracktrade/api/worker/prospect.py` (replace `claim_and_tick`)
- Modify: `src/cracktrade/api/worker/runner.py` (the call in `run_forever`, line ~208)
- Modify: `src/cracktrade/api/settings.py`
- Test: `tests/api/test_prospect_worker.py`

**Interfaces:**
- Consumes: `reserve_ordinals` (Task 3), `count_tick` (Task 4), `PrefetchHorizon` (Task 5), `TickJob`/`TickResult`/`run_tick` (Task 6).
- Produces: `claim_and_sweep(connection, settings, *, provider=None, engine_settings=None, stop=None) -> ProspectSessionRow | None`. `claim_and_tick` is removed.

- [ ] **Step 1: Add the settings**

In `src/cracktrade/api/settings.py`, after `worker_poll_seconds`:

```python
    #: Prospecting ticks run concurrently in one worker process (spec section 19.7).
    #:
    #: Measured, not derived from the core count: see the scaling table in
    #: ``docs/superpowers/specs/2026-08-21-prospecting-parallelism-design.md`` section 8. A tick
    #: is single-core by construction -- the GA pool inside it is deliberately not used, because
    #: nesting one would oversubscribe the machine by this factor.
    prospect_parallelism: int = Field(default=8, ge=1)

    #: How long the parent may reuse a fetched history before fetching it again.
    prospect_prefetch_ttl_seconds: float = Field(default=60.0, gt=0)
```

Set the `prospect_parallelism` default to the value Task 1 measured, not to 8 if the measurement disagrees.

- [ ] **Step 2: Write the failing tests**

Add to `tests/api/test_prospect_worker.py`:

```python
def test_a_sweep_runs_several_ticks_and_counts_them_all(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))

    claim_and_sweep(
        db, _settings(parallelism=4), provider=_provider(), stop=_stop_after(4)
    )

    after = repo.require_session(session.id)
    assert after.ticks_completed + after.ticks_failed == 4
    assert after.cursor_index == 0
    assert after.passes_completed == 1


def test_every_tick_in_a_sweep_gets_a_distinct_seed(
    db: psycopg.Connection[TupleRow],
) -> None:
    # Reservations are the seed source. Two ticks sharing one would run the same search twice
    # and store it twice -- the duplicate-candidate bug the ordinal exists to prevent.
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))

    claim_and_sweep(db, _settings(parallelism=4), provider=_provider(), stop=_stop_after(4))

    stored = repo.leaderboard(session_id=session.id, survivors_only=False)
    seeds = [row.candidate.seed for row in stored]
    assert len(set(seeds)) == len(seeds)


def test_a_session_is_released_when_the_sweep_yields(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)

    claim_and_sweep(db, _settings(parallelism=2), provider=_provider(), stop=_stop_after(2))

    assert repo.require_session(session.id).claimed_by is None


def test_a_session_asked_to_stop_is_landed_rather_than_swept(
    db: psycopg.Connection[TupleRow],
) -> None:
    repo = ProspectRepo(db)
    session = _session(db)
    repo.request_stop(session.id)
    db.commit()

    claim_and_sweep(db, _settings(parallelism=4), provider=_provider())

    after = repo.require_session(session.id)
    assert after.status is ProspectStatus.STOPPED
    assert after.ticks_completed == 0
```

```python
def test_a_queued_run_stops_a_sweep_reserving_more(
    db: psycopg.Connection[TupleRow],
) -> None:
    # Section 19.7 promises a user's backtest waits at most one search. When the worker took one
    # tick per claim that was free; a sweep that holds its session has to ask, and this is the
    # test that says so.
    repo = ProspectRepo(db)
    session = _session(db, universe=(HOME, "NVDA", "MU", "INTC"))
    _queue(db, _seed(db), RunKind.BACKTEST)

    claim_and_sweep(db, _settings(parallelism=2), provider=_provider())

    after = repo.require_session(session.id)
    # It drained the window it had already dispatched and reserved nothing further.
    assert after.ticks_completed + after.ticks_failed == 2
    assert after.claimed_by is None
```

`_seed` and `_queue` are the helpers in `tests/api/test_worker.py:85` and `:99`; import them or
copy them into a shared fixture module rather than writing a third pair.

Add the two helpers near `_settings`:

```python
def _settings(*, parallelism: int = 2) -> ApiSettings:
    return ApiSettings(
        worker_lease_seconds=60.0,
        worker_heartbeat_seconds=30.0,
        prospect_parallelism=parallelism,
    )


def _stop_after(ticks: int) -> threading.Event:
    """An event a sweep sees as set once it has dispatched ``ticks`` positions.

    A sweep otherwise runs until something asks it to stop, which a test cannot wait for.
    """
    event = threading.Event()
    seen = 0

    class _Counting(threading.Event):
        def is_set(self) -> bool:
            nonlocal seen
            seen += 1
            return seen > ticks

    return _Counting()
```

Replace the existing `_settings()` definition rather than adding a second one.

- [ ] **Step 3: Run to verify they fail**

```bash
uv run pytest tests/api/test_prospect_worker.py -k sweep -v
```

Expected: FAIL, `ImportError: cannot import name 'claim_and_sweep'`.

- [ ] **Step 4: Implement**

In `src/cracktrade/api/worker/prospect.py`, replace `claim_and_tick` with:

```python
def claim_and_sweep(
    connection: psycopg.Connection[TupleRow],
    settings: ApiSettings,
    *,
    provider: MarketDataProvider | None = None,
    engine_settings: Settings | None = None,
    stop: threading.Event | None = None,
) -> ProspectSessionRow | None:
    """Claim a session and keep P ticks in flight until asked to yield.

    ``None`` when there is no session to work on.

    **Sliding window, not a batch.** P positions are reserved and dispatched, and as each child
    returns, its candidate is written and one more position is reserved and dispatched. A
    reserve-P/await-all loop would let the slowest tick in each wave gate the rest -- measured
    at 18-31s per tick, up to about 40% of the pool idle at the tail.

    **Yielding drains rather than cancels.** When a run is queued the sweep stops reserving and
    lets the ticks in flight finish, which bounds a user's wait at one search exactly as section
    19.7 promises. Cancelling would throw away compute that was about to produce a candidate.
    """
    with unit_of_work_on(connection) as work:
        claimed = ProspectRepo(work.connection).claim_session(
            worker_name(), lease_seconds=settings.worker_lease_seconds
        )
    if claimed is None:
        return None

    if claimed.stop_requested or (stop is not None and stop.is_set()):
        with unit_of_work_on(connection) as work:
            repo = ProspectRepo(work.connection)
            if claimed.stop_requested:
                logger.info("session %s stopping as requested", claimed.id)
                repo.stop_session(claimed.id)
            else:
                repo.release_session(claimed.id, worker_name())
        return claimed

    heartbeat = SessionHeartbeat(
        database_url=settings.database_url,
        session_id=claimed.id,
        worker=worker_name(),
        interval=settings.worker_heartbeat_seconds,
        _stop=threading.Event(),
        _cancelled=threading.Event(),
    )
    with heartbeat as beat:
        try:
            _sweep(
                connection,
                claimed,
                settings,
                provider=provider or YFinanceProvider(),
                engine_settings=engine_settings or load_settings(),
                should_yield=lambda: beat.cancelled or (stop is not None and stop.is_set()),
            )
        except ConflictError:
            logger.warning("session %s was taken over mid-sweep; discarding it", claimed.id)
            return claimed
        if not beat.cancelled and (stop is None or not stop.is_set()):
            score_due(connection, claimed, provider=provider, engine_settings=engine_settings)

    with unit_of_work_on(connection) as work:
        ProspectRepo(work.connection).release_session(claimed.id, worker_name())
    return claimed


def _sweep(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    settings: ApiSettings,
    *,
    provider: MarketDataProvider,
    engine_settings: Settings,
    should_yield: Callable[[], bool],
) -> None:
    """Keep the pool full until ``should_yield``, then drain it."""
    params = SweepParams.from_dict(session.params)
    horizon = PrefetchHorizon(
        provider,
        interval=params.interval,
        ttl_seconds=settings.prospect_prefetch_ttl_seconds,
    )
    parallelism = settings.prospect_parallelism
    owner = worker_name()

    def dispatch(pool: ProcessPoolExecutor, count: int) -> list[Future[TickResult]]:
        with unit_of_work_on(connection) as work:
            reserved, _ = ProspectRepo(work.connection).reserve_ordinals(
                session.id, count=count, worker=owner
            )
        jobs: list[Future[TickResult]] = []
        start, end = params.window()
        for reservation in reserved:
            family = family_of(reservation.ticker)
            tickers = (reservation.ticker, *family.members, *family.controls)
            frames = horizon.frames_for(tickers, start, end)
            jobs.append(
                pool.submit(
                    run_tick,
                    TickJob(
                        reservation=reservation,
                        params=params,
                        seed=session.seed + reservation.ordinal,
                        frames=frames,
                        max_filled_fraction=engine_settings.max_filled_fraction,
                    ),
                )
            )
        return jobs

    with ProcessPoolExecutor(max_workers=parallelism) as pool:
        pending = set(dispatch(pool, parallelism))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                _land(connection, session, future.result(), owner=owner)
            if not should_yield() and not _run_is_queued(connection):
                pending |= set(dispatch(pool, len(done)))


def _run_is_queued(connection: psycopg.Connection[TupleRow]) -> bool:
    """Whether a user is waiting on a backtest.

    Section 19.7 promises a queued run waits at most one search. When the worker took one tick
    per claim that fell out of the loop's ordering for free; now that a sweep holds its session
    across many ticks, the sweep has to ask. Asked once per completed tick rather than on a
    timer, which is exactly as often as the answer can change anything.
    """
    with unit_of_work_on(connection) as work:
        return RunRepo(work.connection).count(statuses=[RunStatus.QUEUED]) > 0


def _land(
    connection: psycopg.Connection[TupleRow],
    session: ProspectSessionRow,
    result: TickResult,
    *,
    owner: str,
) -> None:
    """Write one child's finding and count it, in one transaction.

    The candidate insert shares the transaction with the count and both are ownership-checked,
    so a session taken over mid-search leaves nothing behind -- half a tick would be worse than
    none.
    """
    with unit_of_work_on(connection) as work:
        repo = ProspectRepo(work.connection)
        if result.candidate is not None:
            candidate = result.candidate
            repo.add_candidate(
                session_id=session.id,
                ticker=candidate.ticker,
                discovered_at=candidate.discovered_at,
                last_bar_seen=candidate.last_bar_seen,
                seed=candidate.seed,
                candidate=to_dict(candidate),
                strategy_yaml=candidate.strategy_yaml,
                survived_transfer=candidate.survives_transfer,
                transfer_median=candidate.transfer.median_sibling_sharpe,
                transfer_control=candidate.transfer.median_control_sharpe,
            )
        repo.count_tick(session.id, failed=result.candidate is None, worker=owner)
    logger.info(
        "session %s: %s %s",
        session.id,
        result.reservation.ticker,
        "failed" if result.failed else "prospected",
    )
```

Imports to add: `from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait`, `from collections.abc import Callable`, `from cracktrade.api.worker.pool import TickJob, TickResult, run_tick`, `from cracktrade.api.repos import RunRepo`, `from cracktrade.api.repos.rows import RunStatus`, `from cracktrade.prospect import PrefetchHorizon, family_of`.

`SweepParams.window()` (in `src/cracktrade/prospect/session.py:99`) returns the `(start, end)` pair §19.7 requires, computed from the interval's reach. It is called once per dispatch rather than once per reservation, which is correct: every tick in a dispatch shares a window, and §19.7 requires the window be recomputed as the sweep runs, not frozen at session creation.

In `runner.py`, rename the import and call: `claim_and_tick` → `claim_and_sweep`, and update `run_forever`'s docstring, which currently says a session "is claimed for a single tick and released". It is now claimed for as long as no run is queued, and yields within one tick of one being.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/api/test_prospect_worker.py -v
```

Expected: all pass, including the pre-existing tests updated in Task 4.

- [ ] **Step 6: Gate and commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add -A
git commit -m "Keep P prospecting ticks in flight behind one session lease"
```

---

### Task 8: Prove parallelism changes no number

This is the gate on the whole design. If it fails, the design is wrong, not the test.

**Files:**
- Test: `tests/api/test_prospect_worker.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Write the test**

```python
def test_a_sweep_produces_the_same_candidates_however_wide_it_runs(
    db: psycopg.Connection[TupleRow],
) -> None:
    """The central claim of section 19.7 as amended: P is a throughput knob, not an input.

    A tick's seed is the session's seed plus its ordinal, so the same ordinal must produce the
    same candidate whether it ran alone or beside seven others. If this ever fails, the engine
    is reporting numbers that depend on how busy the machine was.
    """
    universe = (HOME, "NVDA", "MU", "INTC")

    def sweep(parallelism: int) -> dict[int, str]:
        session = _session(db, universe=universe, name=f"p{parallelism}")
        claim_and_sweep(
            db,
            _settings(parallelism=parallelism),
            provider=_provider(),
            stop=_stop_after(len(universe)),
        )
        stored = ProspectRepo(db).leaderboard(session_id=session.id, survivors_only=False)
        return {row.candidate.seed: row.candidate.strategy_yaml for row in stored}

    serial = sweep(1)
    parallel = sweep(4)

    assert serial and set(serial) == set(parallel)
    for seed, strategy_yaml in serial.items():
        assert parallel[seed] == strategy_yaml, f"seed {seed} differs between P=1 and P=4"
```

Both sessions use seed 11 from `_session`, so equal ordinals give equal seeds and the dicts are directly comparable.

The design document's section 7 states this test as P=1 against P=8; it runs at P=4 here so the suite stays quick, and the claim is identical — a four-ticker universe cannot fill eight slots anyway. Update the design document's section 7 to say P=4 in the same commit, so the two do not disagree.

- [ ] **Step 2: Run it**

```bash
uv run pytest tests/api/test_prospect_worker.py -k however_wide -v
```

Expected: PASS. **If it fails, stop.** Do not adjust the assertion. Diagnose: the likely causes are BLAS/numba thread nondeterminism (Task 1, step 3 — pin threads in the pool initializer) or a seed not derived from the reservation ordinal. Report the failure and its output rather than working around it.

- [ ] **Step 3: Run the causality suite untouched**

```bash
uv run pytest -m causality -v
```

Expected: all pass, with no edits to any causality test. These prove the four look-ahead layers hold; parallelism moved `prospect_once` across a process boundary and must not have touched them.

- [ ] **Step 4: Commit**

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
git add tests/api/test_prospect_worker.py
git commit -m "Pin that a sweep's results do not depend on how wide it ran"
```

---

### Task 9: Amend the spec

Code and spec must not disagree. §19.7 currently states an invariant this design deliberately weakened.

**Files:**
- Modify: `docs/ENGINE_SPEC.md` (§19.7, around lines 3660-3705)

**Interfaces:**
- Consumes: the measured numbers from Task 1.
- Produces: nothing.

- [ ] **Step 1: Replace the dead-worker bullet**

Find the bullet beginning "**A dead worker loses one tick, not the session.**" and replace it with:

```markdown
- **A dead worker loses up to P in-flight ticks, not the session.** This is the one place
  prospecting inverts §14.5. A run whose worker dies is failed and never re-queued, because
  re-running refetches retroactively adjusted prices and so measures something else. A *session*
  whose worker dies is simply claimable again once its lease lapses. Because the cursor is
  advanced at reservation rather than at completion (below), a dead worker abandons the
  positions it had reserved: completed ticks are already durable in their own transactions, so
  nothing measured is lost, and the abandoned tickers are skipped for the current pass rather
  than retried. Round-robin returns them roughly P ticks later. **Rejected: cursor slots as a
  leased work queue**, which would preserve the original "loses one tick" exactly — rejected on
  cost, a schema change and a second lease mechanism against a failure mode round-robin already
  self-heals.
```

- [ ] **Step 2: Replace the transaction paragraph**

Find "**A tick writes its candidate and its cursor in one transaction...**" and replace with:

```markdown
**A tick reserves its position before it searches, and writes its candidate and its count in one
transaction — decided 2026-08-21.** The cursor is advanced by `reserve_ordinals` before the work
starts, which is what allows several ticks to be in flight: reservation is the serialisation
point and is one cheap statement, so a multi-minute search no longer holds a transaction open
across it. Landing a result still checks ownership, and the candidate insert still shares that
transaction: a session taken over mid-search leaves nothing behind, because half a tick — a
candidate with no tick counted against it — would be worse than none.
```

- [ ] **Step 3: Replace the one-worker decision**

Find "**One worker, runs first — decided 2026-08-20.**" and amend it, keeping the original rejection of a *second process* and recording what changed:

```markdown
**One worker, runs first, P ticks wide — decided 2026-08-20, widened 2026-08-21.** Prospecting
shares the run worker's process and its loop checks a queued run before every reservation. The
worker keeps `prospect_parallelism` ticks in flight in a process pool and stops reserving when a
run appears, draining what is in flight, so a user who launches a backtest still waits at most
one search.

**Rejected, still: a second worker process for sweeps.** It would double the deployment surface
for no isolation that matters. Note that `claim_session`'s `FOR UPDATE SKIP LOCKED` means N
worker processes would each take a *different* session and already work today; that remains an
operational option and is not a design.

The 2026-08-20 form of this decision was one tick at a time, justified by both halves being
CPU-bound on the same cores. Measured 2026-08-21: prospecting used one core of thirty-two, at a
100% duty cycle and 22.2s per tick, because `plan_workers` sizes the GA pool from the search
budget rather than the core count and a prospecting search never clears the threshold. There was
nothing to contend for. A tick's seed is still the session's seed plus its ordinal, so a tick is
exactly reproducible and P is a throughput knob that changes no result — pinned by a test that
sweeps the same universe at P=1 and P=4 and compares the strategies bit for bit.
```

- [ ] **Step 4: Verify the spec and code agree**

Re-read §19.7 start to finish against `src/cracktrade/api/worker/prospect.py`. Every claim in the section must be true of the code. Fix whichever is wrong.

- [ ] **Step 5: Full gate and push**

```bash
./scripts/check.sh
```

Expected: `all green`.

```bash
git add docs/ENGINE_SPEC.md
git commit -m "Record that a prospecting sweep now runs P ticks wide"
git push origin main
```

---

## Notes for the executor

- **Task 1 is not optional and not reorderable.** `prospect_parallelism`'s default and Task 8's viability both depend on it. If the measurement shows scaling stops at 4, use 4 and say so.
- **Do not enable the GA pool inside a tick.** `workers=1` in `run_tick` is load-bearing; §19.1 measured that larger searches produce no better strategies, and a nested pool would oversubscribe the machine by P.
- **`docs/API.md` has drifted from the implementation** and is not the contract; the OpenAPI document is. Nothing in this plan changes the HTTP surface, so `web/src/api/schema.gen.ts` should not need regenerating — if `npm --prefix web run gen:api` produces a diff, something in this plan touched the API by accident.
- **If a test's intent stops applying, delete it and say so in the commit body.** Do not loosen an assertion to make it pass.
