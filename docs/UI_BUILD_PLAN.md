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
| §3 Config editor | **Done** — both modes, per-field search controls, signal editor, save with diff |
| §5 Charts tab | **Done** — run and fold selectors, four groups, §5.2 as designed states |
| §2.6 History, diff, restore, comparison | **Placeholder** |
| §4 Validate, Fork, Promote | **Missing** — all three have live endpoints |

One tab still routes to `ComingSoon` ([`router.tsx`](../web/src/routes/router.tsx)): history.
The strategy detail index redirects to `config`, which as of phase 5 is the editor rather than a
placeholder.

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
| D-17 | **Promote diff (§2.4)** | (a) generalise the diff service to compare two configs rather than two versions, exposed as `POST /config/diff`; (b) client-side text diff of `optimized_yaml` against the head's YAML | **(a)** — **decided, built**. §4 calls this "the single most important anti-footgun in the app"; a text diff cannot produce the section grouping that separates "the ticker moved" from "an RSI window moved by one" |
| D-18 | **Charting library** | §5.7 forbids dual axes, requires shared crosshair/brush across charts 1–3, zero-inclusive axes, and PNG export | **Apache ECharts — decided, built** in phase 6. Already a dependency; `connect`, `getDataURL`, heatmaps and `markLine`/`markArea` cover §5.7 directly. Full import rather than `echarts/core`: a missed registration fails by silently not drawing part of a chart |

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

### Phase 4 — The three run views, and the actions that hang off them — **DONE**

Landed: `POST /config/diff` and its spec amendment; the searchable-parameter count wired to
`/config/validate`; `lib/result.ts`; all three run views; Promote, Fork and Validate; the limits
panel mounted. 52 web tests, 181 API tests.

Two things found by dumping real payloads to build against rather than reading the brief:

**`Trade.is_open` serialised as the string `"False"`** — a `numpy.bool_` reaching the
serializer's `str()` fallback. Truthy in JavaScript, so §5.2's "open trades are never counted"
would have dropped every trade from every aggregate. Fixed at the domain boundary; spec §11
amended; two repo tests.

**Suppression does not reach the `result` blob.** §15.2 promises withheld figures are never
sent, §15.1 requires the blob be passed through unaltered, and both cannot hold. §15.1 wins;
the floor is applied client-side at one chokepoint (`figuresOf`). Spec §15.2 clarified.

The original phase-4 text follows, for what each view was built to say.

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

### Phase 5 — The config editor — **DONE**

Landed: both modes over one draft, per-field pin/bounds controls with the engine's search range
inline, the signal editor with completion, the parenthesisation fix and the literal hint, the
two unit conventions resolved in words, drafts surviving navigation with an unload warning, and
save with note and diff preview. 83 web tests, 718 Python tests.

**The draft is text, and the form is a projection over it.** The other arrangement — an object
as the source of truth, serialised into the YAML pane on demand — is simpler until someone
imports a commented file, changes one window in the form, and finds their comments gone. Every
form write goes through `yaml`'s document API, which edits nodes in place. `save_version` stores
the text it is given verbatim as `config_yaml`, so this round trip is what decides whether a
user's own file survives being edited by the form. Verified end to end against a running server:
a form-shaped edit comes back byte-identical, comments included.

**One validator, and it is the server's.** The brief asks for client-side validation on every
change. What makes a configuration a strategy is the engine's judgement (spec §3.1), and a
second implementation of it in the form would agree until the schema next changed and then
disagree silently, in the screen whose job is to say what is wrong. So: debounced round trips to
`/config/validate`, with "checking" as a real state — the save gates on whether the answer
describes the text currently on screen, while the *display* of issues keeps the last answer so an
unfixed error does not blink on every keystroke. Two different claims, deliberately.

Two things found by looking at the live render rather than at the tests:

**The resolved-value hint was itself wrong.** `commission_pct: 0.05` rendered as "0.1% = $5.00"
— `percent()` rounds to one decimal, doubling the number in the hint whose entire purpose is to
stop someone misreading this field by a factor of a hundred.

**Unset optional fields offered a search toggle.** `exit.trailing_stop_pct` with no value is not
a stop being tuned between bounds; it is a stop that does not exist, and a control saying
"searched — range unknown" says the opposite.

Opens with a server change after all: **§3.9's promised line numbers were unreachable from the
API.** `POST /config/validate` parsed the YAML and threw the text away, so the line number the
spec promises existed for the CLI and nowhere else — an editor could mark a syntax error's line
and no other. `build_strategy` now takes an optional `source`, and path and line arrive as
separate fields so an error *with* a line does not lose the field attribution an error *without*
one keeps. Spec §3.9 amended.

### Phase 6 — Charts — **DONE**

