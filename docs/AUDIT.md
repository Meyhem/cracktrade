# cracktrade — engine quality audit

Date: 2026-08-16. Audited against the code at Phase 5 completion (Phases 6–9 unbuilt).

The question this document answers is not "is the code clean" — it is **"if a user optimizes a
strategy with this and puts real money behind the output, what could make that decision wrong?"**

Findings are ranked by that criterion, not by how hard they are to fix.

---

## 0. Scope: what actually exists

| Layer | State | Bears on the investment decision? |
| --- | --- | --- |
| Config / strategy schema | built | indirectly |
| Market data | built | yes — garbage in |
| Indicators (61) | built | yes |
| Signals + causality harness | built | yes — critically |
| Backtest engine | **not built** | yes — critically |
| Metrics | **not built** | yes — critically |
| Optimizer | **not built** | yes — critically |
| Validation / robustness | **not designed** | **the whole decision** |

Roughly half the machinery that determines whether a reported number is trustworthy does not
exist yet. That is the honest headline. The built half is strong; the unbuilt half is where the
financial risk concentrates, and the current spec for it has real methodological gaps (§2).

---

## 1. Defects in shipped code

### A1 — CRITICAL. Undefined indicator values produce trades

**Verified empirically.** NaN is silently treated as `False` in signal evaluation, and the grammar
permits `~`, so negation converts "undefined" into an active **True** signal.

```
rsi           = [nan nan nan  50  60 nan nan  20  25  70]
"rsi > 30"    → [ 0   0   0   0   1   1   0   0   0   0]     # safe
"~(rsi > 30)" → [ 0   0   0   0   0   0   1   1   1   1]     # fires off NaN bars
```

Warm-up suppression (`prepare_signal`) only covers **leading** NaN. It does nothing for interior
NaN — and interior NaN is not an edge case, it is the normal output of several registered
indicators. Measured over 300 synthetic bars:

| Output | Leading NaN | **Interior NaN** | Share of usable bars |
| --- | --- | --- | --- |
| `psar_psarl` | 11 | **177** | 61% |
| `psar_psars` | 1 | **112** | 37% |
| `supertrend_supertl` | 6 | **264** | **90%** |
| `supertrend_superts` | 20 | **16** | 6% |

This is by design in pandas_ta — the *long* parabolic SAR only has a value while the trend is up.
But it means a natural strategy such as `~(close < psar_psarl)` takes positions on 61% of bars
purely because the indicator is undefined there. The engine's headline guarantee ("no decision
rests on an undefined indicator") holds only at the start of the series.

A second, quieter face of the same bug: even without `~`, `close > psar_psarl` is silently `False`
on 61% of bars. The strategy the user thinks they wrote is not the one being tested, and nothing
reports the discrepancy.

**Fix.** Propagate a *definedness mask* alongside evaluation: every referenced series contributes
`notna()`, the mask is `AND`-ed across the expression tree, and the final signal is forced `False`
wherever the mask is `False` — before the next-open shift. Three-valued logic, collapsed to a safe
`False` at the boundary. Then report per-signal `defined_pct` so a strategy that is undefined 90%
of the time is visible rather than merely quiet.

### A2 — MEDIUM. `year_freq` is not a `Portfolio.from_signals` parameter

Spec §7.5 lists `year_freq='252 days'` in the `from_signals` argument table. **It is not one of
that function's 60 parameters in vectorbt 1.0.0.** It lives in the process-global
`vbt.settings['returns']['year_freq']`, which defaults to `'365 days'`, and it is accepted as a
per-call argument on the metric methods (`sharpe_ratio`, `sortino_ratio`, `annualized_return`,
`max_drawdown`, `calmar_ratio` — confirmed; `total_return` does not take it, correctly).

Left unfixed, every annualized metric is computed on a 365-day year over 252 trading bars: CAGR
overstated, Sharpe inflated by √(365/252) ≈ 1.20×. Mutating the global from inside a library is
also unacceptable — it would leak into any other vectorbt user in the same process.

**Fix.** Pass `year_freq='252 days'` explicitly at each metric call site. Never touch
`vbt.settings`. Assert it with a test that computes Sharpe both ways and shows they differ.

### A3 — MEDIUM. Forward fill fabricates zero-return bars, invisibly

`prepare_frame` applies `ffill()` and then only checks that no NaN *survives*.

**Corrected during implementation** — the first version of this finding said a filled bar has
`High == Low == Close`. It does not: `ffill` carries the *entire previous row* forward, which is
worse. The fabricated bar has a zero close-to-close return **and** a high/low range copied from a
different session. So realised volatility is understated (Sharpe overstated), and a stop-loss can be
triggered on that date by a price that never traded on it. The test asserting the flat-bar version
failed, which is how the error surfaced.

