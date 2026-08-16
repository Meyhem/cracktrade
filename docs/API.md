# cracktrade HTTP API — draft v1

**Status: proposal for review, not yet normative.** Once approved, the decisions here get
recorded in `docs/ENGINE_SPEC.md` (the only normative document) and this file becomes the
implementation reference. Companion: [`docs/DB_SCHEMA.sql`](DB_SCHEMA.sql).

Scope: every endpoint the Claude-Design UI (`Cracktrade.dc.html`) needs — no more. The UI is a
React + TypeScript + Mantine SPA; the API is a thin interface over the library, per the
architecture rule that everything of substance lives in the library and interfaces only parse
and render.

Architecture in one paragraph: one FastAPI-style process serves this API; a worker (in-process
task or sibling process) claims queued runs from Postgres (`FOR UPDATE SKIP LOCKED`), executes
the engine, streams progress into `run.progress`, and lands the serialized result. The database
is the queue — no broker. Progress reaches the browser over one SSE channel with polling as the
fallback.

---

## 0. Conventions

- Base path `/api/v1`. JSON request/response bodies, UTF-8.
- **Result payloads are the engine's serialization.** Wherever a response embeds a run result,
  it is `cracktrade.serialize.to_dict(...)` verbatim — fields plus the contractual derived
  properties (`is_credible`, `failures`, `fold_win_rate`, `improvement_pct`,
  `overfitting_gap_pct`, `at_bound`, ...). The API never reshapes or recomputes engine numbers.
  Non-finite floats are `null` (spec §13.1).
- Timestamps ISO-8601 UTC (`2026-08-16T09:41:00Z`); dates plain `YYYY-MM-DD`; IDs are UUIDs.
- Errors are `application/problem+json`: `{type, title, status, detail}`. Config-validation
  failures additionally carry `errors: [{path, message, line?, suggestion?}]` — the spec §3.9
  contract (aggregated, dotted paths, YAML line where recoverable, did-you-mean for unknown
  keys) so the editor can attach each error to its field.
- Status codes: `201` created, `202` accepted (run launched), `409` conflict (stale
  `base_version`, duplicate name, wrong run state), `422` config invalid, `404` unknown id.
- Pagination: `?limit=` (default 50) + `?offset=` on `GET /runs` only; every other collection
  is small by nature (personal tool). No auth — single user, local. If that changes, auth is a
  middleware concern and an `owner_id` column; no endpoint shape changes.
- **There are no DELETE endpoints.** Strategies, versions, and runs are never deleted —
  append-only is a product guarantee, enforced by the schema.

---

## 1. Meta

### `GET /meta`

Static facts the UI needs before rendering anything; cacheable for the session.

```jsonc
{
  "engine_version": "0.9.0",
  "trade_floor": 20,                    // MIN_TRADES_TO_JUDGE — suppression threshold
  "significance": 0.95,                 // deflated-Sharpe bar
  "instability_threshold": 0.5,
  "objectives": ["calmar", "sortino", "sharpe", "legacy_pnl"],
  "fold_schemes": ["anchored", "rolling"],
  "defaults": {
    "optimize":     { "objective": "calmar", "epochs": 10, "cache": true },
    "walk_forward": { "objective": "calmar", "epochs": 10, "folds": 6, "scheme": "anchored" }
  },
  "indicators": [                       // from the engine registry (spec §5.2)
    { "type": "sma", "parameters": [{ "name": "window" }], "outputs": ["<name>"], "requires": ["close"] },
    { "type": "macd", "parameters": [{ "name": "fast" }, { "name": "slow" }, { "name": "signal" }],
      "outputs": ["<name>_macd", "<name>_macdh", "<name>_macds"], "requires": ["close"] }
    // ...
  ],
  "exit_fields": [                      // priority chain, for the editor's shadowing hints
    { "name": "atr_stop_multiplier", "stop_priority": 1 },
    { "name": "trailing_stop_pct",   "stop_priority": 2 },
    { "name": "stop_loss_pct",       "stop_priority": 3 },
    { "name": "take_profit_pct" }, { "name": "signal" },
    { "name": "min_holding_days" }, { "name": "max_holding_days" }
  ],
  "limits": [                           // the "What this cannot tell you" banner lines
    "Ticker selection is hindsight — you chose the symbol knowing its history.",
    "Prices are retroactively adjusted; the same backtest run months apart uses different data.",
    "One ticker per strategy. Long only."
  ]
}
```

