# UI design prompt — cracktrade

Paste everything between the rules below into a UI-generating tool (Claude, v0, Lovable, …). It is
self-contained: it does not assume the tool can see this repository.

---

## Brief

Design and build the web UI for **cracktrade**, a trading-strategy optimization engine.

A user writes a trading strategy as a structured config, searches its parameters, and then decides
where to put real money based on what the screen shows them. That last clause is the entire design
constraint:

> **A number that looks authoritative and is not is the worst possible output of this product.**

Every layout decision follows from that. A big green "+149%" with the caveats folded into an
accordion is a design failure here, even though it is the conventional choice. The caveats *are*
the product. The engine already computes them; the UI's job is to make them impossible to miss and
impossible to misread.

Build it as a React + TypeScript single-page app. Use whatever component and chart libraries you
prefer, as long as everything is keyboard-navigable and readable at 1280px and above. Work against
mocked data shaped exactly like the payloads described below; do not invent extra fields, and do not
silently drop fields you find inconvenient to render.

---

## 1. Domain model

Five persisted entity types. Relations matter — the whole navigation model is built on them.

### `Strategy`

The editable, user-owned object.

```ts
type Strategy = {
  id: string
  name: string                  // from config.strategy.name
  ticker: string                // from config.universe.ticker
  config: StrategyConfig        // the full YAML-equivalent object, see §3
  currentVersion: number        // points at the head of the version history
  parentId: string | null       // set when created by Fork or by Promote
  origin: 'authored' | 'forked' | 'promoted'
  createdAt: string
  updatedAt: string
  lastRun: RunSummary | null    // most recent run of any kind, for the list view
}
```

### `StrategyVersion`

Every save of a config creates one. Versions are immutable and never deleted — this is an audit
trail of what the user believed at each point, and deleting the embarrassing ones defeats it.

```ts
type StrategyVersion = {
  id: string
  strategyId: string
  version: number               // 1, 2, 3… monotonic per strategy
  config: StrategyConfig        // the complete config as of this save, not a delta
  createdAt: string
  note: string | null           // optional user-supplied "why I changed this"
  restoredFrom: number | null   // set when this version was created by restoring an older one
}
```

Store the full config per version, not a patch. Configs are small, and reconstructing one by
replaying deltas is a class of bug this app cannot afford — a restored strategy that differs from
what the user saw in the diff is worse than no history at all.

### `OptimizationRun`

Produced by the **Optimize** action. Always belongs to exactly one parent `Strategy`. Holds a
candidate config that the search found — it is *not* itself a strategy until the user promotes it.

### `BacktestRun`

Produced by the **Backtest** action. Simulates the config exactly as written, no search.

### `ValidationRun`

Produced by the **Walk-forward** action. The only run type that issues a credibility verdict.

All three run types are immutable once complete, are listed under their parent strategy, and carry
`status: 'queued' | 'running' | 'succeeded' | 'failed'`, `startedAt`, `finishedAt`, `elapsedSeconds`,
and the parameters the run was launched with.

**Every run also carries `strategyVersion: number`** — the version of the config it actually ran
against. This is not bookkeeping. A user edits a config and re-runs; without the version stamp, the
run list shows two numbers that appear comparable and are not, because they came from different
strategies wearing the same name. Show the version on every run row, and mark runs made against a
version other than the current one as *stale* — they describe a config that no longer exists.

---

## 2. Screens

### 2.1 Strategy list — the landing page

A table (not a card grid — these are compared column-against-column, not browsed):

| column | notes |
| --- | --- |
| Name | with a small lineage marker when `parentId` is set: forked-from / promoted-from, linking to the parent |
| Ticker | |
| Date range | `start_date → end_date` from the config |
| Last run | type, relative time, and its headline verdict chip |
| Runs | counts per type, e.g. `3 opt · 1 bt · 1 wf` |

The verdict chip is the load-bearing element in this table. Four states, and they must be visually
distinguishable at a glance without relying on colour alone (use an icon or glyph too):

- **Credible** — a walk-forward run passed every robustness check.
- **Not credible** — a walk-forward run ran and failed at least one check. Show the failure count.
- **Unvalidated** — backtests or optimizations exist, but no walk-forward has been run. This is the
  default state and it must not read as neutral-positive; it means *nobody has checked this yet*.
- **Never run**.

Provide search by name/ticker, filter by verdict state, and sort by any column. Primary action:
**New strategy**. Secondary: import a config file.

### 2.2 Strategy detail

Header: name, ticker, date range, lineage breadcrumb (`momentum_breakout → forked → momentum_v2`),
and the action bar (§4).

Below it, tabs:

**Config** — the editor (§3).

**Optimizations** — a table of `OptimizationRun`s for this strategy, newest first. Columns:
objective, epochs, out-of-sample return, improvement over the unoptimized baseline, the
overfitting gap, trial count, elapsed time, status. Each row expands into the full run view (§2.3)
and carries a **Promote to strategy** action.

**Backtests** — a table of `BacktestRun`s: return, benchmark return, excess, trade count, max
drawdown, status.

**Validation** — a table of `ValidationRun`s: folds, scheme, combined out-of-sample return, fold
win rate (`4/6`), and the credibility verdict.

**Charts** — every visual judgement of the strategy, for one selected run (§5). This is the tab a
user lives in while forming an opinion, and it is the only place in the app where the full chart set
appears.

**History** — the version history and the comparison table (§2.6).

An empty tab gets a real empty state that explains what the action does and why you would run it,
with the button to run it — not a shrug.

