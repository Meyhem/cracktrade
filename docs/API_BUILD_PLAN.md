# cracktrade — persistence + HTTP API build plan

**Status: plan for approval, then the working checklist.** Follows the repo convention: this
document exists for the duration of the build and is deleted once the build finishes; every
decision that must outlive it gets recorded in `docs/ENGINE_SPEC.md` in the same commit as the
code that implements it (Phase 0 and per-phase spec updates below).

Inputs: [`docs/DB_SCHEMA.sql`](DB_SCHEMA.sql) (draft v1, validated against live Postgres) and
[`docs/API.md`](API.md) (draft v1). Requesting this plan is taken as approval of both, with the
API.md §10 open questions resolved to their proposed defaults — flagged in §1 below; object to
any of them before Phase 0 lands.

---

## 1. Locked decisions

Defaults adopted from API.md §10 (each gets recorded in the spec in Phase 0):

| # | Decision | Choice |
| --- | --- | --- |
| D-1 | Verdict source | Latest succeeded walk-forward **against the current head** only |
| D-2 | Cancellation | `POST /runs/{id}/cancel` exists; cooperative cancel in the worker |
| D-3 | Run numbering | One per-strategy sequence shared across kinds |
| D-4 | Strategy names | Globally unique, case-sensitive (reverses spec §3.2 [DROP]) |
| D-5 | Auth | None; server binds to localhost. Single user. |

New decisions this plan introduces:

| # | Decision | Choice and why |
| --- | --- | --- |
| D-6 | Data access | **psycopg 3, raw SQL, no ORM.** The schema is hand-written SQL, results are stored verbatim jsonb, and an ORM is a second description of the schema that can drift from the first. Async pool (`psycopg_pool`) in the API process; plain sync connections in the worker. |
| D-7 | Migrations | **Hand-rolled engine** (user requirement): ordered SQL files, checksum-verified, forward-only. No down migrations — rollback of applied DDL on a database whose whole design is append-only would be a fiction; recovery is a new forward migration. |
| D-8 | Canonical DDL | The migration chain is the single source of truth. `docs/DB_SCHEMA.sql` becomes `migrations/0001_initial.sql` (plus §2.1 deltas) and the docs file is deleted once the spec records the schema — same rule as everywhere: one normative home per fact. |
| D-9 | Process model | Two processes from one codebase: the API server (FastAPI, async) and the worker (sync, claims runs from Postgres with `FOR UPDATE SKIP LOCKED`). The database is the queue; no broker. |
| D-10 | Live updates | Worker → Postgres `NOTIFY` → API `LISTEN` → SSE fan-out. Client polling stays the documented fallback. |
| D-11 | Web framework | FastAPI + uvicorn. Pydantic DTOs at the edge only; internal types are frozen dataclasses like the engine's. |
| D-12 | Entry point | New console script `cracktrade-api` with `serve`, `worker`, and `db migrate|status|verify` subcommands. The existing `cracktrade` CLI stays untouched — two interfaces, one library. |

"Enterprise code" is defined here as the gate this repo already enforces, extended to the new
surface: strict mypy, full ruff, layered architecture with no logic in routes, typed error
taxonomy mapped to problem+json at exactly one boundary, every state transition transactional,
integration tests against a real Postgres, and honest failure reporting end to end.

---

## 2. Architecture

```
src/cracktrade/api/
  __init__.py
  main.py               # cracktrade-api: serve | worker | db migrate/status/verify
  app.py                # FastAPI app factory (routes + middleware + lifespan pool)
  settings.py           # ApiSettings (env prefix CRACKTRADE_API_: database_url, host, port, ...)
  errors.py             # ApiError taxonomy (NotFound, Conflict, ValidationFailed, ...)
                        #   + the single problem+json exception handler
  schemas/              # pydantic request/response DTOs, one module per API.md section
  routes/               # thin: parse DTO -> call service -> render DTO. Nothing else.
  services/             # transactional workflows: save_version, promote, launch_run, diff, ...
  repos/                # one repo per table + the two views; typed rows; raw SQL
  db/
    pool.py             # async pool lifecycle (API) + sync connect (worker)
    uow.py              # transaction/unit-of-work helper
    migrate.py          # the migration engine (Phase 3)
    migrations/
      0001_initial.sql
  worker/
    runner.py           # claim loop, heartbeat, lease recovery
    execute.py          # engine invocation, progress + cancellation hooks,
                        #   result + series landing, failure -> category mapping
  events/
    notify.py           # NOTIFY emit (worker) / LISTEN relay (API)
    sse.py              # GET /events

tests/api/              # unit + integration (marker: db), mirrors the package layout
docker-compose.yml
.env.example
```