---

## 2. Config validation

### `POST /config/validate`

Dry-run validation — the engine's `build_strategy` with nothing executed and nothing stored.
Feeds three surfaces: the live editor (form ⇄ YAML sync + field errors), the import dialog's
preview ("parsed · 0 errors · 1 warning"), and the New dialog.

Request — exactly one of:

```jsonc
{ "yaml": "strategy:\n  name: momentum_v2\n..." }   // or
{ "config": { "strategy": { "name": "momentum_v2" }, "...": "..." } }
```

Response `200` (also when invalid — invalidity is the answer, not a transport error):

```jsonc
{
  "valid": false,
  "errors":   [ { "path": "exit", "message": "at least one exit mechanism must be set", "line": 14 } ],
  "warnings": [ { "path": "exit.trailing_stop_pct",
                  "message": "shadowed by atr_stop_multiplier — configured and has no effect" } ],
  "canonical_yaml": "strategy:\n  name: momentum_v2\n...",   // dump_strategy(...) when parseable
  "config": { "...": "parsed canonical form, when parseable" },
  "namespace": ["close", "open", "high", "low", "volume", "sma_long", "rsi_ind", "atr_14"],
  "searchable_parameters": [
    { "path": "indicators.sma_long.window", "value": 200, "low": 100, "high": 300,
      "searched": true, "explicit_bounds": false },
    { "path": "exit.atr_stop_multiplier",   "value": 2.5, "low": 1.25, "high": 3.75,
      "searched": true, "explicit_bounds": false }
  ],
  "required_warmup_bars": 200
}
```

Notes: warnings never block (spec §3.7); the "searched" checkbox and "set bounds" control in the
editor read/write the config's own `optimize:` key (spec §9.1) — search bounds are config, not
separate state. `namespace` and `searchable_parameters` power the "available names" chips and
the per-indicator search-range line.

---

## 3. Strategies

### `GET /strategies`

The list view. Query: `search=` (matches name or ticker, the top-nav search box),
`verdict=credible|not_credible|unvalidated|never_run` (the filter chips; omit for All).

Rows come from the `strategy_overview` view:

```jsonc
{
  "strategies": [{
    "id": "…", "name": "momentum_v2", "ticker": "NVDA",
    "start_date": "2018-01-01", "end_date": "2025-12-31",
    "head_version": 7, "edited_at": "…", "created_at": "…",
    "origin": "forked",
    "lineage": { "parent_strategy_id": "…", "parent_name": "momentum_breakout",
                 "parent_version": 4, "origin_run_id": null },
    "origin_not_credible": false,
    "counts": { "optimize": 4, "backtest": 2, "walk_forward": 2, "versions": 7 },
    "last_run": { "id": "…", "kind": "walk_forward", "status": "succeeded", "at": "…" },
    "verdict": "not_credible",          // credible | not_credible | unvalidated | never_run
    "verdict_run_id": "…",
    "verdict_note": "3 checks failed"   // chip sub-line, derived server-side per state
  }],
  "totals": { "all": 6, "credible": 1, "not_credible": 1, "unvalidated": 3, "never_run": 1 }
}
```

### `POST /strategies`

The New-strategy dialog (blank or minimal seed; the fork option routes to `/fork`).

```jsonc
// request
{ "name": "rsi_pullback", "ticker": "NVDA",
  "start_date": "2018-01-01", "end_date": "2025-12-31",   // end_date optional → today
  "seed": "minimal" }                                     // "minimal" | "empty"
```

Creates the strategy and v1 (`origin: created`), returns `201` with the detail shape (§3,
`GET /strategies/{id}`). No run is launched — the strategy starts never-run. `409` on duplicate
name, `422` with field errors otherwise.

### `POST /strategies/import`

```jsonc
{ "yaml": "…file contents…", "filename": "momentum_v2.yaml" }
```

Validates through §2 semantics. Invalid → `422` with `errors` (the dialog blocks). Valid →
`201`, strategy + v1 (`origin: imported`, YAML stored as written, note records the filename),
response includes `warnings` for the dialog's non-blocking hatch note.

### `POST /strategies/{id}/fork`