### 2.3 Optimization run view

The search fits parameters on the first 80% of history and reports on the final 20%, which it never
saw. The layout must make the train/test distinction structural, not a footnote.

**Top band — out-of-sample only.** Return, CAGR, max drawdown, Sharpe, trade count, all from
`test_metrics`. Label the band explicitly: *"Out of sample — the final 20% of history, evaluated
once, after every choice was final."*

**Beside it — three comparisons, always shown together:**

1. `test_metrics` vs `baseline_test_metrics` — what the search actually bought you, measured on the
   same window for both sides. This is `improvement_pct`.
2. `train_metrics` vs `test_metrics` — the `overfitting_gap_pct` (train CAGR minus test CAGR). A
   large positive gap is the signature of a curve fit. Render it as a two-bar comparison, and when
   the gap exceeds the test CAGR itself, say so in words.
3. Trade count against the floor of 20. **Below 20 closed trades, suppress the precise figures
   entirely** and replace them with "too few trades to draw a conclusion from (N of 20 needed)".
   Do not print a CAGR to two decimal places off six trades. This rule applies on every screen.

**Parameter changes table.** One row per parameter the search moved:
`path` (e.g. `indicators.rsi_ind.window`), old value, new value, and the search range `[low, high]`.
Render the range as a track with the old and new values marked on it — the shape of the move is the
information, not the digits. Flag every parameter whose optimum sits *on* a bound with a warning:
the true optimum probably lies outside the range, so the range was the binding constraint rather
than the data, and it is worth widening and re-running.

**Search diagnostics**, collapsed by default: evaluations, failures, infeasible candidates, trials,
budget, seed, convergence message, most common failure reason.

**The resulting config**, as YAML, with a diff against the parent strategy's config, and the
**Promote to strategy** button.

A banner at the top of this screen, always present, never dismissible:

> A single train/test split is one draw. Run walk-forward validation before acting on this.

### 2.4 Backtest run view

Simpler, and honest about being simpler. It shows what the config as written would have done.

- **Lead with the benchmark comparison**, not the return. A strategy returning 13% where the ticker
  itself returned 40% is a failure, and the screen must read that way — put strategy and
  buy-and-hold side by side at the same visual weight, with the excess return as the headline.
- Metrics table: return, CAGR, drawdown, Sharpe, Sortino, Calmar, win rate, profit factor,
  exposure %, average holding days, trade count — the strategy in one column, buy-and-hold beside
  it. A strategy declares one entry rule and one exit rule, so a run is one simulation and one set
  of numbers; there is no best-of-N to pick and no inflation to warn about.
- Charts (§5).
- Trade list: entry date, exit date, entry price, exit price, size, PnL, return %, fees, holding
  days, open/closed. Sortable, filterable to winners/losers.
- **Data vintage** panel: ticker, first and last bar, bar count, forward-filled bar count and
  percentage, fetch date, and a short price-frame digest. This exists because the price source
  retroactively adjusts history for splits and dividends, so the same backtest run months apart uses
  different prices. Two runs disagreeing is explainable if you can compare digests, and merely
  alarming if you cannot. Show the digest as copyable monospace text.
- **Definedness**: the fraction of bars on which the entry and exit conditions could
  actually be evaluated. Several indicators are undefined on most of their bars by design. A signal
  defined on 8% of bars is a fact the user needs before they read the returns.
- **Shadowed stops** warning: stops follow a priority chain (ATR > trailing > fixed), and a stop
  that is configured but loses the chain has no effect. Name any that were shadowed.

### 2.5 Validation run view — the one that issues a verdict

This is the screen a user should be looking at when they decide to commit money, and it should feel
like the most serious screen in the app.

**The verdict, at the top, at full width.** Either `CREDIBLE` or `NOT CREDIBLE`. It is a boolean,
deliberately not a score — a number that is 80% trustworthy is not something to put money behind,
and averaging the checks would let a strong headline return paper over a failed overfitting test.

When not credible, list every failed check immediately underneath, in plain language, as the engine
phrases them:

```
NOT CREDIBLE
  out-of-sample +6.6% vs +149.0% buy-and-hold, profitable in 2/4 folds
  ✗ deflated Sharpe P=0.00, below the 0.95 bar for 4800 trials
  ✗ a 10% parameter nudge destroys 81% of the objective
  ✗ only 16 out-of-sample trades in total
```

Then a card per robustness check. Each card needs a one-line plain-English explanation of what the
check means — the user is a trader, not a statistician, and a bare "PBO 0.62" teaches nothing:

- **Fold results** — one row per fold: index, test window dates, out-of-sample return, train
  return, trade count, and the parameters that fold chose. Plus the aggregate: fold
  win rate (`4/6`), median return, interquartile spread, combined compounded return. *A strategy
  profitable in 6 of 6 folds and one carried entirely by fold 3 are different objects, and an
  average cannot tell them apart* — so show the per-fold bars, not just the mean.
- **Benchmark** — combined out-of-sample return against buy-and-hold over the same window.
- **Deflated Sharpe** — observed Sharpe, the threshold that luck alone would produce given the trial
  count, the probability, the trial count, the observation count, and whether the cross-trial
  variance had to be estimated rather than observed. Bar is 0.95.
- **Probability of backtest overfitting (PBO)** — the probability, the number of train/test
  combinations, the median logit. Above 0.5 the selection procedure is worse than choosing at
  random, and the result must not be presented as a recommendation. Render 0.5 as a hard line on
  the scale.
