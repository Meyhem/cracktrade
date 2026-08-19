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

`universe.interval` chooses the bar width — `15m`, `30m`, `1h` or `1d`, defaulting to `1d`. See
[Intraday strategies](#intraday-strategies) for what changes when it is not daily.

One entry rule and one exit rule per strategy, so a run is one simulation and one set of
numbers. To compare two entry conditions, write two strategies and run both — the engine used to
cross `E` entries with `X` exits and report the best of `E×X` measured on the same data, which is
a selection step that inflates whichever pair wins. `uv run cracktrade indicators` lists all 61
indicator types and the names each one contributes to the signal namespace.

Signals are expressions over price series and indicator outputs. Comparisons must be
parenthesised, because `&` binds tighter than `<`; the parser says so when you forget.

## Intraday strategies

```bash
uv run cracktrade backtest examples/sap_intraday.yaml
```

Set `universe.interval` to `15m`, `30m` or `1h` and three things change.

**Nothing is held overnight.** Whatever is open is sold on the session's last bar, and no
position is opened there. The close time is *learned* from recent completed sessions rather than
read from a hard-coded calendar, so an early close before a holiday is handled by the same
mechanism as an ordinary day — and a session whose close cannot yet be predicted is not traded
at all. Every result reports how many positions were nonetheless carried overnight; on a healthy
run it is zero, and it is reported rather than suppressed.

**Bars stop being days.** `min_holding_days` and `max_holding_days` are refused outright on an
intraday strategy; use `min_holding_bars` and `max_holding_bars`, which mean what they say.
Every window and floor in the engine is counted in *sessions* rather than bars, because 30 bars
is a reasonable test window when a bar is a day and under two Xetra sessions when it is half an
hour.

**History runs out fast.** Yahoo serves 15-minute and 30-minute bars for about 55 days and
hourly bars for about two years. A date range wider than that is refused with the limit named,
rather than quietly truncated. Every result carries how many sessions it actually covered and
says so plainly when that is too few to conclude anything — which, at 15m and 30m, is most of
the time. Evolution is unavailable below `1h` for the same reason: four segments and a holdout
do not fit in eight weeks.

Timestamps are the exchange's own local wall clock, with no timezone attached. A Xetra bar
reads 09:00 whether you are in Frankfurt or Chicago, and EU daylight-saving switches — which
fall on different dates from the US ones — never move a session boundary.

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

**Nothing is annualized on the wrong calendar.** The annualisation factor is measured from the
history rather than assumed: 252 sessions for daily bars, and for intraday bars the sessions
times however many bars one session of that venue actually holds — 17 on a 30-minute Xetra day,
13 on a New York one. A frame whose spacing contradicts the declared `interval` is refused,
because that is exactly the mix-up the measurement exists to prevent.

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
- **Intraday history is short and cannot be lengthened.** 55 days at 15m and 30m is a limit of
  the data source, not a setting. Results over it are real and are flagged as thin, every time.
- **Intraday analysis is historical only.** There is no live evaluation and nothing sends alerts;
  the engine tells you what a strategy would have done, not what to do now.

## Development

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
```

mypy runs strict and ruff has the full rule set enabled. Both must be clean and the suite green
before anything is considered done.

`docs/ENGINE_SPEC.md` is the normative contract — behaviour is specified there first and the
code follows. `docs/AUDIT.md` records the financial and technical review that produced the
validation phase, including the mistakes found along the way.

### Working on the API layer

The HTTP interface and its persistence (spec §14–§15) need a PostgreSQL server. One is defined
in `docker-compose.yml`; it hosts the database and nothing else, since the server and worker are
run straight from the checkout.

```bash
docker compose up -d
```

Its credentials are the code defaults, so nothing needs configuring. `.env.example` lists every
knob if you want to change one — copy it to `.env`, which is git-ignored.

Tests that need the database are marked `db`:

```bash
uv run pytest -m db
```

They **skip** when no server is reachable, so the engine's own suite still runs on a checkout
without Docker. That is a deliberate hole: a suite can report green having proved nothing about
the database. Close it wherever that matters — CI, or before a commit that touches this layer:

```bash
CRACKTRADE_API_TEST_DB_REQUIRED=1 uv run pytest -m db
```

Each test runs against its own database, cloned from a session template and dropped afterwards,
so tests cannot influence one another and none of them touch the development database.

### Running the API

`./scripts/dev.sh up` starts the database, applies pending migrations, and starts the server,
worker and web UI as tracked background process groups — `down` stops exactly what it started,
`status` shows what's running and who holds ports 8000/5173 if it wasn't this script, and `logs`
follows any of them. It exists because `uv run cracktrade-api serve` is really three processes
(the `uv` wrapper, the console script, and whatever it forks); killing only the one you can see
leaves the rest orphaned and still holding the port or a worker lease. `./scripts/dev.sh --help`
lists every command; pass a subset of `api`, `worker`, `web` to any command to target just those.

The manual equivalent, for a terminal-per-process workflow. They are separate processes on
purpose: a walk-forward is minutes of CPU-bound work and must not sit inside a request.

```bash
uv run cracktrade-api db migrate
```

```bash
uv run cracktrade-api serve
```

```bash
uv run cracktrade-api worker
```

`serve` binds to loopback and there is no authentication (spec §15.4, D-5). Both commands
refuse to start against a database with pending migrations, before the call that never
returns — a process serving requests against half a schema fails later, inside a request, and
far less clearly.

The interactive docs are at `http://127.0.0.1:8000/api/v1/docs`, and
`GET /api/v1/health` reports whether the database is reachable and its schema current:

```bash
curl -s localhost:8000/api/v1/health
```

Stop the worker with Ctrl-C: it finishes the current run's checkpoint and records it as
`cancelled`, rather than being killed and swept as abandoned a minute later. Signal twice to
exit at once.

`docs/API.md` is the endpoint reference; the normative semantics are spec §14–§15.

## Status

The engine, optimizer, validation suite, HTTP interface and persistence are complete. The web
UI (React + TypeScript + Mantine) and AI-assisted strategy generation come next. The core is a
library and the CLI is one consumer of it, so adding another interface did not mean moving any
logic — the engine stays stateless, and what the API stores is the engine's own serialized
output.
