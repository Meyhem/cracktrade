# cracktrade — web UI build plan

**Status: plan for approval, then the working checklist.** Follows the repo convention: this
document exists for the duration of the build and is deleted once the build finishes. Every
decision that must outlive it gets recorded in [`docs/ENGINE_SPEC.md`](ENGINE_SPEC.md) in the
same commit as the code that implements it.

Inputs: [`docs/UI_PROMPT.md`](UI_PROMPT.md) — the brief, normative for *what* the screens must
say — and the server's OpenAPI document, normative for what can be said at all.
[`docs/API.md`](API.md) has drifted and is not an input.

Phases 1–3 are merged to `main` (`0bf27b1`): the scaffold and typed data layer, the strategy
list with creation and import, and the run lifecycle. This plan covers phases 4–7, which is
every screen where a user reads a number.

---

## 1. Where the build actually is

| Brief | State |
| --- | --- |
| §2.1 Strategy list | **Done** — search, verdict filter, sort, lineage, run counts |
| §2.2 Detail shell | **Done** — header, breadcrumb, tabs, promoted-from-not-credible banner |
| §4 Launch dialogs, long-running run UX | **Done** — SSE + polling, progress, cancel, failure categories |
| §2.2 Run tables | **Done** — per-kind columns, staleness, empty states |
| §2.3–2.5 Run views | **Stub** — header and failure path only; result keys dumped as chips |
| §3 Config editor | **Placeholder** |
| §5 Charts tab | **Placeholder** |
| §2.6 History, diff, restore, comparison | **Placeholder** |
| §4 Validate, Fork, Promote | **Missing** — all three have live endpoints |

Three tabs route to `ComingSoon` ([`router.tsx:28`](../web/src/routes/router.tsx)). The strategy
detail index redirects to `config`, so *landing on a strategy currently shows a placeholder* —
which is the strongest argument in §4 below about ordering.

---

## 2. Data reality check

The brief predates the API and assumes mocked payloads. This section is what the server can
actually serve today, verified against the code rather than against `API.md`. It is the input
that decides how much of §5 is buildable and where the brief has to bend.

### 2.1 The editor is fully servable — better than the brief assumes

`POST /config/validate` already returns everything §3 needs, and returns it on a 200 even when
the config is invalid ([`strategies.py:57`](../src/cracktrade/api/routes/strategies.py)):

- `errors` / `warnings` as attributable issues — §3's error rendering and the errors-block-save,
  warnings-never-block distinction;
- `namespace` — the signal editor's autocomplete list, including the suffixed names a
  multi-output indicator contributes. This closes appendix item 9 of the brief, which lists it
  as an engine-side gap;
- `searchable_parameters` with `path`, `value`, `low`, `high` — both the inline
  "14 → search 7–21" range control and the count the launch dialog's no-parameters guard needs;
- `canonical_yaml` and `config` — the two-way form/YAML sync.

Consequence: the editor needs no engine work. It is large, but it is only UI.

### 2.2 Chart series — captured per run and per fold

Stored at run time and never recomputed ([`execute.py:156`](../src/cracktrade/api/worker/execute.py)),
readable via `/runs/{id}/series`, `/runs/{id}/series/{name}` and `/runs/{id}/series/{name}.csv`.
Fold `0` means the whole run; a walk-forward has no fold 0 and numbers its folds from 1.

`equity`, `benchmark_equity`, `drawdown`, `close`, `monthly_returns`, `rolling_12m_return`.

`monthly_returns` carries `in_market` alongside the values, so §5.5's "a month with no position
is not a 0% month" is servable rather than something to fake. The CSV route means §5.7's
"underlying series as CSV" is the same bytes the chart drew, not a recomputation.

This closes appendix items 5 and most of 6 of the brief.

### 2.3 What is still missing, chart by chart