- **Parameter stability** — the optimum perturbed ±10% and ±20%. Plot degradation per parameter per
  multiplier; a plateau survives, a spike is a curve fit, and in a point estimate the two look
  identical. Name the fragile parameters explicitly. The threshold is: losing more than 50% of the
  objective to a single 10% nudge marks the result unstable.
- **Cost sensitivity** — the headline recomputed at 1×, 2×, 3× slippage and commission. Show the
  break-even multiple if one was reached. An edge that dies when costs double is not tradeable.
- **Confidence intervals** — bootstrap intervals for mean and total fold return. Mark whether each
  interval excludes zero.

Then the winning config, YAML, with **Promote to strategy**.

### 2.6 Version history and comparison

Two things live on this tab: a timeline of every saved version, and a table that compares them.

#### The timeline

Newest first, one row per `StrategyVersion`: version number, timestamp, the user's note, a one-line
summary of what changed (`rsi_ind.window 14 → 21, atr_swing.take_profit_pct 18 → 12`), and how many
runs were made against it. The current version is marked as head. A version created by a restore
says so and links to the version it was restored from.

Per-row actions: **View** (read-only config), **Diff** (against any other version, defaulting to the
one before it), and **Restore**.

The diff view is a side-by-side of the two YAML configs with changed lines highlighted, plus a
structured summary above it grouping changes by section — indicators added/removed/retuned, entry
and exit rules changed, execution costs changed, universe changed. Group them, because "the date
range moved" and "an RSI window moved by one" are not the same magnitude of change and a flat line
diff presents them identically.

Flag universe and execution changes prominently in the diff. A change to the ticker, the date range,
or the cost model means every earlier run was measured on a different question, and the comparison
table below is no longer comparing strategies — it is comparing experiments.

#### Restore

Restoring does **not** rewind or delete anything. It creates a *new* version at the head whose
config is a copy of the older one, with `restoredFrom` set. History is append-only, so a user who
restores and then regrets it can restore forward again.

Show the diff between the current version and the target before confirming, with the count of runs
that will become stale as a result. If the config is dirty in the editor, say so and require the
user to save or discard first — never silently drop unsaved edits.

#### The comparison table

One row per version, oldest at the top so the strategy reads as a progression. Columns:

| column | source |
| --- | --- |
| Version | number, note, timestamp, head marker |
| Changed | the one-line change summary |
| Runs | count, by type |
| Return | out-of-sample return from the best run against this version |
| vs buy-and-hold | excess return over the benchmark — the number that actually matters |
| Max drawdown | |
| Sharpe | |
| Trades | out-of-sample trade count |
| Fold win rate | from a walk-forward run, if one exists — `4/6` |
| Verdict | credible / not credible / unvalidated |
| Δ | movement against the *previous version that has a comparable run* |

The Δ column is what the user is really asking for — did this edit help — so it carries the most
risk of lying. Rules:

- **Compare like with like or not at all.** A Δ may only be computed between two runs of the same
  type, with the same objective, and the same fold count and scheme where applicable. If the nearest
  earlier comparable run does not exist, the cell reads `—` and says why on hover. Do not fall back
  to comparing a backtest against a walk-forward.
- **Let the user pick which run type the table reports.** A segmented control — backtest /
  optimization / walk-forward — switches the whole table's source. Default to walk-forward when any
  exists, since it is the only run type with a verdict, and fall back to backtest.
- **A version with no run of the selected type shows no numbers.** Empty cells, not zeros, not
  inherited values from a neighbouring version.
- **Below the 20-trade floor, suppress the figures** here exactly as everywhere else, and show the
  trade count alone.
- **Flag incomparable data vintages.** Runs made weeks apart use retroactively adjusted prices, so
  two rows can differ because the data moved rather than the config. When two runs' price-frame
  digests differ, mark the Δ with a caveat rather than presenting it as a clean improvement.
- Δ is shown for return, excess-over-benchmark, drawdown, Sharpe and fold win rate. Direction is
  signed and explicit (`+3.1pp`), never a bare arrow, and remember that a *smaller* drawdown is
  better while a larger return is — do not colour by sign alone.

Above the table, plot the selected metric across versions as a small line or step chart, with each
point labelled by version. This is the "am I making progress or wandering" view.

Two honest warnings belong on this screen, permanently:

> Editing and re-running a strategy until the numbers improve is itself a search, and it is not
> counted in the trial count that deflates the Sharpe ratio. A strategy on its twelfth version has
> been optimized far harder than its last run reports.

> Comparing versions on the same ticker and the same date range means the later versions were chosen
> with knowledge of how the earlier ones performed on that data. Walk-forward validation is the only
> thing on this screen that pushes back on that.

The second one is the reason this table needs the fold win rate and verdict columns at all: without
them it is a leaderboard for overfitting, and it would be a well-designed one.

---

## 3. The config editor

This is where most of the interaction time goes, so it deserves the most design effort.

Offer two synchronised modes with a toggle: a **form** (default) and a **raw YAML** editor. Edits in
either are reflected in the other. The YAML mode needs syntax highlighting and inline error markers.

Validation runs on every change, client-side, with a server round-trip on save. Save is disabled
while invalid, and the reason is stated next to the disabled button — never a dead button with no
explanation. Errors attach to the field that caused them, and every message says what is wrong and
what to do instead.

**Every successful save creates a new `StrategyVersion`** (§2.6). The save control shows what the
new version number will be, offers an optional one-line note, and previews the diff against the
current version. A save that changes nothing creates no version. Unsaved edits must survive
navigation within the app and warn on page unload; the editor shows a dirty marker and the version
it is based on, so a user who has been editing for ten minutes knows they are still on v4.

