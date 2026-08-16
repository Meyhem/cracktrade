# cracktrade — implementation plan

Rebuild of the `swing-trader` engine (`/home/meyhem/dev/swing-trader`, reference only — never
imported, never modified) as a multi-interface Python library. CLI is the first interface; an HTTP
API reusing the same core comes later.

## Decisions (agreed with user, do not re-litigate)

| Decision | Choice |
| --- | --- |
| Spec | `docs/ENGINE_SPEC.md` is the contract; written in Phase 0 before any code |
| Fidelity | Clean rebuild. Every defect in the spec's defect register is fixed. No legacy-compat mode. Results will **not** match the old system. |
| Universe | Exactly **one ticker per config** (`universe.ticker: str`). No multi-ticker, no shared-cash portfolios. |
| Persistence | **None.** Optimized YAML + metrics go to stdout. `-o` writes a file only when asked. No run catalogue, no DB. |
| Indicators | Registry with per-indicator param schemas, replacing the legacy fixed 21-field allowlist |
| Look-ahead | Structurally impossible — see "Causality" below. Non-negotiable. |
| Data source | yfinance behind a `MarketDataProvider` protocol. Disk cache opt-in via `--cache`. |
| Precision | `float64` (legacy used `float32`) |
| Train/test | Fit on train, select variant on train, report on test. **Walk-forward is the default protocol** (revised 2026-08-16 after audit; single split kept as a fast mode). |
| Determinism | Seeded RNG + `updating='deferred'`; reproducible even with `workers=-1` |
| LLM (later) | OpenAI-compatible base URL + model + key from settings; no provider-specific code |
| Stops vs holding | **Stops always fire.** `min_holding_days` suppresses only signal and time exits (decided 2026-08-16; a stop disabled for N days is not a stop). |
| Objective | **Calmar on train, with the trade-count floor as a hard feasibility constraint** (`+inf`), not a multiplier. Pluggable; legacy PnL objective selectable. |
| Selection bias | Treated with the same rigour as look-ahead: benchmark, fold dispersion, deflated Sharpe, PBO, parameter stability, cost sensitivity. Spec section 12. |

## Causality invariant

Every value used to decide at bar *t* is computable from bars ≤ *t*; every fill happens strictly
after the bar that produced the signal. Four independent enforcement layers:

1. **Grammar** — signal expressions are AST-whitelisted (names, numeric literals, comparisons,
   boolean ops, arithmetic, parens). `Call`, `Attribute`, `Subscript`, slicing are rejected at parse
   time, so `.shift(-1)`, `.iloc[t+1]`, `.rolling(center=True)` are not expressible.
2. **Registry** — indicators declare `warmup` and a causality classification; `center=True` is never
   passed; forward-projected outputs (Ichimoku senkou) are dropped or realigned; whole-sample
   normalisation is rejected.
3. **Single alignment path** — one `causal_shift(series, n)` that raises on `n < 0`. Repo lint bans
   `.shift(-` and `center=True`. Entries, exits, and per-bar risk series all go through it.
4. **Truncation-equivalence harness** — for sampled bar indices *t*, run the pipeline on history
   truncated at *t* and assert every indicator/signal/stop value at *t* is identical to the
   full-history run. Property-based over random strategies. Permanent CI gate.

Optimizer leakage is prevented by types: fitness accepts only a `TrainWindow`; the test window is a
distinct type reachable only from the reporting path.

Pessimistic conventions, all **verified empirically against vectorbt 1.0.0 and pinned by tests**:
same-bar SL+TP → the stop fires; gap through a stop → fill at the actual open, never the stop price;
`sl_stop=0.0` closes immediately while NaN means no stop; stop distances measured from the fill
price, not the entry bar's close; incomplete (today's partial) bar is dropped.

Residual biases outside engine control, documented not fixed: ticker selection is hindsight;
yfinance applies split/dividend adjustments retroactively.

## Architecture

```
src/cracktrade/
  domain/        frozen result models — pure, no pandas, no vectorbt in public fields
  config/        Pydantic v2 strategy schema, parse/serialize, error formatting
  data/          MarketDataProvider protocol + YFinanceProvider + OHLCV contract
  indicators/    IndicatorRegistry, pandas_ta adapters, engine builtins
  signals/       AST-whitelisted evaluation + causal shift
  backtest/      variant expansion, holding rules (signal_func_nb), vectorbt invocation
  optimize/      parameter discovery, search space, DE driver, fitness, train/test
  cli/           Typer app + render.py — parsing and rendering only
  errors.py  settings.py
```

Dependency direction is one-way: `cli -> optimize -> backtest -> signals -> indicators -> config ->
domain`; `data` sits behind a protocol. **vectorbt is imported only inside `backtest/`.** Core
returns typed result objects; the CLI is the only thing that renders them, which is the same seam
the future API will use.

## Phases

| # | Phase | Status |
| --- | --- | --- |
| 0 | Write `docs/ENGINE_SPEC.md` | done |
| 1 | Project skeleton and toolchain | done |
| 2 | Strategy configuration model | done |
| 3 | Market data layer | done |
| 4 | Indicator registry and computation | done |
| 5 | Signal layer + causality harness | done |
| 5.5 | Correctness patch: definedness mask (D15), fill provenance, UTC cutoff, float equality | done |
| 6 | Backtest engine | done |
| 7 | Metrics, benchmark, result models | done |
| 8 | Optimizer | done |
| 8.5 | Validation and robustness (walk-forward, DSR, PBO, stability, bootstrap, cost sweep) | |
| 9 | CLI output polish | |

Out of scope this pass: AI strategy generation, charts, persistence, HTTP API, multi-ticker,
Google Drive, Zulip, watchdog.

## Audit

`docs/AUDIT.md` (2026-08-16) reviews the engine from a financial and technical standpoint and is the
source of the Phase 5.5 and Phase 8.5 additions. Its conclusion in one line: the discipline applied
to look-ahead bias now has to be applied to **selection** bias, which is the failure mode that
actually costs money in a strategy optimizer.