Fork button (head), the diff view's "Fork from v6" (explicit version), and the New dialog's
copy-existing option.

```jsonc
{ "name": "momentum_v3", "version": 6 }    // version optional → head
```

`201` → new strategy, `origin: forked`, v1 = exact copy of the parent config at that version.
A fork starts a fresh history at v1 — it does not inherit the parent's versions.

### `GET /strategies/{id}`

Detail header + Config tab in one call:

```jsonc
{
  "id": "…", "name": "momentum_v2", "origin": "forked",
  "lineage": { "...": "as in the list row, plus crumb-ready names" },
  "origin_not_credible": false,
  "head": {
    "version": 7, "created_at": "…", "note": "broker fee schedule changed",
    "config": { "...": "canonical parsed config" },
    "yaml": "strategy:\n  name: momentum_v2\n…"
  },
  "counts": { "optimize": 4, "backtest": 2, "walk_forward": 2, "versions": 7 },
  "verdict": {
    "state": "not_credible",
    "run_id": "…", "run_number": 22, "version": 7, "finished_at": "…",
    "summary": "out-of-sample +6.6% vs +149.0% buy-and-hold, profitable in 2/4 folds",
    "failures": [ "deflated Sharpe P=0.00, below the 0.95 bar for 4800 trials", "…" ],
    "meta": "4 folds · anchored · calmar"
  },
  "promoted_warning": null    // for promoted strategies whose own validation hasn't passed:
                              // { "origin_run_id", "origin_run_number", "parent_name", "text" }
}
```

The editor's own needs (namespace, searchable parameters, live errors) come from
`POST /config/validate` as the user types — the strategy payload stays a snapshot.

---

## 4. Versions, history, diff

### `GET /strategies/{id}/versions`

History tab, newest first. `change_summary` is computed against the previous version at read
time (never stored); `flags` are derived from which section the changes touched:

```jsonc
{ "versions": [{
    "version": 7, "head": true, "origin": "edited", "restored_from": null,
    "note": "broker fee schedule changed", "created_at": "…",
    "change_summary": [
      { "path": "execution.commission_pct", "old": 0.05, "new": 0.08 },
      { "path": "execution.slippage_pct",   "old": 0.05, "new": 0.1 }
    ],
    "flags": ["execution_changed"],      // execution_changed | universe_changed | restored
    "runs_against": { "optimize": 1, "backtest": 0, "walk_forward": 1 }
}]}
```

### `GET /strategies/{id}/versions/{n}`