### Schema

```yaml
strategy:
  name: rsi_pullback              # required, non-blank

universe:
  ticker: NVDA                    # required, exactly one symbol — no portfolios
  start_date: '2023-01-01'        # required, YYYY-MM-DD
  end_date: '2025-12-31'          # defaults to today; must be after start_date

execution:
  initial_capital: 10000.0        # required, > 0
  commission_pct: 0.05            # required, >= 0 — percent, not a fraction
  slippage_pct: 0.1               # required, >= 0 — percent, not a fraction
  risk_free_rate: 0.04            # optional, annual rate as a fraction: 0.04 is 4%

indicators:                       # optional list
  - name: sma_long                # must be a valid identifier; must not shadow
    type: sma                     #   open/high/low/close/volume; must not start with "_";
    window: 200                   #   must be unique within the list
    source: close                 # optional: open|high|low|close|volume, default close
    optimize: true                # optional, see below
  - name: rsi_ind
    type: rsi
    window: 14

entry:                            # required, exactly one
  signal: (close > sma_long) & (rsi_ind < 35)
  optimize: true

exit:                             # required, exactly one
  signal: null                    # optional expression
  stop_loss_pct: 5                # optional, > 0
  trailing_stop_pct: 8            # optional, > 0
  atr_stop_multiplier: 2.5        # optional, > 0
  take_profit_pct: 18             # optional, > 0
  min_holding_days: 3             # optional, >= 1
  max_holding_days: 15            # optional, >= 1
  optimize: true

position_sizing:                  # optional; omitted means 100% of available cash per trade
  type: fixed_pct                 # fixed_pct | fixed_cash | fixed_shares
  value: 25                       # > 0; for fixed_pct, <= 100, and it is a percentage of
                                  #   available cash, not of total equity
```

Note the units carefully in the UI: `commission_pct: 0.05` means 0.05%, while `risk_free_rate: 0.04`
means 4%. Two different conventions in adjacent fields is a genuine footgun — put the unit in the
field suffix, and show the resolved value ("0.05% = $5 on a $10,000 trade").

### Validation rules the form must enforce

Structural:

- `start_date` strictly before `end_date`.
- Indicator names are unique. `entry` and `exit` are single mappings and carry no name.
- An indicator name must be a valid identifier, must not be `open`/`high`/`low`/`close`/`volume`,
  and must not begin with an underscore — it becomes a variable in signal expressions.
- Unknown keys are rejected outright, so surface a typo'd field as an error rather than ignoring it.
- Numbers are not coerced from strings: `"200"` is not `200`.

The exit rule, which has the subtlest rules:

- `exit` must define *at least one* way to exit: `signal`, `stop_loss_pct`, `trailing_stop_pct`,
  `atr_stop_multiplier`, `take_profit_pct`, or `max_holding_days`. Without one, the strategy holds
  its first position forever. Message: *"defines no way to exit a position: set at least
  one of …"*.
- `min_holding_days` must be less than `max_holding_days`.
- **Stop priority:** `atr_stop_multiplier` > `trailing_stop_pct` > `stop_loss_pct`. Only the
  highest-priority one set is active. If more than one is set, show a non-blocking warning on the
  shadowed ones — they are configured and do nothing. `take_profit_pct` is orthogonal and always
  applies.
- Worth surfacing as an editor note: stops always fire, even inside `min_holding_days`, which
  suppresses only signal exits and time exits. A stop disabled for N days is not a stop.

Signal expressions, which need their own small editor with autocomplete over the available names:

- The namespace is `open`, `high`, `low`, `close`, `volume`, plus every indicator name in the
  config. Multi-output indicators contribute suffixed names (e.g. a MACD named `m` contributes
  `m_macd`, `m_signal`, `m_hist`) — the UI should list the exact names each configured indicator
  contributes.
- The grammar permits names, numeric literals, comparisons, boolean operators (`&`, `|`, `~`),
  arithmetic, and parentheses. **Function calls, attribute access and subscripting are rejected**,
  so `close.shift(-1)`, `close.iloc[t+1]` and `rolling(center=True)` are not strategies that fail
  validation — they are strings that are not strategies. This is the engine's structural guarantee
  against look-ahead bias and the editor should present it as a feature, not just an error.
- Comparisons must be parenthesised, because `&` binds tighter than `<`. `close > sma & rsi < 30`
  is a parse error; `(close > sma) & (rsi < 30)` is correct. This is the single most common mistake
  — the error message should show the corrected form, and the editor should offer to apply it.
- An unknown name is an error naming the symbol and listing what is available.

The `optimize` field, on any indicator and on `entry` and `exit`:

- `true` (default) — every numeric field in this entry is searchable.
- `false` — pin the whole entry.
- A mapping naming individual parameters, each either `false` (pinned) or `{min, max}` explicit
  bounds. Default bounds are ±50% of the configured value.

Design this as a per-field control in the form: each numeric field gets a small toggle for
pinned/searched, and searched fields can be given explicit bounds. Show the effective search range
inline (`14 → search 7–21`), because that range is what the optimizer is actually allowed to do and
it is currently invisible until a run finishes.

One thing to state plainly in the editor, because users hit it constantly: **numbers written inside
a signal expression are not optimizable.** The `35` in `rsi_ind < 35` is unreachable by the search.
To tune it, move it into an indicator. Detect bare numeric literals in comparisons and offer this
hint inline.