Layering rule, enforced by review and a lint test: `routes → services → repos → db`, never a
skip and never a cycle. The engine (`cracktrade.*` outside `api/`) is called **only** from
`services/` (validation, diffing, meta) and `worker/execute.py` (runs). Repos never import the
engine; routes never import repos.

Request flow: route parses DTO → service opens a unit of work → repos execute SQL → service
maps engine/db errors to the `errors.py` taxonomy → one middleware renders problem+json.
Run flow: `POST .../runs` inserts a queued row and commits → worker claims it → engine executes
with progress callbacks writing `run.progress` + `NOTIFY` → terminal update lands result,
series, and promoted columns in one transaction → SSE relays → clients refetch.

### 2.1 Schema deltas to fold into `0001_initial.sql`

The draft schema needs four worker/queue columns that the run views don't show but D-2/D-9
require. Amended now, while nothing is applied anywhere:

- `run.claimed_by text` — worker identity holding the lease;
- `run.heartbeat_at timestamptz` — liveness for lease recovery;
- `run.cancel_requested boolean NOT NULL DEFAULT false` — the API's only write into a live
  run; the worker checks it between generations/folds;
- `run_guard()` adjusted: `claimed_by`, `heartbeat_at`, `progress`, `status`+result landing
  stay mutable while non-terminal; `cancel_requested` may flip false→true only; identity
  fields stay frozen; terminal rows stay immutable.

---

## 3. Phases

Every phase ends with the full gate green (`uv run ruff check . && uv run ruff format . &&
uv run mypy && uv run pytest`, plus `uv run pytest -m db` with the compose database up) and a
commit + push, per the working agreement. Spec updates land in the same commit as the code
they describe.

### Phase 0 — Decisions into the spec, skeleton into the tree — **DONE**

Goal: the contract exists before the code does.

Landed: spec §14/§15 plus the §1.1, §1.2, §1.3 and §3.2 amendments; deps pinned; the package
skeleton with `ApiSettings`, the `ApiError` taxonomy, and the `cracktrade-api` entry point;
26 tests. Two additions beyond the plan, both cheap now and expensive later: the **layering
test** (`tests/api/test_layering.py`) that scans imports and fails if `routes → services →
repos → db` is violated or a repository reaches the engine — spec §15.3 promises that rule, so
something has to enforce it — and a settings test asserting the `CRACKTRADE_API_` prefix cannot
shadow the engine's `CRACKTRADE_` settings, since a collision there would let an interface
knob change a computed result.

- ENGINE_SPEC gains §14 *Persistence* (schema semantics: append-only enforcement, verdict and
  staleness derivation rules D-1, run lifecycle states, failure-category ↔ exit-code mapping)
  and §15 *HTTP interface* (conventions from API.md §0, decision table above). Amend §3.2 with
  the name-uniqueness reversal (D-4).
- Dependencies: `fastapi`, `uvicorn`, `psycopg[binary,pool]`, `sse-starlette`, `httpx` (tests).
  Pin per the repo's rule; probe nothing on faith — anything whose behaviour matters gets a
  pinning test when it's first relied on.
- Package skeleton from §2 with empty-but-typed modules, `ApiSettings`, `cracktrade-api`
  console script wired (`serve`/`worker`/`db` print "not implemented" and exit non-zero).
- Done when: gate green; `uv run cracktrade-api --help` shows the three subcommands.

### Phase 1 — Docker compose dev environment — **DONE**

Goal: `docker compose up -d` gives every developer (and the db-marked tests) the same Postgres.

Landed and verified from an empty volume: compose up → healthy in ~6s → `pytest -m db` passes
with no configuration. Two departures from the plan as written, both deliberate. Isolation is
**per test**, not per session — each test gets a database cloned from a session template, which
is a page-level copy inside the server and cheap enough to do per test, and it removes ordering
dependence entirely rather than reducing it. And the db tests skip on an *unreachable server*
rather than on an unset variable, so a fresh clone needs no environment at all; because a
skipping suite can report green having proved nothing, `CRACKTRADE_API_TEST_DB_REQUIRED=1`
turns the skip into a failure for CI. `_provision_template` in `tests/api/conftest.py` is the
seam Phase 2 fills with the real migration run.

- `docker-compose.yml`: `postgres:16-alpine`, named volume, healthcheck (`pg_isready`),
  port `${CRACKTRADE_DB_PORT:-5432}`, credentials from `.env` (with `.env.example` committed;
  `.env` git-ignored). Nothing else in the file — the API and worker run via `uv run` in dev.
- `ApiSettings.database_url` default matches the compose defaults, so zero-config works.
- Test infrastructure for everything that follows: a `db` pytest marker; a session fixture
  that connects to the compose server and creates one throwaway database per test session
  (plus per-test schema reset via `TEMPLATE`), skipping with an explicit reason when
  `CRACKTRADE_API_TEST_DATABASE_URL` is unset. README gains the three-command dev setup.