**Fix.** Count filled cells, carry the count on `MarketData`, surface it in the result, and reject
a history where filled bars exceed a configurable fraction (suggest 1%).

### A4 — LOW/MEDIUM. `date.today()` is machine-local

`_drop_incomplete_bar` compares a UTC-normalized index against the host's local date. The
determinism claim ("a run at 14:00 and a run at 22:00 see identical history") is true within one
machine but not across machines in different time zones.

**Fix.** Compare in UTC, and document that the guarantee is per-UTC-day.

### A5 — LOW. Float equality is expressible in signals

The grammar accepts `==` and `!=` between float price series. `close == sma_20` is essentially
never true and is always a user error.

**Fix.** Reject `==`/`!=` where both operands are series, with a message pointing at a tolerance
comparison.

### What is genuinely right

Worth stating, because it is the foundation the rest sits on: the truncation-equivalence harness is
a real proof obligation, not a smoke test, and it caught real deviations that were then
investigated individually rather than tolerance-suppressed. The single-`causal_shift` chokepoint
with an AST-enforced repo-wide ban on any other `.shift` is the right architecture for this
invariant. Dropping ichimoku's forward-projected spans and the chikou span rather than exposing
them was correct and is the kind of thing most backtesters get wrong. Measured warm-up taken as
`max(declared, observed)` across all indicators is conservative in the right direction.

---

## 2. Methodological gaps in the unbuilt phases

These are not bugs — the code does not exist. They are the places where the current spec, if built
exactly as written, would produce numbers that look authoritative and are not.

### B1 — CRITICAL. There is no benchmark

Spec §9.5 reports optimized-vs-unoptimized *of the same strategy*. That answers "did the optimizer
do something", not "should I invest". The decisive comparison is against **buy-and-hold of the same
ticker, over the same window, under the same cost model**. A strategy returning 13% on a ticker
that returned 40% is a failure the current report would present as a success.

vectorbt supports this directly — `Portfolio.sharpe_ratio(benchmark_rets=…)` and friends — so the
cost is low. Report buy-and-hold CAGR / MDD / Sharpe alongside, plus the excess return and the
information ratio.

### B2 — CRITICAL. The fitness function is not a defensible objective

Ported verbatim from legacy (§9.3):

```
fitness = pnl * (1 ± mdd);  if trades < 5: pnl *= 0.1;  maximum across variants
```

Four independent problems:

1. **Raw PnL is scale-dependent and outlier-dominated.** One lucky trade outranks a hundred
   consistent ones. It is not a risk-adjusted objective in any recognised sense.
2. **The small-sample penalty is soft.** `trades < 5 → ×0.1` is defeated by any fluke bigger than
   10×. A 3-trade result should be *infeasible*, not discounted.
3. **The drawdown term barely bites.** A typical −20% drawdown scales fitness by 0.8. Drawdown is
   the constraint most investors actually care about and it is doing almost no work here.
4. **Model selection happens inside the objective.** "maximum across variants" means every single
   DE evaluation reports the best of `E×X` variants. The in-sample optimum is therefore the maximum
   of `E·X·N` random variables and is upward-biased by construction — before any out-of-sample step
   even runs.

**Fix.** Make the objective pluggable, default to a risk-adjusted one (Calmar or Sortino on the
train window), express the trade-count floor as a **hard feasibility constraint** returning `+inf`,
and count `E·X·N` as the trial count for B4.

### B3 — CRITICAL. One 80/20 split cannot support an investment decision

A single contiguous test window is a single draw. If the last 20% happens to be a bull market,
every strategy passes; if it is 2022, every strategy fails. Neither outcome carries information
about the strategy.

Worse, the split is a **one-shot resource** and the workflow burns it. The user will look at the
test result, adjust the strategy, and re-run — at which point the test window has been fit to,
through the user, and the "out-of-sample" label is false. Nothing in the design prevents or even
records this.

**Fix.** Walk-forward validation (anchored and rolling folds) is not a deferred nicety, it is the
default reporting protocol. Report **per-fold dispersion**, not just an aggregate: a strategy
positive in 7 of 8 folds is a different object from one whose entire edge is in fold 3. Additionally
record a run counter per strategy file so repeated optimization against the same data is at least
visible in the output.

### B4 — HIGH. Overfitting is never quantified

DE at `popsize=15 × dim` over `epochs` generations performs thousands of trials. The maximum Sharpe
over `N` independent trials on *pure noise* is inflated by roughly `√(2 ln N)` standard errors —
for N = 5,000 that is about 2.9σ. A backtest with no edge whatsoever routinely produces an
impressive-looking optimum, and the current design has no way to say so.

**Fix.** Two standard instruments, both directly implementable from data the engine already has:

