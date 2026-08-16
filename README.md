# cracktrade

A trading-strategy optimization engine. You describe a strategy in YAML, the engine searches its
parameters, and it tells you — plainly — whether the result is worth acting on.

That last part is the design constraint. A backtester that reports a large number is easy. One
that reports a large number *and* the reasons not to believe it is the useful kind, because the
ways a backtest lies are well understood and every one of them is checkable.

```
NOT CREDIBLE
  out-of-sample +6.6% vs +149.0% buy-and-hold, profitable in 2/4 folds
  x deflated Sharpe P=0.00, below the 0.95 bar for 4800 trials
  x a 10% parameter nudge destroys 81% of the objective
  x only 16 out-of-sample trades in total
```

That is real output, from one of the example strategies in this repository.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Use

```bash
uv run cracktrade validate examples/rsi_pullback.yaml
```

```bash
uv run cracktrade backtest examples/rsi_pullback.yaml
```

```bash
uv run cracktrade walkforward examples/rsi_pullback.yaml --folds 6
```

`backtest` runs a strategy as written and compares it against buying and holding the same
ticker. `optimize` searches the parameters on the first 80% of history and reports on the rest.
`walkforward` does that repeatedly across successive folds and then judges the result — it is
the one to use before putting money behind anything.

All three take `--format table|json|yaml` and `-o FILE`. Stdout carries the requested output and
nothing else, so this works:

```bash
uv run cracktrade backtest examples/rsi_pullback.yaml --format json | jq '.benchmark'
```

`walkforward --strict` exits 6 when the result fails its robustness checks, so it can gate a
pipeline.

## Writing a strategy

```yaml
strategy:
  name: rsi_pullback
universe:
  ticker: NVDA
  start_date: '2023-01-01'
  end_date: '2025-12-31'
execution:
  initial_capital: 10000.0
  commission_pct: 0.05
  slippage_pct: 0.1
indicators:
  - name: sma_long
    type: sma
    window: 200
  - name: rsi_ind
    type: rsi
    window: 14
entry:
  signal: (close > sma_long) & (rsi_ind < 35)
exit:
  atr_stop_multiplier: 2.5
  take_profit_pct: 18
  max_holding_days: 15
```

One entry rule and one exit rule per strategy, so a run is one simulation and one set of
numbers. To compare two entry conditions, write two strategies and run both — the engine used to
cross `E` entries with `X` exits and report the best of `E×X` measured on the same data, which is
a selection step that inflates whichever pair wins. `uv run cracktrade indicators` lists all 61
indicator types and the names each one contributes to the signal namespace.

Signals are expressions over price series and indicator outputs. Comparisons must be
parenthesised, because `&` binds tighter than `<`; the parser says so when you forget.

## What it refuses to do

**Look-ahead bias is not expressible.** Not discouraged — inexpressible. Signal expressions are
parsed into an AST and checked against a whitelist that excludes function calls, attribute
access and subscripting, so `close.shift(-1)`, `.iloc[t+1]` and `rolling(center=True)` are not
strategies that fail validation, they are strings that are not strategies. Indicators declare
their warm-up; outputs that are plotted into the future, like Ichimoku's leading spans, are
dropped rather than exposed. There is exactly one function in the engine that shifts a series in
time and it raises on a negative shift, with a repository test asserting nothing else calls
`.shift` at all.

The guarantee is *proven*, not asserted: a truncation-equivalence harness recomputes every
indicator on `history[:t+1]` and requires the value at bar `t` to match the full-history run bit
for bit.

**Undefined values do not become trades.** A NaN comparison is `False`, but `~(rsi > 30)` would
invert that into an active signal — and several indicators are undefined on most of their bars
by design (`supertrend_supertl` on 90% of them). Evaluation tracks definedness alongside value,
so undefined collapses to "no signal" under every operator, and the report says what fraction of
bars a condition could actually be evaluated on.

**Nothing is annualized on the wrong calendar.** 252 trading days, passed explicitly at every
call site.

## What it tells you that you did not ask for

- **The benchmark.** Every result is measured against buying and holding the same ticker over
  the same window under the same cost model. A strategy returning 13% where the ticker returned
  40% is a failure, and the report leads with that.
- **Fold dispersion.** Six folds, six numbers, plus the spread. A strategy profitable in 6 of 6
  and one carried entirely by fold 3 are different objects.
- **A deflated Sharpe ratio.** The maximum Sharpe over `N` trials is inflated even when there is
  no edge at all, so the observed value is compared against what luck alone would produce for
  the number of configurations actually scored.
- **The probability of backtest overfitting**, via CSCV over the fold matrix. Above 0.5 the
  selection procedure is worse than choosing at random.
- **Parameter stability.** The optimum is perturbed ±10% and ±20%. A plateau survives; a spike is
  a curve fit, and in a point estimate the two look identical.
- **Cost sensitivity.** The headline is recomputed at 2× and 3× slippage. An edge that dies when
  costs double is not tradeable.
- **When the sample is too small to say anything.** Below 20 out-of-sample trades the report says
  so instead of printing a CAGR to two decimal places.

## Known limits

Documented rather than hidden:

- **Ticker selection is hindsight.** You chose the symbol knowing its history. No engine can
  correct for that.
- **Prices are retroactively adjusted.** yfinance applies split and dividend adjustments across
  the whole series, so absolute price thresholds are not what they appear and the same backtest
  run months apart uses different data. Every result records a digest of the prices it used.
- **Numbers inside signal strings are not optimizable.** The `35` in `rsi_ind < 35` is not
  reachable by the search. Move it into an indicator to tune it.
- **One ticker per configuration.** No portfolios, no cross-sectional strategies.
- **Long only.**

## Development

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
```

mypy runs strict and ruff has the full rule set enabled. Both must be clean and the suite green
before anything is considered done.

`docs/ENGINE_SPEC.md` is the normative contract — behaviour is specified there first and the
code follows. `docs/AUDIT.md` records the financial and technical review that produced the
validation phase, including the mistakes found along the way.

## Status

The engine, optimizer and validation suite are complete. AI-assisted strategy generation, charts,
persistence and the HTTP interface are not built yet; the core is a library and the CLI is one
consumer of it, so adding another interface does not mean moving any logic.
