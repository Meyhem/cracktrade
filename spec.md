# Plan: Engine Reimplementation Spec (strategy format · backtester · optimizer)

## Context

`swing-trader` is a ~3.9k LOC Python app that backtests and optimizes YAML-defined swing
strategies on daily OHLCV. Three subsystems are being lifted into a **new Python backend that
keeps vectorbt**; Google Drive and Zulip are explicitly out of scope and get dropped.

The knowledge needed to rebuild these subsystems is currently spread across code, a Zulip
`explain_*` handler that doubles as user documentation, an LLM system prompt in
`src/bot/generator.py` that is the most complete strategy-format reference in the repo, and
`.agents/AGENTS.md` invariants. Several behaviours are load-bearing but undocumented (indicator
zero-fill, per-column capital isolation, next-open shift chain), and several are defects that
would be silently inherited by a naive port.

**Deliverable:** one self-contained markdown spec, precise enough to rebuild strategy parsing,
the backtest engine, and the optimizer without reading the old code — with defects called out
separately so the rebuild fixes them deliberately rather than by accident.

Decisions taken (confirmed with user):
- Target: **Python, vectorbt retained.** Library semantics referenced, not restated; but every
  vectorbt default the current code relies on implicitly must be listed as "pin explicitly".
- Fidelity: **document current behaviour as source of truth, then a dedicated defect section**
  with recommended target behaviour for each.
- Optimizer: document the DE engine as built, but specify **fit-on-train / select-on-train /
  report-on-test** (plus optional walk-forward) as the target evaluation protocol.

## Output

Single file: **`docs/ENGINE_SPEC.md`** (new `docs/` dir). Then publish as a private Artifact and
hand over the URL, so it is readable outside the repo.

## Source material (already read — no further exploration needed)

| Area | Files |
| --- | --- |
| Strategy format | `src/core/config.py`, `src/bot/generator.py:8-392` (schema reference + 2 full examples), `src/bot/handlers/explain.py:28-371` (field-by-field user doc) |
| Data contract | `src/data/market.py` |
| Engine | `src/execution/{indicators,signals,portfolio,backtester}.py` |
| Metrics | `src/metrics.py` (dead code), `src/optimization/evaluator.py:85-107` (the real summary) |
| Optimizer | `src/optimization/optimizer.py`, `src/optimization/evaluator.py` |
| Orchestration | `src/cli/main.py:64-168` |
| Artifacts | `src/storage/{client,local}.py`, `src/execution/{charting,evaluation_charts}.py` |
| Invariants | `.agents/AGENTS.md` (next-open rule, no negative shift, no vectorbtpro, index preservation) |
| Baseline tests | `tests/test_{execution,indicators,optimizer}.py` |

## Spec structure

1. **Scope & system overview** — three subsystems, dependency graph, what is dropped (GDrive,
   Zulip, watchdog, OpenAI generator), pinned versions (`vectorbt 1.0.0`, `pandas-ta 0.4.71b0`,
   `pandas 2.3.3`, Python ≥3.12) per `uv.lock`.

2. **Strategy configuration format** — full YAML contract from the Pydantic models: 7 sections
   (`strategy`, `universe`, `execution`, `indicators`, `entry_variants`, `exit_variants`,
   `position_sizing`), every field with type/default/units/validation rule. Must capture:
   - `ConfigModel` is `strict=True` but does **not** forbid extra top-level keys — this is why a
     `watchdog:` block survives parsing and is ignored. Reimplementation must decide: keep the
     escape hatch or forbid.
   - `IndicatorModel` uses `extra='forbid'` with a **fixed 21-field allowlist** (`name, type,
     source, window, fast, slow, signal, std, k, d, smooth_k, multiplier, lower_length,
     upper_length, af0, af, tenkan, kijun, senkou, bb_length, kc_length`) — the single biggest
     extensibility constraint in the schema.
   - `ExecutionModel` silently ignores `entry_price`/`exit_price` (present in tests, absent from
     the model) — execution venue is hardcoded to next-open.
   - Date fields are `str` compared lexicographically, not `date`; `end_date` defaults to today.
   - Human-facing validation-error formatting contract in `parse_config`.
   - Two complete worked examples lifted from `generator.py`.