### Error rendering

Validation errors arrive as a list of messages. Render them as:

```
Please fix the following issues in your strategy file:
  - exit: min_holding_days (15) must be less than max_holding_days (10)
  - entry.signal: 'rsi_indd' is not defined; available names are …
```

Attach each to its field, and also collect them in a summary panel that scrolls to the field on
click. Distinguish errors (block save) from warnings (shadowed stops, an entry with no optimizable
parameters, a very short date range) — warnings never block.

---

## 4. Actions

Present these in the strategy detail action bar, in this order:

| action | what it does | duration |
| --- | --- | --- |
| **Validate** | Checks the config without running anything. Fast, no market data. | instant |
| **Backtest** | Runs the config as written, against a buy-and-hold benchmark. | seconds |
| **Optimize** | Searches the parameters. Creates an `OptimizationRun` under this strategy. | minutes |
| **Walk-forward** | Optimizes across successive folds and judges credibility. Creates a `ValidationRun`. | many minutes |
| **Fork** | Duplicates this strategy as a new, independently editable one. | instant |

Plus **Promote to strategy**, which lives on an individual optimization or validation run, not on
the strategy.

### Launch dialogs

**Optimize** takes: `epochs` (differential-evolution generations, default 10, minimum 1) and
`objective` (`calmar` — the default — `sortino`, `sharpe`, or `legacy_pnl`). Explain each objective
in one line in the picker; `legacy_pnl` should be visibly marked as retained for comparison and not
recommended. Warn before launching when the strategy has no optimizable parameters — every numeric
field pinned means the search would burn time re-scoring one configuration, and the engine refuses.

**Walk-forward** takes: `folds` (default 6), `scheme` (`anchored` or `rolling` training window),
`epochs`, and `objective`. Show an estimated duration, since this is `folds × epochs` searches and
the wait is real.

Both should also expose whether to cache downloaded price history.

### Long-running run UX

Optimization and walk-forward take minutes. The UI must:

- Return to the strategy immediately with the run in `queued`/`running` state — never a blocking
  modal.
- Show progress by generation for an optimize run, and by fold for a walk-forward run.
- Let the user navigate away and come back, and run several strategies concurrently.
- Surface failures with the engine's own error text and category, not "something went wrong". The
  categories are: config invalid, market data unavailable or unusable, engine/simulation failure,
  and a causality violation (which is an engine bug, and should be presented as one — it means the
  engine refused an operation that would have let information from the future reach an earlier bar).

### Fork

Opens a dialog pre-filled with `{original name} copy`, creates a new strategy with the same config,
`parentId` set, `origin: 'forked'`, and no runs. Navigate straight into its config editor. The
lineage must be visible on both the parent and the child.

A fork starts a fresh version history at version 1 — it does not inherit the parent's versions. But
it records *which* version of the parent it was forked from, and the child's history tab shows that
as its origin point with a link. Offer forking from any historical version, not only the head: "take
v3 in a different direction" is a natural thing to want after a restore-or-branch decision, and
without it the user will restore just to fork and pollute the parent's history doing it.

### Promote to strategy

Takes the config an optimization or validation run produced and creates a new `Strategy` from it,
with `parentId` set to the strategy the run belonged to and `origin: 'promoted'`. The dialog shows
the **diff against the parent config** before confirming — the user is about to adopt a set of
numbers a machine chose, and they should see exactly which ones moved.

Carry the provenance forward: the new strategy's detail page states which run it came from, with a
link, and repeats that run's verdict. A strategy promoted from a run that was *not* credible must
say so on its own detail page. This is the single most important anti-footgun in the app — promotion
is exactly the moment where a caveat gets lost.

---

## 5. The Charts tab

The user this app is for reads charts better than they read statistics. That is not a weakness to
design around, it is the fastest honest channel available — a single equity curve with the benchmark
drawn on it settles a question that a table of eleven ratios leaves open. So the charts get their
own tab, not a strip at the bottom of a run view.

The same rule as everywhere else applies with more force here, because a chart is more persuasive
than a number: **a picture that implies precision the sample does not support is the worst possible
output.** Sixteen trades drawn as a smooth curve is a lie told in a friendlier medium.

### 5.1 Frame

At the top of the tab, a **run selector** — not a chart. Charts describe one run; a tab that quietly
draws the newest one is how a user ends up judging a config they have since edited. The selector is
a compact row showing: run type, `strategyVersion`, launch date, status, and the headline result.
Default to the most recent succeeded run of the most serious type available — validation, then
optimization, then backtest — and say in words which one is being shown. Runs made against a version
other than the current one are marked **stale** here exactly as they are in the run tables.

A strategy declares one entry rule and one exit rule, so a backtest or optimization run is one
simulation and needs no second selector — the run selector alone determines what is drawn.

**Validation runs additionally get a fold selector**, because walk-forward re-optimizes
independently per fold: every fold has its own parameter vector, and a fold's trades belong to that
vector alone. Default it to **"Combined (all folds)"**, or pick a fold index.

The fold selector changes what Groups A and B can show, because they are trade- and price-level
charts:

- With a **specific fold** selected, Groups A and B render normally, scoped to that fold's test
  window and trades.
- With **Combined** selected, Groups A and B show an explanatory empty state instead of data —
  *"select a fold to see its trades and price chart — combining trades from folds that used
  different parameters would chart results that are not one configuration"* — with a link into the fold
  table. Group C stitches fold test windows chronologically (they are contiguous and
  non-overlapping by construction) and stays available in Combined view; Group D is validation-native
  and unaffected by fold selection.