- Done when: fresh clone → `docker compose up -d` → `uv run pytest -m db` runs (against a
  trivial connectivity test) rather than skips.

### Phase 2 — Migration engine, from scratch

Goal: schema changes are ordered, verified, and refuse to lie. This is its own phase because
everything after it trusts it.

Engine (`db/migrate.py`), deliberately small — files, a ledger, and refusals:

- Migrations are `NNNN_name.sql` files; strictly increasing order of application.
- Ledger table `schema_migrations(version int PK, name text, checksum text, applied_at
  timestamptz, duration_ms int)` — created by the engine itself, idempotently.
- `migrate`: takes `pg_advisory_lock` (fixed key) so concurrent starts serialize; applies each
  pending file **in its own transaction**; records name + sha256 checksum.
- Refusals, each with a precise message: an applied file whose checksum changed (drift — the
  file was edited after application); a new file numbered below an applied one (history
  rewrite); a ledger version with no matching file (DB ahead of code).
- `status`: applied / pending / drift, one line each. `verify`: exit non-zero on any refusal
  condition without applying — the CI/pre-flight form.
- Known limitation, documented: everything runs in a transaction, so `CREATE INDEX
  CONCURRENTLY` is unsupported until a `-- migrate: no-transaction` directive is needed.
- `0001_initial.sql` = approved schema + §2.1 deltas. `docs/DB_SCHEMA.sql` deleted (D-8); the
  ER diagram semantics now live in spec §14.

Tests (all `db`-marked, against real Postgres): fresh apply → ledger correct; re-run → no-op;
edited applied file → refused; out-of-order insert → refused; two concurrent `migrate`
processes → one applies, both exit clean; the Phase-1 fixture now provisions databases by
running the real migration chain. Port the schema smoke assertions from the draft review
(verdict/staleness views, all twelve rejected mutations) into permanent tests here.

### Phase 3 — Data layer: pool, unit of work, repositories

Goal: typed, transactional access; SQL lives here and nowhere above.

- `pool.py` (async pool with lifespan hooks; sync connector for the worker), `uow.py` (async
  context manager owning one transaction; services compose repos inside exactly one).
- Repos: `StrategyRepo`, `VersionRepo`, `RunRepo`, `SeriesRepo`, plus read models over
  `strategy_overview`/`run_overview`. Frozen-dataclass rows, no pydantic below the edge.
- Error taxonomy mapping: unique violation → `Conflict`, FK violation → `NotFound`/`Conflict`,
  trigger exceptions surface as `InvariantViolation` (a bug indicator, 500 — the service layer
  must have prevented it).
- Repo-level behaviours with their own tests: version insert is head+1 within the caller's
  transaction (D-3 numbering for runs likewise); `FOR UPDATE SKIP LOCKED` claim query;
  overview queries return exactly the API.md list-row fields.
- Done when: every repo method has a `db` test; mutation-rejection tests prove the taxonomy
  mapping (not just that Postgres refused).

### Phase 4 — Engine extensions (library work, spec §-updates in the same commits)

Goal: everything API.md §9 needs from the library, plus the two run-control hooks D-2/D-9
imply. No HTTP code in this phase; the library stays independently testable.

1. **Series capture**: opt-in `capture_series=True` on `run_backtest`/`optimize`/
   `walk_forward` returning the series bundle (equity, benchmark equity, drawdown, close;
   monthly returns with in-market flags; rolling 12-month; per fold for walk-forward).
   Truncation-equivalence and causality suites must pass unchanged — series are outputs, not
   inputs, but the harness proves nothing leaked.
2. **Structured checks**: `ValidationReport.checks` → `tuple[Check, ...]` (`name`, `passed`,
   `stat`), contractual in `serialize`; `failures` becomes derived from it so verdict logic
   exists once.
3. **Registry introspection**: public accessor returning indicator types, parameters,
   outputs, required inputs — feeds `GET /meta`.
4. **Warnings as data**: `build_strategy` returns structured `[{path, message}]` warnings
   (shadowed stops per §3.7) alongside the strategy.
5. **Progress + cancellation hooks**: optional `on_progress(stage, percent)` and
   `should_stop() -> bool` callables threaded to the DE generation callback and fold loop.
   Cancellation raises a typed `RunCancelled`. Determinism test: same seed with and without
   callbacks → identical results (D12 discipline).

### Phase 5 — Services and the read/write endpoints (no runs yet)

Goal: everything the UI needs before anything executes: `GET /meta`, `POST /config/validate`,
strategies (list/create/import/fork/get), versions (list/get/save/restore/diff).