| Chart | Source | Backtest / Optimize | Per fold |
| --- | --- | --- | --- |
| 1 Equity vs buy-and-hold | `equity`, `benchmark_equity` | ✅ | ✅ |
| 2 Drawdown | `drawdown` | ✅ | ✅ |
| 3 Price with trade markers | `close` + trades | ✅ | **✗ no trades** |
| 4 PnL distribution | trades | ✅ | **✗** |
| 5 Cumulative PnL by trade | trades | ✅ | **✗** |
| 6 Won against lost | trades | ✅ | **✗** |
| 7 Monthly heatmap | `monthly_returns` | ✅ | ✅ |
| 8 Yearly returns | `Metrics.yearly_returns` | ✅ / **partial** | ✅ |
| 9 Rolling 12m | `rolling_12m_return` | ✅ | ✅ |
| 10–15 Group D | `ValidationReport` | n/a | ✅ |
| 11 Parameter drift | `FoldResult.parameters` | n/a | ✅ |

Three real gaps:

**G-1 — `FoldResult` carries no trades.** Its fields are `index`, `train_bars`, `test_bars`,
`first_test_bar`, `last_test_bar`, `metrics`, `train_metrics`, `parameters`. `BacktestResult`
and `OptimizationResult` both carry `trades`; a fold does not. So selecting a specific fold on a
validation run renders Group A charts 1–2 and nothing of Group B — exactly the case §5.1's fold
selector exists to serve. This is the brief's appendix item 6, still open.

**G-2 — no OHLC, only `close`.** §5.3 chart 3 says "Close or OHLC", so close-only is compliant
and this is not a blocker. Recording it here so it is not rediscovered as a defect.

**G-3 — `OptimizationResult` has no `BenchmarkComparison`.** `BacktestResult.benchmark` holds a
full benchmark `Metrics`, which is where chart 8's benchmark bars come from. An optimization run
has `test_metrics` / `baseline_test_metrics` but no benchmark block, while its
`benchmark_equity` *series* is captured. So on an optimization run the benchmark is drawable as
a curve and not as yearly bars.

### 2.4 Promote has no diff endpoint

`GET /strategies/{id}/diff` compares two versions **of one strategy** and returns section-grouped
changes plus both YAML texts. §4's Promote dialog needs a run's produced config against the
parent strategy's config — a different pairing, and one nothing serves.

---

## 3. Open decisions

These change what gets built and are the reason this is a plan rather than a first commit.

| # | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| D-14 | **G-1, per-fold trades** | (a) extend `FoldResult` to carry its fold's `Trade` list — the walk-forward runner already evaluates each fold as a full backtest before discarding all but `metrics`; (b) ship the fold selector with Group B showing a "not recorded for folds" state | **(a)**, in phase 7. The engine already computes it; (b) writes an empty state that exists only to apologise for a field we chose not to keep, and §5.1 makes the per-fold view the whole point of the selector |
| D-15 | **G-3, optimization benchmark** | (a) add `BenchmarkComparison` to `OptimizationResult`; (b) derive yearly benchmark returns client-side from `benchmark_equity`; (c) omit benchmark bars on optimization runs | **(a)**. (b) puts a second definition of a published number in the client, which is the failure mode this repo is organised against |
| D-16 | **D-14/D-15 and stored results** | Both change the serialised result shape. Runs already in the database were stored under the old shape | Render defensively from what a run recorded; no backfill, no migration. A run is an immutable record of what was computed, and inventing a benchmark for an old one is the same lie as any other |
| D-17 | **Promote diff (§2.4)** | (a) generalise the diff service to compare two configs rather than two versions, exposed as `POST /config/diff`; (b) client-side text diff of `optimized_yaml` against the head's YAML | **(a)**. §4 calls this "the single most important anti-footgun in the app"; a text diff cannot produce the section grouping that separates "the ticker moved" from "an RSI window moved by one" |
| D-18 | **Charting library** | Not yet chosen. §5.7 forbids dual axes, requires shared crosshair/brush across charts 1–3, zero-inclusive axes, and PNG export | Decide at the top of phase 7, not now. Phases 4–6 need no charts beyond the run views' inline equity/drawdown, which the same choice will serve |

D-14, D-15 and D-17 are engine- and API-side work inside a UI plan. That is expected — the brief's
own appendix predicted it — but it means phases 4 and 7 each open with a server change and a spec
update before any component is written.

---

## 4. Phases