Below that, the charts, in four labelled groups in this order, each group introduced by the question
it answers. A sticky in-page nav rail listing the groups; no accordions, no tabs within the tab.
Everything is on the page and reachable by scrolling, because the ones a user would collapse are the
ones they most need to see.

The run views (§2.3–2.5) keep their inline equity, drawdown and price charts — those are load-bearing
there. Each run view gets a **See all charts** link that opens this tab with that run already
selected.

### 5.2 Suppression rules

These override every chart description below.

- **Below the 20-trade floor**, render no per-trade or per-period chart at all. Show the same
  "too few trades to draw a conclusion from (N of 20 needed)" state used elsewhere, occupying the
  chart's full footprint so the layout does not silently shrink. The price chart with trade markers
  is the sole exception: it shows individual events rather than an estimate, and looking at six
  trades one by one is a reasonable thing to do.
- **When a required series is absent**, show a labelled empty frame naming what is missing. Never
  substitute a chart that happens to have data.
- **Where a window is shorter than the chart's period** — rolling 12-month return over eight months
  of history — draw nothing rather than a partial window.
- **Open trades** (`is_open`) are drawn but never counted in any distribution or aggregate, and are
  visually distinct from closed ones. An unrealized gain is not a result.

### 5.3 Group A — what the strategy actually did

**1. Equity curve, strategy against buy-and-hold.** One chart, two series, same axes, same starting
capital, starting at the first bar the strategy could have acted on (`warmupUntil` — the indicator
warm-up is not free performance). A strategy that returned 80% while the ticker returned 200% is a
failure, and on separate axes it looks like a success. Offer a log-scale toggle, default linear,
labelled — on a decade of history a linear axis makes early years unreadable.

**2. Drawdown (underwater).** Directly beneath, sharing the x-axis: for every bar, how far below the
previous all-time high the account sits. Always ≤ 0, filled. `max_drawdown_pct` is one number; this
shows *how long* the user would have spent underwater, which is what actually makes people abandon a
strategy. Mark the longest recovery period with its duration in days — the widest valley matters
more than the deepest.

**3. Price with trade markers.** Close or OHLC, with an entry marker at each `entry_date`/
`entry_price`, an exit marker at `exit_date`/`exit_price`, and the span between them shaded by
outcome. Open trades get a span with no right edge. Shade in-position regions across the whole
chart so `exposure_pct` becomes something you can see rather than a percentage you have to trust.
Hovering a trade shows return %, PnL, fees and holding days; clicking one selects the matching row
in the run view's trade list, and selecting a row there scrolls this chart to that trade.

Charts 1–3 share an x-axis, a synchronized crosshair, and one zoom/brush control that drives all
three. They are a single instrument.

### 5.4 Group B — where the money came from

The question this group answers: *is the profit a system, or is it one lucky trade?*

**4. Trade PnL distribution.** A histogram of closed-trade PnL, losses left of zero, wins right.
Win rate and win/loss size asymmetry in one picture, which is the point — win rate alone is
meaningless, and a 90%-win-rate strategy that loses money is easy to build. Annotate the chart with
**the total return recomputed with the single best trade removed.** If that number is negative, say
so in words directly on the chart: *"without its best trade this strategy loses money."* For samples
near the 20-trade floor, a dot strip (one dot per trade, jittered) is an acceptable and more honest
encoding than bins.

**5. Cumulative PnL by trade.** Trades in chronological order along the x-axis, one bar each up or
down, cumulative line over the top. Profit concentration becomes impossible to miss: a staircase is
a system, a cliff is one bet. Note that the x-axis is trade sequence, not time — label it, because
the resemblance to an equity curve is otherwise misleading.

**6. Won against lost.** Two stacked bars: gross profit from winning trades, gross loss from losing
trades. Their ratio *is* `profit_factor`, made physical. Below 1.0 the loss bar is taller and the
user can see it without knowing what a profit factor is. Print the count and average size inside
each bar.

### 5.5 Group C — did it hold up over time

**7. Monthly return heatmap.** Years down, months across, cells coloured on a diverging scale with
white at exactly 0%. Months in which the strategy held no position are rendered as an explicit
"no trades" state, not as 0% — flat and absent are different, and a heatmap that conflates them
overstates consistency. What the user is looking for is broad pale green, not two dark cells
carrying a decade.

**8. Yearly returns, strategy and benchmark.** A bar pair per calendar year. *"All the profit came
from one year"* is the most common way a backtest misleads and no aggregate figure reveals it. This
chart stays even though chart 7 contains the same information more finely — the monthly grid shows
pattern, the yearly bars show magnitude against the benchmark, and users read them differently.

**9. Rolling 12-month return.** For each date, the return of the preceding twelve months. Zero line
drawn; the minimum marked and labelled, since that value is `worst_rolling_12m_pct` and answers
"how badly could my entry timing have gone?". A curve drifting toward zero over the years is an
edge that decayed, and it is invisible in every aggregate on every other screen.

### 5.6 Group D — would it survive contact with reality

Validation runs only. When the selected run is a backtest or optimization, this group is not hidden
— it is shown as a panel explaining that these checks require a walk-forward run, with the button to
launch one. An unvalidated strategy must never present as a validated one with a shorter page.

**10. Fold returns.** One bar per `FoldResult`: out-of-sample return solid, that fold's *train*
return as a hollow bar behind it. Zero line, median marked. Two failures show up at once — how many
folds were green (`fold_win_rate`), and how much taller training was than testing. Solid green train
bars over ragged red test bars is a curve fit, drawn.