**D-18 decided: Apache ECharts**, already a dependency and unused until now. Chosen against
§5.7 rather than on general merit: canvas rendering for a decade of daily bars, `connect` for a
genuinely shared crosshair and brush across charts 1–3, `getDataURL` for PNG export, and
first-class heatmaps, `markLine`, `markArea` and `visualMap` that charts 3, 7, 12 and 13 need.
The *whole* library is imported rather than the tree-shakeable `echarts/core` build: a missed
registration there fails by not drawing part of a chart, which is the one failure mode this
application cannot tolerate quietly.

D-14 and D-15 landed first, with a third engine change the brief implied and nothing provided:
§5.7 requires forward-filled bars to be marked on time-domain charts, and only their *count*
was recorded. `RunSeries.filled` now carries the dates (spec §8.1, amended).

**Where this deviates from §5.1, and why.** The brief says Group C stitches fold windows and
stays available in Combined view. The monthly grid does, and marks the months where two folds
each contributed a half rather than compounding them. Charts 8 and 9 aggregate over a calendar
year and a trailing twelve months, and in a six-fold walk-forward *every* such window crosses a
boundary — compounding across one states a return for a strategy that was never traded. Both
refuse in Combined view and point at the fold selector. That is narrower than §5.1's wording and
the only reading that does not invent a number.

§5.4's "total return recomputed with the single best trade removed" is reported in **currency**,
as two sums of realised P&L, not as a percentage. Removing a trade from a compounded curve
changes the capital every later trade was sized against, so an honest version needs the engine's
simulation; a percentage computed in the client would wear the headline return's label while
measuring something else. Both sums are of the same kind, which is what the question needs.

Five defects the tests were happy with and the live render was not, all found by looking:

1. **Five of nine charts drew nothing.** ECharts is rebuilt when Mantine's colour scheme
   resolves, and the option effect — keyed on the option, which had not changed — did not re-run,
   so the new instance never received one. No error, correct size, blank frame. Charts that
   happened to re-render afterwards healed themselves, which is why four worked and five did not.
   The option is now applied inside the effect that creates the instance.
2. **The price chart claimed a series "was not recorded"** in a walk-forward's combined view.
   It waives the trade floor (§5.2's sole exception) and had been waiving the *whole* state,
   turning "these folds ran different configurations" into a false statement about the run.
3. **The stability axis labelled a −20% perturbation "−120%"** — `StabilityPoint.multiplier` is
   a fractional change applied as `value * (1 + m)`, read here as a multiple, on the one chart
   whose entire subject is how far a parameter can move.
4. **The confidence intervals were plotted as fractions on an axis formatted `%`**, so a total
   return of +209% read as "+2%". They were also drawn with the stacked-invisible-offset trick,
   which breaks for a negative low — an interval running −33% to +209% was drawn entirely right
   of zero, directly above a caption saying it straddled zero. Now two charts on separate scales
   (the two statistics differ by three orders of magnitude and §5.7 forbids a second axis), each
   a two-point line segment, at the precision the CLI already uses for each.
5. **The deflated Sharpe's luck threshold was off-chart**, because ECharts does not extend an
   axis to fit a mark line. The bar sat alone looking like a result rather than a failure.

Also fixed in passing: `parameters_at_bound` serialises as `ParameterChange` objects, not names,
so the optimization view's "widen the range and run again" warning had never once rendered
against a real payload while the per-row badge beside it kept working.

### Phase 7 — Version history and comparison

Timeline, section-grouped diff with universe and execution changes flagged, restore as an
append with its stale-run count and dirty-editor guard, and the comparison table with the Δ
comparability rules — same kind, same objective, same folds and scheme, or `—` with a reason;
empty cells rather than zeros; vintage-digest mismatch caveats; the trade-floor suppression; the
metric-across-versions chart; and the two permanent warnings about iterated search and hindsight.

The Δ rules get unit tests before the table renders. Every one of them is a rule about refusing
to show a number, and a comparison table that quietly compares the wrong two runs is precisely
the "authoritative and wrong" output the project is organised against.

**Check before building the timeline:** the two diffs canonicalise differently. `POST
/config/diff` compares both sides through the YAML writer (spec §15.1, amended 2026-08-17);
`GET /strategies/{id}/diff` compares the *stored* configs. Where both sides were written by
`save_version` that is consistent, but an imported config is stored as the user wrote it, so a
diff between an import and a later save may report defaults the user merely omitted —
`execution.risk_free_rate: null → 0.04` — as though they were edits. Verify against a real
imported strategy before the timeline renders change summaries; if it reproduces, the fix is to
canonicalise the version diff the same way, and it is a spec amendment, not a UI workaround.

---

## 5. Definition of done

`./scripts/check.sh` green; no `ComingSoon` route remaining; every screen in the brief's
Deliverable paragraph built with its loading, empty, failed and suppressed states; every decision
above recorded in `ENGINE_SPEC.md`; this file deleted.