Ordering rationale. Two candidates argued for going first. The **config editor** is what the
detail page lands on, so today a strategy opens on a placeholder; the **run views** complete a
loop phase 3 left dangling, where a run can be launched and watched but never read. Run views
win: they are the cheapest large increment (every number is already stored and typed), they
unblock Promote, and §2.5's verdict screen is the one screen the whole product exists to show.
The editor is second because editing is what a user does *in response to* what a run said.

Charts precede history because the brief calls the charts tab the one users live in while
forming an opinion, while the comparison table — the most rule-heavy screen in the app — is the
last thing anyone needs.

Every phase ends with `./scripts/check.sh` green and a commit and push. Spec updates land in the
same commit as the code they describe.

### Phase 4 — The three run views, and the actions that hang off them

The stub in [`RunViewPage.tsx`](../web/src/features/runs/RunViewPage.tsx) keeps its header,
failure path and cancellation copy; everything below the fold gets built per kind.

- **§2.3 optimization** — the out-of-sample band with its explicit label, the three comparisons
  (improvement, overfitting gap as a two-bar comparison, trade count against the floor), the
  parameter-changes table with the range track and on-the-bound warnings, collapsed search
  diagnostics, resulting YAML, and the never-dismissible one-draw banner.
- **§2.4 backtest** — benchmark-led, strategy and buy-and-hold at equal weight with excess as the
  headline; metrics table in two columns; trade list sortable and filterable; the data vintage
  panel with a copyable digest; definedness; shadowed stops.
- **§2.5 validation** — the full-width boolean verdict, failed checks in the engine's own words,
  then a card per robustness check each with its one-line plain-English explanation. Checks are
  rendered, never recomputed: `Check` already carries `passed`, and a second implementation of
  the verdict is a second verdict.
- **Promote** (needs D-17) and **Fork** dialogs, both diff-before-confirm. Fork takes a version,
  so §4's "fork from any historical version" works from the run view and later from the timeline.
- **Validate** in the action bar.
- Mount `LimitsPanel` on the validation view — it is written and currently unreachable.
- Fix `searchableParameters={1}`, hardcoded at both `LaunchRunModal` call sites, from
  `/config/validate`. The no-parameters guard is written and can currently never fire.

Opens with D-17 (`POST /config/diff`) server-side.

### Phase 5 — The config editor

Form and YAML modes synchronised, per-field pin/bounds controls showing the effective search
range, the signal expression editor with `namespace` autocomplete, the parenthesisation fix
offered as an edit, the bare-numeric-literal hint, the two adjacent unit conventions made
explicit with resolved values, dirty state surviving navigation and warning on unload, and save
creating a version with note and diff preview. Errors attach to fields and collect in a summary
panel; warnings never block.

No server work. The largest single phase, and the one with the most component tests.

### Phase 6 — Charts

Opens with D-14, D-15 and D-18 — per-fold trades and the optimization benchmark land in the
engine with a spec update, then the tab is built against them.

Run selector with the seriousness ordering and stale marking, fold selector for validation runs
defaulting to Combined with §5.1's explanatory empty state, four labelled groups behind a sticky
nav rail, and §5.2's suppression rules treated as designed states rather than error handling.
Combined-view Group C stitches fold windows chronologically client-side, since a walk-forward
stores no whole-run series by construction.

The suppressed and absent-series states get tests. They are the states most likely to regress
into silently rendering a figure, and that regression is invisible until it has misled someone.

### Phase 7 — Version history and comparison

Timeline, section-grouped diff with universe and execution changes flagged, restore as an
append with its stale-run count and dirty-editor guard, and the comparison table with the Δ
comparability rules — same kind, same objective, same folds and scheme, or `—` with a reason;
empty cells rather than zeros; vintage-digest mismatch caveats; the trade-floor suppression; the
metric-across-versions chart; and the two permanent warnings about iterated search and hindsight.

The Δ rules get unit tests before the table renders. Every one of them is a rule about refusing
to show a number, and a comparison table that quietly compares the wrong two runs is precisely
the "authoritative and wrong" output the project is organised against.

---

## 5. Definition of done

`./scripts/check.sh` green; no `ComingSoon` route remaining; every screen in the brief's
Deliverable paragraph built with its loading, empty, failed and suppressed states; every decision
above recorded in `ENGINE_SPEC.md`; this file deleted.