- **Deflated Sharpe Ratio** (Bailey & López de Prado) — adjusts the observed Sharpe for the number
  of trials, the sample length, and the skew/kurtosis of returns. Output is a probability that the
  true Sharpe exceeds zero.
- **Probability of Backtest Overfitting** via CSCV over the walk-forward fold matrix from B3 —
  the frequency with which the in-sample-best configuration underperforms the median out of sample.

These convert "it returned 22%" into "the probability this is noise is X%", which is the number the
user's money actually depends on.

### B5 — HIGH. No parameter-stability check

DE returns a single point. Financially, a usable optimum is a **plateau**; a sharp spike is a
curve-fit. The engine currently has no way to distinguish them, and they look identical in the
report.

**Fix.** After the search, perturb each optimized parameter across a local grid (±10%, ±20%) and
report the objective's degradation surface. Flag any result where a single-parameter ±10%
perturbation destroys a large fraction of the objective. Cheap to compute, and it is the single
most honest overfitting tell available.

### B6 — HIGH. `min_holding_days` suppressing stop-losses is a dangerous semantic

Spec §7.2 targets holding constraints that "bind SL/TP as well as signal exits" — i.e. a configured
stop-loss does **not** protect the position for the first `min_holding_days`. That is the opposite
of what a stop-loss is for, it materially fattens the left tail of the return distribution, and it
means a backtest's drawdown figure reflects a risk control the user believes they configured but
which was disabled.

The entire fixed-point iteration planned for Phase 6 exists only to enforce this semantic against
realised positions. Deciding it the other way removes both the danger and most of the complexity.

**Fix (recommended).** Stops always fire. `min_holding_days` suppresses only signal exits and time
exits. If the legacy semantic is kept, it must be stated in the report on every run that uses it.
**This decision must be made before Phase 6 is written.**

### B7 — MEDIUM/HIGH. Results carry no statistical qualification

A 20% test window may contain six trades. Six trades support no conclusion, but the report will
print a CAGR to two decimal places regardless.

**Fix.** Report the test-window trade count prominently, bootstrap confidence intervals on mean
trade return and on CAGR, and suppress the headline verdict below a minimum trade count rather than
printing a precise-looking number.

### B8 — MEDIUM. The cost model is optimistic and untested for sensitivity

Flat `commission_pct` plus a fixed `slippage_pct` ignores that slippage scales with volatility and
inversely with liquidity, and that filling at the open — where gaps concentrate — is the worst case.
Modelling this properly is a large commitment; testing sensitivity to it is not.

**Fix.** Re-report the headline metrics at 1×, 2× and 3× the configured slippage. A strategy whose
edge disappears at 2× costs is not tradeable, and this exposes it in three extra backtests.

### B9 — MEDIUM. Idle cash earns nothing while the risk-free rate is charged as a hurdle

A strategy in the market 25% of the time is charged a 4% annual hurdle across 100% of the period
while earning 0% on cash for the other 75%. Excess return over the risk-free asset is a defensible
frame, but it is a *decision*, and the alternative (accrue the risk-free rate on idle cash)
materially changes low-exposure results.

**Fix.** State the convention in the report and always show `exposure_pct` next to Sharpe so the
number is interpretable.

### B10 — MEDIUM. Adjusted prices make results non-reproducible over time

`auto_adjust=True` retro-adjusts the whole series on every dividend and split. A backtest run today
and the same backtest run next quarter use different price histories. §2.7 documents this as a
residual bias; it also has a reproducibility consequence that is not documented.

**Fix.** Record the data vintage (fetch date, first/last bar, a hash of the frame) in the result, so
a rerun that differs can be explained rather than mistrusted. Additionally, warn when a signal
compares a raw price series against an absolute constant, since those thresholds are meaningless
under retro-adjustment.

### B11 — MEDIUM. No sub-period breakdown

"All the profit came from March 2020" is the most common way a backtest lies, and a single aggregate
figure cannot show it.

**Fix.** Per-calendar-year return table, plus worst rolling 12-month return, in every report.

---

## 3. Improvement plan

Ordering principle: fix what is cheap now and expensive later, resolve the decisions that determine
architecture before writing the code they constrain, and treat validation as a first-class phase
rather than a postscript.

### Phase 5.5 — Correctness patch to shipped layers

Small, self-contained, and much cheaper now than after three layers are built on top.

1. Definedness mask through signal evaluation; signal forced `False` on any undefined input (A1).
2. `defined_pct` per signal, carried to the reporting layer (A1).
3. Filled-bar accounting on `MarketData` + rejection threshold (A3).
4. UTC-based incomplete-bar detection (A4).
5. Reject series-to-series `==` / `!=` in the grammar (A5).
6. Conformance tests: a `psar`-based negation strategy takes zero positions on undefined bars.

### Phase 6 — Backtest engine *(unchanged scope, one decision first)*