3. **Market data contract** — yfinance shape (single-ticker flat columns vs MultiIndex
   `(Price, Ticker)`), the `.capitalize()` normalisation, end-date exclusivity +1 day,
   `ffill().bfill()`, `float32` cast, DatetimeIndex-must-survive rule. Defines the exact frame the
   engine consumes so the new backend can swap the data source.

4. **Indicator layer** — the `locals_dict` namespace (raw `open/high/low/close/volume` +
   one key per indicator); `df_filled = df.ffill()` fed to indicators while the namespace holds
   raw series; `rolling_max`/`rolling_min` as engine builtins; pandas_ta dispatch by
   `getattr(ta, type)` with signature introspection auto-injecting `high/low/close/volume`;
   `window → length` remap; multi-output fan-out naming `{name}_{first_token_lowercased}`
   (`my_bb_bbl`, `my_macd_macdh`, `my_stoch_stochk`); per-ticker loop + recombination in
   `apply_ta`; the `.fillna(0)` on every output.

5. **Signal layer** — `pd.eval` with `local_dict`, allowed operators, the "wrap comparisons in
   parens, use `&`/`|`" rule, no function/method calls, and the **`.shift(1)` next-open chain**:
   condition true on close of D → boolean lands on D+1 → vectorbt fills at `Open[D+1]`. Pin the
   `tests/test_execution.py` assertion (signal at index 20 → entry timestamp index 21) as the
   conformance test.

6. **Backtest engine** — cartesian product entry × exit variants; exit composition
   (`signal_exit | entries.shift(max_holding_days)`, then AND-NOT the min-holding mask built from
   `entries.shift(1..min_holding_days)`); `entries.vbt.signals.clean(exits)`; then
   `PortfolioBuilder`: stop-loss **priority chain** (`atr_stop_multiplier` > `trailing_stop_pct` >
   `stop_loss_pct`, `take_profit_pct` orthogonal), ATR stop as a per-bar percent series
   `(ATR(14) * mult / Close).shift(1)`, position sizing map
   (`fixed_pct`→`percent`, `fixed_cash`→`value`, `fixed_shares`→`amount`, default `inf`/`amount`),
   fees/slippage as `pct/100`, `freq='d'`.
   - **Explicit-defaults subsection** (per the chosen approach): `direction`, `cash_sharing`,
     `accumulate`, `stop_exit_price`, `init_cash` broadcast, `size_granularity` — currently all
     implicit library defaults and therefore version-fragile. Each must be pinned in the rebuild.
   - **Capital model callout**: multi-ticker runs are N *independent* single-asset portfolios each
     seeded with the full `initial_capital`, not one shared-cash portfolio. Every "portfolio-level"
     number downstream is a sum of independent runs. This is the most consequential domain fact in
     the spec.

7. **Metrics** — the six-metric `MetricsRegistry` (CAGR, max drawdown, win rate, Sharpe, profit
   factor, Sortino) *and* the note that it is dead code; the metrics actually shipped are computed
   inline in `evaluator.calculate_full_pnl` (`Total Trades`, `Win Rate [%]`, `Profit Factor`,
   `Total PnL`, `CAGR [%]`, `Max Drawdown [%]`). Spec defines one merged metric set for the rebuild.

8. **Optimization engine**
   - Parameter discovery: recursive traversal of `indicators`, `entry_variants`, `exit_variants`
     only; every non-bool int/float leaf becomes a parameter; bounds `±50%` (`min/max(v*0.5,
     v*1.5)`, zero → `[-0.5, 0.5]`); int-ness recorded from the YAML literal and re-applied via
     `round()` on injection (floats rounded to 2dp). Numbers embedded in `signal:` strings are
     **not** reachable — the main expressiveness limit.
   - DE configuration: `strategy='best1bin'`, `popsize=15`, `mutation=(0.5,1)`,
     `recombination=0.7`, `maxiter=epochs`, `workers=-1`, `updating='deferred'`, Latin-hypercube
     seeding of `popsize*dim - 1` points with the baseline config injected as member 0.
     Evaluation budget ≈ `(epochs+1) * popsize * dim`.
   - Fitness: max over variants of `pnl * (1 + mdd)` when `pnl > 0`, `pnl * (1 - mdd)` when
     `pnl <= 0` (mdd is a negative fraction, so both directions penalise); `pnl *= 0.1` when
     trades < 5; negated for scipy minimisation; ticker-scoped vs aggregated modes.
   - Post-run pipeline: best entry×exit chosen on train → `prune_variants` collapses config to
     that single pair and narrows `universe.tickers` to the target → full re-run for reporting →
     parameter change diff filtered to the surviving variants.
   - Orchestration: `[None] + tickers` ⇒ one `"overall"` run plus one run per ticker, each a
     complete independent DE.
   - **§ Target evaluation protocol** (chosen option): fit on train, select variant on train,
     **report on test**; optional rolling walk-forward (fold count, anchored vs rolling, no
     leakage across the boundary); keep the current in-sample reporting available behind a flag so
     old vs new results can be diffed during migration.