**11. Parameter drift across folds.** Each fold re-optimizes independently, so `FoldResult.parameters`
gives a value per parameter per fold. One normalized line per parameter across folds. A real optimum
is picked roughly consistently; a lookback that goes 5 → 60 → 12 is not an optimum, it is noise, and
this is the only chart in the app that shows it. With entry/exit variants removed, this is also the
*only* remaining measure of structural stability across folds — nothing else reports whether the
folds agreed.

**12. Parameter stability plateau.** From `StabilityReport.points`: x is the perturbation multiplier
(−20%, −10%, +10%, +20%), y is the objective, one line per parameter, baseline marked. Flat lines
survive; a spike at the centre is a needle that will not exist next year. Mark the 50% degradation
threshold, and label fragile parameters on the chart itself rather than only in the legend. In a
point estimate a plateau and a spike look identical — that is why this chart exists.

**13. Cost ladder.** Return against the cost multiple from `CostSensitivity.scenarios`, zero line
drawn, the `break_even_multiple` crossing marked and labelled. An edge that dies when costs double
belongs to the broker, and the crossing point says exactly how much room there is.

**14. Deflated Sharpe and PBO.** Not really charts — two one-dimensional scales, which is the honest
form for these numbers. For the deflated Sharpe: the `observed` value plotted against `threshold`
(the Sharpe a lucky search of that many trials produces from no edge at all), with the 0.95
probability bar. Seeing the observed value sit to the *left* of the luck threshold communicates more
than any p-value text. For PBO: the probability on a 0–1 scale with 0.5 as a hard line, above which
selection is worse than random.

**15. Confidence intervals.** `mean_return_interval` and `total_return_interval` as horizontal bars
with the point estimate marked and zero drawn in. When the bar straddles zero, label it: the
headline return is not distinguishable from luck. This is the cheapest chart on the page and one of
the most decisive.

### 5.7 Rules for every chart here

- Every percentage axis includes zero. No truncated baselines, ever.
- The benchmark series is present wherever one exists. A strategy result without its benchmark is
  not a result.
- Colour is never the only encoding — pair it with shape, fill, position or a direct label, in every
  chart that uses green/red for sign.
- No dual y-axes. Two units on one frame invent correlations.
- Forward-filled bars (`DataVintage.filled_bars`) are marked on the time-domain charts rather than
  interpolated across silently.
- Hover gives exact figures; nothing important is available *only* on hover.
- Every chart carries a one-line plain-language caption saying what it answers and what a bad
  picture looks like. The user is a trader, not a statistician.
- Every chart is downloadable as PNG and its underlying series as CSV.
- **No pie charts, gauges, letter grades, or 0–100 scores.** A win-rate pie in particular implies
  win rate is the answer, and it is not.
- **No composite "strategy health" visual of any kind.** Credibility is a boolean with reasons
  (§2.5), and a chart that averages the robustness checks would let a strong return paper over a
  failed overfitting test — exactly the failure this whole product exists to prevent.

### 5.8 Chart payload

Everything in Groups A–C comes from one object per run (per fold, for validation runs). Group D
reads the validation report objects already described in §2.5.

```ts
type Point = { date: string; value: number }

type RunCharts = {
  runId: string
  runType: 'backtest' | 'optimization' | 'validation'
  strategyVersion: number
  foldIndex: number | null             // validation runs only; null for Combined view
  warmupUntil: string                  // first bar the strategy could have acted on
  isStale: boolean                     // strategyVersion !== strategy.currentVersion

  equity: Point[]                      // account value, one per bar, from warmupUntil
  benchmarkEquity: Point[]             // buy-and-hold, same bars, same starting capital
  drawdown: Point[]                    // percent below running peak, always <= 0
  rolling12m: Point[] | null           // null when history is under 12 months

  monthlyReturns: {
    year: number
    month: number                      // 1-12
    returnPct: number
    hadPosition: boolean               // false renders as "no trades", never as 0%
  }[]
  yearlyReturns: { year: number; strategyPct: number; benchmarkPct: number }[]

  price: { date: string; open: number; high: number; low: number; close: number
           filled: boolean }[]
  trades: Trade[]                      // as in the trade list, §2.4
  inPosition: { from: string; to: string | null }[]   // to === null while open

  totalReturnPct: number
  totalReturnExcludingBestTradePct: number   // annotation for chart 4
  closedTrades: number                       // drives the 20-trade suppression rule
}
```

Mock this with at least two contrasting runs so the charts are exercised rather than flattered: one
strategy whose profit is genuinely spread across folds and years, and one whose entire result is a
single 2020 trade — the second is the case every rule in §5.2 and every annotation in §5.4 exists
for, and a chart set that only ever renders the first one has not been designed, only decorated.

---

## 6. Tone and copy

The engine's own voice is plain, specific, and willing to say a result is worthless. Match it.

- "Profitable in 2 of 4 folds", not "moderate consistency".
- "A 10% parameter nudge destroys 81% of the objective", not "stability: low".
- "Only 16 out-of-sample trades in total", not a CAGR to two decimals.

Never use a gauge, a letter grade, or a 0–100 score anywhere. Credibility is a boolean with reasons.

Surface these limits somewhere permanent, ideally as a short "what this cannot tell you" panel on
the validation screen:

- **Ticker selection is hindsight.** You chose the symbol knowing its history. No engine corrects
  for that.