**Resolved: stops always fire.** `min_holding_days` suppresses only signal and time exits.

**Built, and the design changed twice under measurement.** The specced fixed-point iteration turned
out to be a chain rather than a contraction — it resolves one trade per pass, so a 200-trade history
would need 200 simulations per variant. It was replaced by applying the holding rules inside
vectorbt's `signal_func_nb`, which evaluates them against realised positions in a single pass. Spec
§7.2 records all three designs and why the first two failed.

**Two library defaults were found to be actively wrong for this engine**, both caught by tests
rather than by reading documentation: `signals.clean` *deletes* an exit that collides with an entry,
which erased `max_holding_days` outright (§7.3 now drops the step); and `stop_entry_price` defaults
to `'close'`, measuring stop distances from the entry bar's close rather than from the price
actually paid at the open (§7.5 now pins `'fillprice'`).

Conventions verified empirically against vectorbt 1.0.0 and pinned by tests: stop-loss wins a bar
that touches both stops, a gap fills at the actual open, `sl_stop=0.0` closes immediately while NaN
means no stop.

### Phase 7 — Metrics, benchmark, and reporting integrity

- `year_freq='252 days'` at every metric call site, never via global settings (A2).
- Buy-and-hold benchmark on the identical window and cost model; excess return; information ratio (B1).
- Per-year return table and worst rolling 12-month (B11).
- `exposure_pct` beside every risk-adjusted metric; cash convention stated (B9).
- Data vintage recorded in the result (B10).

### Phase 8 — Optimizer

- Pluggable objective; default Calmar or Sortino on train (B2).
- Trade-count floor as a hard feasibility constraint, not a multiplier (B2).
- Trial counting (`E · X · N`) carried into the result for use by Phase 8.5 (B2, B4).
- Everything already specified in §9: seeding, `integrality`, `+inf` failure scoring, type-separated
  train/test windows.

### Phase 8.5 — Validation and robustness *(new, and the highest-value phase in the plan)* — **DONE**

1. Walk-forward fold generator (anchored + rolling), replacing the single split as the default (B3).
2. Per-fold result matrix with dispersion reporting (B3).
3. Deflated Sharpe Ratio using the real trial count (B4).
4. Probability of Backtest Overfitting via CSCV over the fold matrix (B4).
5. Parameter-stability surface around the optimum (B5).
6. Bootstrap confidence intervals on the headline metrics (B7).
7. Cost sensitivity sweep at 1× / 2× / 3× slippage (B8).

### Phase 9 — CLI output

Lead with the verdict, not the equity curve. Order: benchmark comparison → out-of-sample dispersion
across folds → PBO / deflated Sharpe → parameter stability → cost sensitivity → the headline
metrics. Suppress the headline entirely when the trade count cannot support it.

---

## 4. The one-paragraph summary

The causality architecture is genuinely strong and the built layers are well made, with one real
defect: undefined indicator values leak into trading decisions through negation, and two registered
indicators are undefined on the majority of bars. The larger risk is not in what is built but in
what is specified: as currently designed, the optimizer would maximize an outlier-dominated raw-PnL
objective across thousands of trials, select among variants inside every evaluation, validate on a
single contiguous window, and report a number with no benchmark, no confidence interval, and no
overfitting diagnostic. Every one of those is individually enough to make a result that looks
compelling and is not. The engineering discipline applied to look-ahead bias now needs to be applied
to selection bias, which is the failure mode that actually costs money in a strategy optimizer.


---

## 5. Outcome

Every finding above is implemented. The suite's own verdict on the two shipped example
strategies, run over real market data, is the shortest summary of why it was worth building.

`momentum_breakout_v2` on MSFT, four anchored walk-forward folds:

```
NOT CREDIBLE
  out-of-sample +6.6% vs +149.0% buy-and-hold, profitable in 2/4 folds
  x deflated Sharpe P=0.00, below the 0.95 bar for 4800 trials
  x a 10% parameter nudge destroys 81% of the objective
  x only 16 out-of-sample trades in total
  x returned +6.6% out of sample against +149.0% for buy-and-hold

fold returns: +16.21%, -2.31%, -8.23%, +2.37%   (median +0.03%, IQR 14.56 pp)
```

A single 80/20 split landing on the first fold would have reported +16.21% and looked like a
find. That is the failure this phase exists to prevent, and it is not hypothetical — it is what
the original design would have printed.

Two units bugs were caught by building the statistics rather than by reading about them. The
deflated Sharpe mixes the ratio with the observation count, so both must be per-period; feeding
it annualised trial Sharpes inflated the luck threshold by a factor of about 16 and failed every
strategy regardless of merit. And a 10% perturbation of a small integer parameter rounds back
onto itself, which would have reported perfect stability for a test that never ran.