9. **Output artifacts** — per-target folder layout
   `optimizations/<strategy>/<target>/`, `<strategy>_optimized_<target>.yaml` (with the mutated
   `strategy.name`), `trades.csv` (vectorbt `records_readable` + `Variant` + `Ticker`, sorted by
   entry), `summary.csv` columns, and the four Plotly HTML charts (per-ticker candlestick +
   trade overlay, aggregate equity curve, monthly-returns heatmap, return distribution,
   contribution-by-ticker). Plus the `StorageClient` ABC as the seam for the new backend.

10. **Defects & recommended target behaviour** — one entry each, with current behaviour, failure
    mode, and recommendation:
    - Indicator `.fillna(0)` over warm-up ⇒ `close >= rolling_max` is true from bar 0 ⇒ spurious
      day-1 entries. Recommend NaN propagation + signal suppression until `max(window)`.
    - `df_test` computed and never read; headline PnL is in-sample (superseded by §8 target).
    - `execution.risk_free_rate` parsed but ignored — Sharpe/Sortino hardcode `0.04`.
    - `risk_free=0.04` passed to vectorbt as a **per-period** rate while being an annual figure.
    - ATR stop calls `ta.atr` directly on multi-ticker DataFrames, bypassing `apply_ta` — breaks
      or silently misbehaves for multi-ticker + `atr_stop_multiplier`.
    - `sl_stop` `.fillna(0)` — a 0.0 stop is not "no stop" in vectorbt; verify against pinned
      version, likely an immediate-exit on the first bar.
    - `min_holding_days` masks only signal/time exits; vectorbt-internal SL/TP fire regardless.
      Both holding-day rules key off *entry signals*, not realised positions.
    - `calculate_full_pnl` can raise `UnboundLocalError` on `variant_pnl` when a target ticker
      matches no column; swallowed by a bare `except` that returns zeros.
    - Fitness returns `0.0` on any exception ⇒ an invalid config outranks a genuinely losing one,
      biasing DE toward unparseable regions.
    - `bfill()` in the loader fabricates pre-listing history for late-IPO tickers.
    - Continuous DE bounds over integer parameters produce a piecewise-flat landscape.
    - No RNG seed + `workers=-1` ⇒ non-reproducible optimizations.
    - `float32` prices; `freq='d'` hardcoded (daily only); long-only is a library default, never
      stated.
    - `indicators` as a **dict** in `tests/test_optimizer.py` vs the **list** the schema requires —
      the test bypasses validation. New backend must pick one shape.
    - Duplicate-strategy-name detection lives in the storage layer, not validation.

11. **Reimplementation checklist** — module boundaries for the new backend, the public API surface
    (`parse_config`, `load_history`, `run_backtest`, `optimize`, `evaluate`), the storage seam, and
    a conformance test list derived from the three existing test files plus new cases for each
    defect above.

## Verification

- Re-read `docs/ENGINE_SPEC.md` end to end against each source file listed above; every numeric
  constant, default, and formula in the spec must be traceable to a `file:line`.
- Cross-check the field tables against all three independent descriptions of the format
  (`config.py` models, `generator.py` prompt, `explain.py` handler) and reconcile the places where
  the prose docs and the code disagree (e.g. `fixed_pct` described as "% of account" while
  vectorbt's `percent` sizing is % of available cash).
- Confirm the spec's next-open claim matches the assertion in `tests/test_execution.py:69`.
- No code is modified and no tests are run — this task produces documentation only.