- **Prices are retroactively adjusted**, so the same backtest run months apart uses different data.
- **Numbers inside signal expressions are not optimizable.**
- **One ticker per strategy.** No portfolios, no cross-sectional strategies.
- **Long only.**

---

## 7. Out of scope

Do not design: live trading, broker connections, order execution, real-time quotes, multi-ticker
portfolios, short positions, intraday bars, social/sharing features, or an AI strategy generator.

---

## Deliverable

A working React + TypeScript app against mocked data, covering: strategy list, strategy detail with
all six tabs, the config editor in both form and YAML modes with full validation, all three run
views, the full Charts tab (§5), the version timeline with diff and restore, the version comparison
table, and the Fork and Promote dialogs. Include the loading, empty, and failed states for each —
this is an app where a run takes five minutes and can legitimately fail, so those states are not
afterthoughts. For the Charts tab, the suppressed states in §5.2 count as states to design, not as
error handling to add later.

Mock at least one strategy with a rich history — six or seven versions, a restore among them, runs
of all three types spread unevenly across versions, and at least one version with no runs at all —
so the comparison table's incomparability rules are actually exercised rather than designed around
a tidy case that will never occur.

---

## Appendix — engine-side gaps this UI implies

Not part of the prompt above; notes for whoever wires the UI to the engine.

1. **Persistence does not exist yet.** The engine writes results to stdout and stores nothing —
   no run catalogue, no database. The entity model in §1 (strategies, version history, parent/child
   lineage, run history) has to be built before any of this UI has data to show. Two constraints
   worth fixing in the schema from the start, because retrofitting either is painful: versions are
   append-only and store whole configs rather than deltas, and **every run row carries the
   `strategyVersion` it executed against**. Without that column the version comparison table cannot
   be built correctly at all — it would be joining runs to a config that has since changed.
   `BacktestResult` already records a `DataVintage` with a price-frame digest, which is exactly what
   the comparison table needs to detect that two versions' numbers differ because the data moved
   rather than the strategy; make sure persistence keeps it.
2. **There is no HTTP API yet.** The core is a library and the CLI is one consumer of it, so adding
   the API means wiring, not moving logic — but it does need writing, including a job queue, since
   optimize and walk-forward are minutes-long operations that cannot run inside a request.
3. **The optimizer is differential evolution, not a genetic algorithm.** Scipy's DE with
   `updating='deferred'` and a fixed seed, so results are reproducible regardless of worker count.
   The UI copy should say "search" or "differential evolution", never "genetic".
4. **A strategy has exactly one entry rule and one exit rule** (engine spec §7.1, changed
   2026-08-16). The engine previously crossed `E` entry variants with `X` exit variants and reported
   the best of `E·X` measured on the same data; that was a selection step that inflated the winner,
   its two selection criteria disagreed with each other, and it multiplied the trial count feeding
   the deflated Sharpe. It is gone, and with it `BacktestResult.variants`, `BacktestResult.best`,
   `VariantResult`, and the `entry_name`/`exit_name` fields on `OptimizationResult` and
   `FoldResult`. The config keys are now `entry:` and `exit:`, single mappings with no `name`.
   Comparing two entry conditions means two strategies and two runs — which the lineage model in §1
   already supports through Fork.
5. **`BacktestResult` does not expose an equity curve.** It carries `Metrics`, `Trade` records and
   yearly returns, but no per-bar equity or drawdown series. This is the single largest gap the
   Charts tab implies: charts 1, 2, 7 and 9 in §5 are all unbuildable without it, and chart 3 needs
   the price frame exposed alongside. `backtest/portfolio.py` already holds the series internally;
   the work is exporting them at the `domain/` boundary without leaking pandas or vectorbt into the
   public fields. `Metrics.yearly_returns` and `worst_rolling_12m_pct` already exist, so charts 8
   and part of 9 are servable today.
6. **`FoldResult` carries neither trades nor a per-bar series.** It has only `metrics` (aggregate)
   and `parameters` per fold. §5.1's per-fold Groups A/B (price with markers, PnL distribution,
   cumulative PnL, won/lost) need each fold's own `Trade` list and its own equity/price series over
   its test window — the same gap as item 4, but one level down, since each fold is its own
   independent backtest internally. Without it, a validation run's Charts tab can only ever show
   Group C (stitched from fold `metrics`) and Group D; a fold selected in place of Combined view
   would have nothing to render. Fix at the same time as item 5 — the walk-forward runner already
   evaluates each fold as a full backtest before discarding everything but `metrics`.
7. **Trades record no excursions.** `Trade` has entry, exit, PnL and holding days, but not the worst
   and best unrealized move while the position was open (MAE/MFE). Those would support a "how much
   heat did this trade take to earn its result" scatter, which is the natural chart for judging
   whether stops are set sanely. Vectorbt's trade records carry the underlying data; the domain type
   does not surface it. Not in §5 above for that reason — worth adding if the series work happens
   anyway.
8. **The optimizer discards per-trial scores.** `DeflatedSharpe.variance_estimated` exists precisely
   because a parallel search does not return them. Retaining them would allow an in-sample versus
   out-of-sample scatter across every evaluated candidate, which is what PBO measures and would draw
   the case for or against the search far better than a single probability. Cheap to keep, painful
   to reconstruct later.
9. **Multi-output indicator names are not currently enumerable from the config alone.** The signal
   editor's autocomplete needs an endpoint exposing the registry: each indicator type, its
   parameters, and the exact names it contributes to the namespace for a given config.