- `app.py` factory, problem+json middleware, OpenAPI served at `/api/v1/openapi.json` and
  kept honest by generating it from the same DTOs the tests exercise.
- Services carry the workflow rules from API.md: optimistic `base_version` (409), no-op save
  rejection, restore-as-append, fork lineage, §3.9 error pass-through with field paths and
  line numbers, diff grouping + section flags.
- Integration tests per endpoint (httpx against the app, real DB): happy path, each documented
  error status, and the contract details that bite later — a no-op save mints no version, a
  fork starts at v1, import stores YAML as written.
- Done when: the coverage matrix rows for list/detail/config/history/diff/dialogs (API.md §8,
  minus runs) are all exercised by tests.

### Phase 6 — Run pipeline: launch, worker, progress, series, SSE

Goal: the spine of the product — a run goes queued → running → terminal with honest progress
and an immutable record.

- Launch endpoints (`POST .../runs`, 202) pinning the head version and assigning `number`.
- Worker (`cracktrade-api worker`): claim → heartbeat loop → execute via Phase-4 hooks →
  land result + series + promoted columns (`is_credible`, `suppressed`) in one transaction →
  `NOTIFY`. Failure mapping from the library's typed exceptions to the four categories /
  exit codes; engine error text preserved verbatim.
- Lease recovery: on claim and on a sweep interval, runs `running` with a stale heartbeat are
  failed honestly (`engine_failure`, "worker terminated mid-run") — never silently re-queued,
  because a re-run on refetched data is a different measurement.
- Cancellation (D-2): API flips `cancel_requested`; queued runs cancel immediately; running
  ones when the worker next checks. Terminal state `cancelled`, no result, no error.
- `GET /runs` (+ filters, headline extraction per kind), `GET /runs/{id}` (result verbatim,
  checks passthrough, config_diff), series endpoints + CSV streaming, `GET /events` SSE
  backed by LISTEN/NOTIFY.
- Tests: a fake-engine worker harness for lifecycle/cancel/lease cases (deterministic, fast);
  one real end-to-end run per kind against a stub `MarketDataProvider` (offline, fixture
  bars — no yfinance in tests); SSE relay test; suppression honesty test (list rows carry no
  withheld figures).

### Phase 7 — Promotion and the verdict surface

Goal: close the loop that makes the product coherent.

- `POST /runs/{id}/promote`: one transaction — strategy (`origin: promoted`,
  `origin_not_credible` snapshot per the design's "the verdict travels"), v1 from
  `optimized_yaml`, auto-queued backtest. 409 on wrong kind/state.
- Verdict wiring end to end: list filter chips, detail verdict block with failures,
  promoted-warning derivation (shown until the strategy's own head-version walk-forward
  passes), stale flags everywhere runs render.
- Tests: promote-from-not-credible carries the warning; a later credible walk-forward clears
  it; verdict flips exactly per D-1 when the head moves.

### Phase 8 — Hardening and closure

Goal: the difference between "works" and "enterprise".

- Concurrency: parallel saves to one strategy (exactly one 201), promote vs save races,
  two workers on one queue, SSE under client churn.
- Operational honesty: structured logs to stderr (stream discipline §13.2 applies to the API
  processes too), request IDs, slow-query log at the pool, graceful shutdown (server drains;
  worker finishes or heartbeat-fails its claim), `/api/v1/health` (DB + migration status —
  serve refuses to start on pending migrations).
- Docs: README dev/run sections final; spec §14/§15 complete; API.md reconciled with what
  shipped (it drifts or it dies — same rule as the spec).
- Delete this file. The build is finished when the plan is redundant.

---

## 4. Explicit non-goals (this build)

- The React/TS/Mantine UI — separate plan once this API is real enough to develop against.
- Auth, multi-user, production deployment/compose profiles, TLS.
- Server-side chart PNG rendering (client-side per API.md §6).
- Price history stored in Postgres (the engine's file cache remains the cache).
- Migration rollbacks (D-7) and `CREATE INDEX CONCURRENTLY` (documented limitation).

## 5. Risks worth naming now

- **Engine runs are minutes long.** The worker is a separate process precisely so a 25-minute
  walk-forward can't starve the event loop; the risk left is a fetch hanging inside a run —
  bounded by cancellation (D-2) and lease recovery, not by timeouts pretending to know how
  long a search should take.
- **yfinance in the loop.** All tests use a stub provider; only ad-hoc manual runs touch the
  network. A flaky provider must surface as `market_data` failures with the engine's own
  error text, never as a hung queue.
- **Serialization drift.** The API re-exposes `serialize.to_dict` verbatim; a library change
  to a contractual property is an API change. One test pins the serialized shape per result
  type against a golden fixture so the break is loud.