One immutable version: `{version, origin, restored_from, note, created_at, config, yaml}`.
(The History tab's View button.)

### `GET /strategies/{id}/diff?from=6&to=7`

The diff view: grouped changes with consequences plus both YAML panes, `changed_lines` indexed
into each pane for highlighting:

```jsonc
{
  "from": { "version": 6, "origin": "restored", "restored_from": 3, "created_at": "…" },
  "to":   { "version": 7, "head": true, "created_at": "…" },
  "groups": [
    { "section": "execution",
      "changes": [ { "path": "execution.commission_pct", "old": 0.05, "new": 0.08 },
                   { "path": "execution.slippage_pct",   "old": 0.05, "new": 0.1 } ],
      "flags": ["execution_changed"] },
    { "section": "universe",   "changes": [], "flags": [] },
    { "section": "indicators", "changes": [], "flags": [] },
    { "section": "entry_exit", "changes": [], "flags": [] }
  ],
  "from_yaml": "…", "to_yaml": "…",
  "changed_lines": { "from": [8, 9], "to": [8, 9] }
}
```

### `POST /strategies/{id}/versions`

Save from the editor. Optimistic concurrency via `base_version` — the editor edits "based on
v7"; if the head moved (another tab, a restore), the save must not silently clobber it.

```jsonc
// request
{ "base_version": 7,
  "config": { "...": "full config, canonical form" },   // or "yaml": "…"
  "note": "widened the RSI window after fold 3" }
```

- `422` — validation failed: the save is rejected outright, no version is created, `errors`
  attach to fields (spec §3.9). A save that changes nothing is also rejected (`409`,
  `no_change`): a no-op save must not mint a version.
- `409` — `base_version` is no longer the head.
- `201` — `{ "version": {…v8…}, "stale_runs": 2 }` — how many runs against the old head just
  became stale, for the save panel's warning line.

### `POST /strategies/{id}/versions/{n}/restore`

The Restore dialog. Appends a new head that copies version `n` exactly
(`origin: restored`, `restored_from: n`). Body: `{ "note": "…" }` (optional).
`201` with the new version. Nothing is rewound and nothing is deleted; runs against the old
head simply become stale by derivation.

---

## 5. Runs

### `POST /strategies/{id}/runs`

Launch. Always against the current head — the server pins `version` at insert; there is no
version parameter (a failed run's "Re-run against v7" is just a fresh launch).

```jsonc
// request — params by kind, defaults from GET /meta
{ "kind": "backtest" }
{ "kind": "optimize",     "params": { "objective": "calmar", "epochs": 10, "cache": true } }
{ "kind": "walk_forward", "params": { "objective": "calmar", "epochs": 10,
                                      "folds": 6, "scheme": "anchored" } }
```

`202` with the run resource (status `queued`, its per-strategy `number` assigned). The UI
returns to the strategy immediately; progress arrives via §7. `seed` may be passed explicitly;
otherwise the server assigns and records it.

### `GET /runs`

One endpoint for four surfaces: the per-strategy run tabs (`strategy_id` + `kind`), the
All-runs page (no filter), the "Running now" sidebar (`status=queued,running`), and the Charts
tab's run picker (`strategy_id`, all kinds). Query: `strategy_id`, `kind`, `status`
(comma-lists allowed), `limit`, `offset`.

Rows are intentionally table-shaped — `headline` carries exactly the columns the run tables
print, extracted server-side from `result` so every list and the run page agree:

```jsonc
{ "runs": [{
    "id": "…", "number": 22, "kind": "walk_forward", "status": "succeeded",
    "strategy": { "id": "…", "name": "momentum_v2" },
    "version": 7, "stale": false,
    "queued_at": "…", "started_at": "…", "finished_at": "…", "elapsed_seconds": 1491.0,
    "params": { "objective": "calmar", "epochs": 10, "folds": 4, "scheme": "anchored" },
    "seed": 20240517,
    "progress": null,                        // while running: {stage, percent}
    "failure_category": null,
    "headline": {                            // by kind — exactly one of:
      // backtest:     { return_pct, benchmark_return_pct, excess_pp, trades,
      //                 max_drawdown_pct, entry_defined_pct, suppressed }
      // optimize:     { oos_return_pct, improvement_pct, overfitting_gap_pct,
      //                 test_cagr_pct, trials, suppressed }
      // walk_forward: { folds, scheme, combined_oos_pct, benchmark_pct,
      //                 profitable_folds, oos_trades, is_credible, failed_checks }
      "folds": 4, "scheme": "anchored", "combined_oos_pct": 6.6, "benchmark_pct": 149.0,
      "profitable_folds": 2, "oos_trades": 16, "is_credible": false, "failed_checks": 3
    },
    "promotable": false        // succeeded optimize/walk_forward runs
}], "total": 8 }
```

Suppression is honest in lists too: when `suppressed` is true the headline still carries the
trade count and definedness, and the UI renders "too few trades" — the API does not send the
withheld figures in list rows.

### `GET /runs/{id}`

The run detail views (optimization run, backtest, validation run, failed run). Everything from
the list row, plus:

```jsonc
{
  "…": "list-row fields",
  "result": { "…": "cracktrade.serialize.to_dict output, verbatim, by kind:" },
  //  backtest      → BacktestResult      (metrics, trades, vintage, benchmark,
  //                                       entry/exit definedness, active & shadowed stops)
  //  optimize      → OptimizationResult  (test/train/baseline metrics, changes with bounds,
  //                                       diagnostics, optimized_yaml, trades)
  //  walk_forward  → ValidationReport    (folds, benchmark, deflated, overfitting, stability,
  //                                       costs, intervals, failures, is_credible,
  //                                       optimized_yaml)
  "error": null,               // failed runs: { "category": "market_data", "exit_code": 3,
                               //   "message": "MarketDataError: NVDA … returned 0 bars\n…" }
  "checks": [                  // walk_forward only — the "Every check" table, structured.
    { "name": "fold_results",  "passed": false, "stat": "profitable in 2 of 4 folds" },
    { "name": "benchmark",     "passed": false, "stat": "+6.6% against +149.0%" },
    { "name": "deflated_sharpe", "passed": false, "stat": "P=0.00, bar is 0.95" },
    { "name": "overfitting",   "passed": false, "stat": "PBO 0.62" },
    { "name": "stability",     "passed": false, "stat": "a 10% nudge destroys 81% of the objective" },
    { "name": "costs",         "passed": false, "stat": "break-even at 1.7× costs" },
    { "name": "intervals",     "passed": false, "stat": "mean interval −6.1% … +14.8%" },
    { "name": "trade_count",   "passed": false, "stat": "16 of 20 needed" }
  ],
  "config_diff": [             // optimize/walk_forward: winning config vs the run's own
                               // base version — the Resulting-config pane and promote dialog
    { "path": "indicators.rsi_ind.window", "old": 21, "new": 16, "low": 11, "high": 32,
      "at_bound": false },
    { "path": "indicators.sma_long.window", "old": 200, "new": 300, "low": 100, "high": 300,
      "at_bound": true }
  ]
}
```

`checks` must come from the engine, not be recomputed by the API — see §9.

### `POST /runs/{id}/promote`

The Promote dialog: the run's winning config becomes a new strategy.

```jsonc
{ "name": "momentum_v2_opt22" }    // server default offered by GET /runs/{id}
```

`409` unless the run is a succeeded `optimize` or `walk_forward`. Otherwise, in one
transaction: create strategy (`origin: promoted`, parent = the run's strategy,
`origin_run_id` = this run, `origin_not_credible` snapshotted from the run/parent verdict at
this moment), create v1 from `optimized_yaml`, and queue a backtest — "so the promoted strategy
is never sitting there with no numbers at all".

`201` → `{ "strategy": {…}, "backtest_run": {…} }`.

### `POST /runs/{id}/cancel` — open question §10

`202`; run ends `cancelled`, no result, no error. The design has no cancel affordance, but a
background queue without one is operationally painful. Included in the enum either way.

---

## 6. Chart series and exports

Series are captured by the worker at run time and stored in `run_series` (see the schema for
why they can never be recomputed later). Fold `0` is the whole run; walk-forward runs also
carry folds `1..N` for the fold picker.

### `GET /runs/{id}/series`

Catalog: `{ "series": [ { "name": "equity", "folds": [0] }, …,
{ "name": "equity", "folds": [0,1,2,3,4] } ] }`.

### `GET /runs/{id}/series/{name}?fold=0`

```jsonc
{ "name": "equity", "fold": 0,
  "dates": ["2018-01-02", "…"], "values": [10000.0, …] }
```

Shapes by name — `equity`, `benchmark_equity`, `drawdown`, `close`: dates + values;
`monthly_returns`: `months` + `values` + `in_market` booleans (flat and absent are different —
the heatmap hatches months with no position); `rolling_12m_return`: dates + values. Everything
else the charts draw (trade markers, PnL distribution, cumulative PnL, yearly returns, fold
bars, stability surfaces, cost ladder, intervals) comes from `result` — no series needed.

### `GET /runs/{id}/series/{name}.csv?fold=0`

`text/csv`, streamed from the same stored points — the chart and the file are the same bytes.
("Every chart: PNG, series as CSV" — PNG export is client-side rendering; CSV is this.)

---

## 7. Progress events

### `GET /events` (SSE)

One channel for everything live; the sidebar queue and any open run page subscribe once.

```
event: run.updated
data: { "id": "…", "strategy_id": "…", "status": "running",
        "progress": { "stage": "optimize · generation 4 of 10", "percent": 41 } }

event: run.finished
data: { "id": "…", "strategy_id": "…", "status": "succeeded" }
```

`run.finished` carries no result — clients refetch `GET /runs/{id}`, so there is exactly one
code path for rendering a run. Fallback when SSE is unavailable: poll
`GET /runs?status=queued,running` at ~2s.

---

## 8. Screen → endpoint coverage

| Design surface | Endpoints |
| --- | --- |
| Top nav search | `GET /strategies?search=` |
| "What this cannot tell you" banner | `GET /meta` (`limits`) |
| New strategy dialog (blank/minimal) | `POST /strategies` |
| New strategy dialog (copy existing) / Fork button / "Fork from v6" | `POST /strategies/{id}/fork` |
| Import config dialog (preview → confirm) | `POST /config/validate` → `POST /strategies/import` |
| Strategy list + verdict filter | `GET /strategies` |
| Detail header, verdict banner, promoted warning | `GET /strategies/{id}` |
| Config tab: form ⇄ YAML, namespace chips, search ranges, live errors | `GET /strategies/{id}` + `POST /config/validate` |
| Save vN / diff-against-head panel | `POST /strategies/{id}/versions` |
| History tab | `GET /strategies/{id}/versions` |
| Version View / Diff view / compare-against picker | `GET /strategies/{id}/versions/{n}`, `GET /strategies/{id}/diff?from&to` |
| Restore dialog | `GET .../diff` (preview) → `POST .../versions/{n}/restore` |
| Optimize dialog / Backtest / Walk-forward buttons | `POST /strategies/{id}/runs` |
| Run tabs (Optimizations / Backtests / Validation), empty states | `GET /runs?strategy_id&kind` |
| All runs page | `GET /runs` |
| "Running now" sidebar | `GET /runs?status=queued,running` + `GET /events` |
| Optimization run view (OOS band, search-bought table, gap, params, diagnostics, resulting config) | `GET /runs/{id}` |
| Backtest view (vs buy-and-hold, simulation grid, trades, vintage, suppressed variant) | `GET /runs/{id}` + `GET /runs/{id}/series/*` |
| Validation run view (verdict, checks, folds, DSR/PBO, stability, costs, intervals) | `GET /runs/{id}` + series |
| Failed run view (category, engine error text, re-run) | `GET /runs/{id}` → `POST /strategies/{id}/runs` |
| Charts tab (run picker, fold picker, groups A–D, CSV) | `GET /runs?strategy_id`, `GET /runs/{id}/series[...]`, `.csv` |
| Promote dialog + auto-backtest | `GET /runs/{id}` (diff, default name) → `POST /runs/{id}/promote` |

---

## 9. Engine additions this API needs

Small, and all in the library (interfaces stay logic-free):

1. **Per-bar series capture.** Result objects carry metrics and trades but not the series the
   Charts tab draws. Add an opt-in series bundle to `run_backtest` / `optimize` /
   `walk_forward` (equity, benchmark equity, drawdown, close; monthly returns with in-market
   flags; rolling 12-month; per fold for walk-forward). Captured at run time for the vintage
   reason above.
2. **Structured verdict checks.** `ValidationReport.failures` is prose; the UI's
   "Every check, and what it means" table needs `checks: [{name, passed, stat}]` as a
   contractual property on `ValidationReport`, so the verdict logic exists in exactly one
   place. The API must not re-derive pass/fail from the underlying fields — duplicated verdict
   logic is precisely the drift this project exists to avoid.
3. **Registry introspection.** Expose the indicator registry (types, parameters, outputs,
   required inputs) for `GET /meta` — the data exists in the registry (spec §5.2); it needs a
   public accessor.
4. **Static config warnings as data.** Spec §3.7 defines shadowed-stop warnings at validation;
   `POST /config/validate` needs them returned as `[{path, message}]`, not log lines.

Everything else the UI shows is already in the serialized results.

---

## 10. Open questions (decisions needed before implementation)

1. **Stale walk-forwards and the verdict chip.** Proposed: a strategy's verdict comes only from
   a walk-forward against the *current head*; a credible run against v3 of a strategy now at v7
   leaves it *unvalidated* (the design itself says such a run "says nothing about the current
   version"). Alternative: latest walk-forward regardless of version, with a stale marker.
2. **Cancellation.** Proposed: include `POST /runs/{id}/cancel`. Not in the design, but a
   background queue without it means a hung yfinance fetch blocks the queue until process
   restart.
3. **Run numbering.** Proposed: one per-strategy sequence shared across kinds ("Backtest #9"
   and "Optimization run #22" can't collide within a strategy). Alternative: per-kind
   sequences, which read nicer ("Backtest #1") but make "#14" ambiguous without its kind.
4. **Strategy name uniqueness.** Proposed: globally unique, case-sensitive, since the UI
   addresses strategies by name in crumbs and promotion records. Reverses the §3.2 [DROP]
   note (which assumed no persistence) — needs recording in the spec.
5. **Auth.** Proposed: none — single user, bound to localhost. Revisit only if the app is ever
   exposed beyond the machine it runs on.
