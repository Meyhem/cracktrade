# cracktrade — Engine Specification

**Status:** normative. This document is the contract the implementation is verified against.
**Scope:** strategy configuration format, market-data contract, indicator layer, signal layer,
backtest engine, metrics, optimizer, and the causality invariant.

## 0. How to read this document

The engine is a clean-room rebuild of `swing-trader`
(`/home/meyhem/dev/swing-trader`, read-only reference, never imported). Every behaviour below is
one of:

| Marker | Meaning |
| --- | --- |
| **[PORT]** | Legacy behaviour carried over unchanged. Reference given as `file:line`. |
| **[FIX]** | Legacy behaviour was defective. Legacy behaviour, failure mode, and target behaviour are all stated. The rebuild implements the target. |
| **[NEW]** | No legacy counterpart. Introduced by this spec. |
| **[DROP]** | Legacy behaviour deliberately removed. |

Legacy references are to files under `/home/meyhem/dev/swing-trader/`.

Results produced by this engine will **not** reconcile with legacy results. That is intended: the
defect register in §10 lists fourteen behaviours that change the numbers. There is no
compatibility mode.

---

## 1. Scope and system overview

### 1.1 Subsystems

```
config ──▶ data ──▶ indicators ──▶ signals ──▶ backtest ──▶ metrics
                                                    ▲           │
                                                    └── optimize ┘
```

Five libraries plus two consumers:

| Layer | Responsibility | May import |
| --- | --- | --- |
| `domain` | value objects, result models. Pure. | — |
| `config` | strategy schema, parsing, validation | `domain` |
| `data` | OHLCV retrieval behind a protocol | `domain` |
| `indicators` | indicator registry and computation | `domain`, `config` |
| `signals` | expression parsing and evaluation, causal alignment | `domain`, `indicators` |
| `backtest` | portfolio simulation. **Sole owner of the vectorbt import.** | all above |
| `metrics` | metric computation from a simulated portfolio | `domain`, `backtest` |
| `optimize` | parameter search, train/test protocol | all above |
| `render` | result models → text/json/yaml | `domain` |
| `cli` | argument parsing, invoking core, rendering | all above |
| `api` | HTTP interface and persistence (§14, §15) | all above |

Dependencies flow strictly downward. Nothing outside `backtest/` imports vectorbt; this keeps the
engine replaceable and every layer above it unit-testable without a portfolio simulation.

The core returns **typed result objects**. Rendering is the consumer's job. This is the seam the
HTTP interface (§15) reuses without modification to core: the engine itself remains stateless,
and everything `api` stores is the engine's own serialized output.

### 1.2 Pinned dependencies

Per legacy `uv.lock`, retained:

| Package | Version | Note |
| --- | --- | --- |
| Python | ≥ 3.12 | |
| `vectorbt` | 1.0.0 | Open-source only. `import vectorbtpro` is prohibited (legacy `.agents/AGENTS.md` §3). |
| `pandas` | 2.3.3 | |
| `numpy` | 2.2.6 | |
| `scipy` | 1.18.0 | `differential_evolution`, `qmc.LatinHypercube` |
| `pandas-ta` | 0.4.71b0 | indicator implementations |
| `yfinance` | ≥ 0.2.40 | default data provider |
| `pydantic` | ≥ 2.7 | schema |

**[NEW — decided 2026-08-16.]** The `api` layer (§15) adds `fastapi`, `uvicorn`,
`psycopg[binary,pool]` (raw SQL, no ORM — §15.4 D-6), and `sse-starlette`, all `>=` pinned:
none of them shapes a result the way the engine pins above do. Any api-layer library behaviour
a result depends on gets an exact pin plus a pinning test at the moment it is first relied on,
same rule as everywhere.

**[NEW — decided 2026-08-18.] Deprecation warnings fail the suite.** Pinning tests catch a
library that has *already* changed under us. A deprecation warning is the same library saying
it is *about to*, and it was being written to a summary at the end of a run that nobody reads —
which is indistinguishable from not knowing. `filterwarnings = ["error", ...]` in
`pyproject.toml` makes that fatal instead, so the notice lands while the old behaviour still
works and there is time to act on it, rather than as a broken backtest after an upgrade.

Exemptions are a named allowlist, never a category-wide mute: one entry per warning, matched on
its message, carrying the reason it cannot be fixed here and what removes it. A warning raised
inside a dependency at import time is the only thing that qualifies — anything reachable from
our own code gets fixed rather than listed. Note when writing an entry that a warning's *class*
need not be `DeprecationWarning`: `StarletteDeprecationWarning` subclasses `UserWarning`, so a
filter aimed at the obvious category silently matches nothing.

### 1.3 Dropped

**[DROP]** Google Drive storage, Zulip bot and all chat handlers, the watchdog subsystem, Plotly
charting, result persistence of any kind, multi-ticker support, shared-cash portfolios.

**[AMENDED 2026-08-16.]** Result persistence returns — but as an *interface-layer* concern
(§14), not an engine one. The engine stays stateless and side-effect-free; the `api` layer
stores the engine's serialized results verbatim. Nothing else in this drop list comes back.

**[DROP]** Multi-ticker. Legacy accepted `universe.tickers: List[str]` and ran N independent
single-asset portfolios, each seeded with the full `initial_capital`
(`src/execution/portfolio.py:55`), summing "portfolio-level" numbers across them. This engine
accepts exactly one ticker, which removes the ambiguity rather than papering over it. The per-ticker
loop in `apply_ta` (`src/execution/indicators.py:19-40`), the `[None] + tickers` optimizer
orchestration (`src/cli/main.py:108`), and ticker-scoped fitness
(`src/optimization/optimizer.py:87-100`) all disappear.

---

## 2. Causality — the primary invariant

> **Every value used to make a decision at bar *t* must be computable from bars ≤ *t*, and every
> fill must occur strictly after the bar that produced the signal.**

This outranks every other requirement in this document. Where causality and fidelity to legacy
behaviour conflict, causality wins. Where causality and performance conflict, causality wins.

Legacy stated the rule as prose guidance (`.agents/AGENTS.md` §3 "Shift Direction Rule", §4 "Next
Open Rule") and relied on developers following it. **[NEW]** This engine enforces it structurally in
four independent layers, so that no single mistake defeats it.

### 2.1 Layer 1 — the strategy language cannot express look-ahead

**[FIX]** Legacy evaluated signals with `pd.eval(expr, local_dict=...)`
(`src/execution/signals.py:16, 26`). `pd.eval` permits attribute access and method calls in its
`python` engine, so `close.shift(-1) > close` is an expressible strategy. Prose in the LLM prompt
forbade it (`src/bot/generator.py:264-265`) but nothing enforced it.

**Target:** signal expressions are parsed with `ast.parse(expr, mode="eval")` and walked against a
node whitelist. Permitted node types, exhaustively:

| Category | Nodes |
| --- | --- |
| Structure | `Expression` |
| Names | `Name` (load context only) |
| Literals | `Constant` restricted to `int`, `float`, `bool` |
| Comparison | `Compare` with `Lt`, `LtE`, `Gt`, `GtE`, `Eq`, `NotEq` |
| Boolean | `BinOp` with `BitAnd`, `BitOr`, `BitXor`; `UnaryOp` with `Invert` |
| Arithmetic | `BinOp` with `Add`, `Sub`, `Mult`, `Div`, `Pow`, `Mod`; `UnaryOp` with `USub`, `UAdd` |

Every other node type is rejected with a pointed error. In particular `Call`, `Attribute`,
`Subscript`, `Slice`, `Lambda`, `IfExp`, `Comprehension`, `Starred`, `Await`, `NamedExpr`, and
`BoolOp` (`and`/`or`, which do not vectorise) are rejected.

Consequence: `.shift(-1)`, `.iloc[t+1]`, `.rolling(center=True)`, `.values`, `max(...)`,
`__class__`, and every other escape hatch are not merely discouraged — they do not parse. A
look-ahead strategy is not a strategy that fails validation; it is a string that is not a strategy.

Rejecting `Call` also removes the arbitrary-code-execution surface that `pd.eval` carries.

Evaluation is performed by a recursive walk over the validated AST against a namespace of pandas
Series, not by `eval`/`pd.eval`. No Python `eval` is called anywhere in the signal path.

**Note on `BoolOp`:** legacy prose said Python `and`/`or` "also work"
(`src/bot/generator.py:250`). They do not — on Series they raise or silently coerce. They are
rejected; `&` and `|` are the only conjunctions.

### 2.2 Layer 2 — indicators are causal by construction

Each registry entry declares:

- `warmup: int` — the number of leading bars whose output is not yet defined, derived from the
  indicator's parameters.
- `causality: CAUSAL | FORWARD_PROJECTED | NON_CAUSAL`

Rules:

- `center=True` is never passed to any rolling computation. **[NEW]**
- `NON_CAUSAL` indicators — anything normalising over the full sample (global min/max scaling,
  whole-series z-score) — are not registrable. Only rolling statistics are permitted.
- `FORWARD_PROJECTED` outputs are handled explicitly, never inherited. Ichimoku's `senkou_a` /
  `senkou_b` spans are plotted 26 bars into the future; at index *t* they hold values derived from
  bars ≤ *t* but *labelled* as *t*+26. **Target:** forward-projected output columns are **dropped**
  from the namespace and referencing them is a validation error naming the reason. Realignment is
  not attempted, because the realigned series duplicates `tenkan`/`kijun` information and the
  silent-wrong-answer risk exceeds the value. **[FIX]** — legacy exposed these columns unlabelled
  via the generic fan-out at `src/execution/indicators.py:107-109`.
- Warm-up region is `NaN`, never zero. See §10 D1.

### 2.3 Layer 3 — one alignment path

**[NEW]** All temporal alignment goes through a single helper:

```python
def causal_shift(obj: SeriesOrFrame, periods: int) -> SeriesOrFrame:
    if periods < 0:
        raise CausalityViolation(...)
    return obj.shift(periods)
```

`.shift(` appears nowhere else in the codebase. A repository lint rule (ruff custom rule or a
`grep`-based test) fails the build on `.shift(-`, `shift(periods=-`, and `center=True` outside the
helper and its tests.

Everything time-dependent routes through it: entry signals, exit signals, time-based exits, and
per-bar risk series such as the ATR stop. Legacy applied `.shift(1)` ad hoc in three places
(`src/execution/signals.py:21`, `src/execution/signals.py:31`,
`src/execution/portfolio.py:25`) and `.shift(n)` in two more
(`src/execution/backtester.py:36, 46`); each was an independent opportunity for the sign to be
wrong.

### 2.4 Layer 4 — truncation-equivalence harness

**[NEW]** The empirical net. It catches leakage regardless of which layer introduced it, including
leakage introduced by a future pandas_ta version.

For a strategy *S*, full history *H* of length *N*, and a sample of indices *t*:

1. Compute the full pipeline on *H* → indicator frame `F_full`, signal frames, stop series.
2. Compute the same pipeline on `H[:t+1]` → `F_trunc`.
3. Assert that every value at index *t* is **exactly equal** (or both NaN) between `F_full` and
   `F_trunc`, for every indicator column, every entry/exit signal, and every per-bar risk series.

Any divergence is look-ahead by definition: a value at *t* that changes when future bars are added
was computed from those future bars.

Coverage requirements:

- every indicator in the registry, at its default and at two randomised parameterisations;
- property-based random strategy generation (Hypothesis) over the signal grammar, so coverage is not
  limited to hand-written cases;
- at least 20 sampled indices per case, including indices inside the warm-up region.

Exact float equality is required, not `isclose`. A causal computation on a prefix produces bitwise
identical results; approximate equality would mask genuine leakage in low-order bits.

This harness is a permanent CI gate. It is introduced in Phase 5 and extended in Phases 4, 6 and 8.

### 2.5 Optimizer leakage is prevented by types

**[FIX]** See §10 D2. The fitness function's signature accepts only a `TrainWindow`. The test window
is a distinct type, `TestWindow`, constructible only by the splitter and consumed only by the
reporting path. Passing test data to fitness is a type error caught by mypy — not a convention a
developer can forget.

The test window is evaluated **exactly once**, after the parameter search is final. Since §7.1 the
search is the *only* step that chooses anything, so there is nothing else that could leak.

### 2.6 Pessimistic conventions

Daily OHLC bars do not record intrabar sequence. Any assumption about ordering within a bar that
favours the strategy is look-ahead wearing a different hat.

| Situation | Rule |
| --- | --- |
| Stop-loss and take-profit both touched within one bar | **Stop-loss fires.** **[NEW]** |
| Price gaps through a stop level at the open | Fill at the **actual open**, not the stop price. **[NEW]** |
| Signal generated on bar *D* | Fill at `Open[D+1]`. Never `Close[D]`, never `Open[D]`. **[PORT]** — `.agents/AGENTS.md` §4 |
| Current day's bar is incomplete | **Dropped.** A run at 14:00 and a run at 22:00 see identical history. **[NEW]** |
| Indicator not yet defined at bar *t* (warm-up) | No signal may be true at *t*. **[FIX]**, see §10 D1 |

The exact vectorbt parameters achieving the first two (`stop_exit_price` and the SL/TP evaluation
order in `Portfolio.from_signals`) are verified empirically against the installed vectorbt 1.0.0
during Phase 6 and recorded in §6.5. They are asserted by a test, not assumed from documentation.

### 2.7 Residual biases — outside engine control

Documented, not fixed. They are properties of the data and of the user, not of the engine.

- **Ticker selection is hindsight.** The user chooses a ticker knowing its history. No engine can
  correct for this.
- **Retroactive adjustment.** yfinance applies split and dividend adjustments across the entire
  series, so historical prices reflect corporate actions that had not yet occurred at that date.
  Bar-to-bar *returns* are unaffected; absolute price levels and any absolute price thresholds in a
  signal are.
- **Index membership and delisting** are not modelled. A ticker that exists today is assumed to have
  existed throughout.

---

## 3. Strategy configuration format

YAML file, seven top-level sections. Parsed into Pydantic v2 models.

### 3.1 Global schema rules

**[FIX]** Legacy `ConfigModel` set `strict=True` (`src/core/config.py:95`) but did **not** forbid
extra top-level keys, which is why a `watchdog:` block survived parsing and was silently ignored
(`src/cli/main.py:40`). Meanwhile `ExecutionModel` silently discarded `entry_price` and
`exit_price`, which appear in legacy tests (`tests/test_execution.py:35-36`,
`tests/test_optimizer.py:10`) but do not exist on the model.

**Target:** `extra='forbid'` on **every** model including the root. An unrecognised key is an error
naming the key and listing the accepted keys at that level. There is no escape hatch. A config that
sets `entry_price: "next_close"` and is silently given next-open execution is worse than a config
that refuses to load.

`strict=True` is retained: `initial_capital: "10000"` is an error, not a coercion.

### 3.2 `strategy` — required

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `name` | `str` | — | Non-empty. Recommended snake_case. Used in output only. |

**[PORT]** `src/core/config.py:87-88`.

**[DROP]** Legacy detected duplicate strategy names in the storage layer rather than in validation.
With no persistence there is no name registry and nothing to collide with.

**[AMENDED 2026-08-16.]** Persistence exists again (§14), and with it a name registry: stored
strategies have globally unique, case-sensitive names (§14.3), enforced by the database, because
the UI addresses strategies by name in breadcrumbs and promotion records. Config validation
itself is unchanged — a YAML file's `strategy.name` collides with nothing until it is stored,
so uniqueness is checked at the persistence boundary, not here.

### 3.3 `universe` — required

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `ticker` | `str` | — | **[FIX]** Exactly one. Non-empty after strip. Legacy: `tickers: List[str]` (`src/core/config.py:33`). |
| `start_date` | `datetime.date` | — | **[FIX]** Legacy typed dates as `str` and compared them lexicographically (`src/core/config.py:34, 39`), which happens to work for ISO-8601 and fails for anything else. |
| `end_date` | `datetime.date` | today (UTC) | **[PORT]** default_factory, `src/core/config.py:35`. |
| `interval` | `Interval` | `1d` | **[NEW]** One of `15m`, `30m`, `1h`, `1d`. The width of one bar. |

Validation: `start_date < end_date`; the span must cover at least `max(indicator warmup) + 30`
trading days, estimated at 252 trading days per calendar year. **[NEW]** — legacy accepted a
two-week backtest of a 200-day moving average and reported it as a result.

`end_date` is **inclusive** of the named day. See §4.2.

#### 3.3.1 `interval` — bar width

The default is `1d`, so every strategy written before intraday support existed keeps its exact
meaning by omitting the field. `Interval` is the single place the four spellings of a bar width
are related: the YAML token, the pandas offset alias, the bar's duration, and how far back the
provider serves it.

| Token | pandas alias | Bar duration | Provider reach |
| --- | --- | --- | --- |
| `15m` | `15min` | 15 minutes | 55 days |
| `30m` | `30min` | 30 minutes | 55 days |
| `1h` | `1h` | 1 hour | 700 days |
| `1d` | `1D` | 1 day | unlimited |

The pandas alias is **not** the YAML token: pandas has no `30m` alias (`m` means month-end) and
deprecated `T`/`H` in 2.x. Getting it wrong does not raise — it silently changes what every
time-based metric is annualised against.

The provider reach figures carry a safety margin below Yahoo's actual limits (60 days for 15m
and 30m, 730 for 1h), because the cutoff moves during the day and a strategy that validated at
09:00 must not become invalid at 17:00. `30m` is *resampled from 15m* by the provider, which is
why it inherits the shorter window rather than getting one of its own. All of this was measured,
not read from documentation; see `tests/fixtures/README.md`.

**Range validation is split in two, deliberately.** The *width* of the range is checked at parse
time and a range wider than the interval's reach is refused. How far in the *past* the range
sits is **not** checked at parse time: the provider's window slides forward every day, so a
strategy that parsed yesterday would fail to parse today, and since the API re-parses stored
YAML on every read, a saved 30m strategy would become unreadable 55 days after it was written —
taking the detail view of every run it ever produced with it. An out-of-reach range instead
produces a `ConfigWarning` on `universe.start_date` and, if run anyway, a loud refusal from the
data layer naming the limit (§4.2). Nothing produces numbers from data that was never fetched.

Intraday strategies carry additional semantics — forced session-close exits, measured
annualisation, exchange-local timestamps — recorded in §4.1, §7.5 and §7.6.

### 3.4 `execution` — required

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `initial_capital` | `float` | — | `> 0` |
| `slippage_pct` | `float` | — | `>= 0`. Percent, e.g. `0.1` = 0.1%. |
| `commission_pct` | `float` | — | `>= 0`. Percent. |
| `risk_free_rate` | `float` | `0.04` | **Annual** rate as a fraction. `0.04` = 4%/year. |

**[PORT]** `src/core/config.py:45-71`. Percent → fraction conversion is `value / 100.0`
(`src/core/config.py:67, 71`).

**[FIX]** `risk_free_rate` was parsed and then ignored; Sharpe and Sortino hardcoded `0.04`
(`src/metrics.py:21, 31`). See §10 D3 and D4.

**[DROP]** `entry_price`, `exit_price` — never existed on the model; now rejected explicitly rather
than swallowed. Execution venue is next-open, always, and is not configurable.

### 3.5 `indicators` — optional, defaults to `[]`

A **list** of indicator specifications.

**[FIX]** Legacy `tests/test_optimizer.py:11-14` passes `indicators` as a **dict** keyed by name and
never validates it, because `OptimizationEngine` traverses the raw dict without constructing a
`ConfigModel`. The list shape from `src/core/config.py:100` is normative here; the dict shape is
rejected, and the optimizer validates before traversing (§8.1).

Base fields on every entry:

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `name` | `str` | — | Unique across the list. Must be a valid Python identifier — it becomes a name in the signal namespace. Must not shadow `open`, `high`, `low`, `close`, `volume`. |
| `type` | `str` | — | Must be present in the registry (§5). |
| `source` | `str` | `"close"` | One of `open`, `high`, `low`, `close`, `volume`. |

**[FIX] Type-specific parameters.** Legacy `IndicatorModel` used `extra='forbid'` with a **fixed
21-field allowlist** shared by every indicator type (`src/core/config.py:8-30`):

```
name, type, source, window, fast, slow, signal, std, k, d, smooth_k,
multiplier, lower_length, upper_length, af0, af, tenkan, kijun, senkou,
bb_length, kc_length
```

Failure modes: adding one indicator requiring a new parameter meant editing a core model; every
indicator accepted every other indicator's parameters, so `{type: sma, window: 20, tenkan: 9}`
validated cleanly and silently dropped `tenkan`; and `model_dump(exclude_none=True)`
(`src/execution/indicators.py:98`) forwarded whatever was set straight into the pandas_ta call,
turning a typo into a `TypeError` from library internals.

**Target:** each registry entry declares its own parameter schema. Validation is two-stage — the
base fields are parsed, `type` is resolved against the registry, then the remaining keys are
validated against that indicator's schema. Unknown parameters are rejected naming the indicator and
listing its accepted parameters. Adding an indicator means adding a registry entry, not editing a
shared model.

`window` remains the user-facing name for the primary lookback across all types, for continuity with
existing strategy files. **[PORT]** — remapped to `length` at the pandas_ta boundary,
`src/execution/indicators.py:92-96`.

### 3.6 `entry` — required

A single mapping, not a list.

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `signal` | `str` | — | Must parse under the §2.1 grammar; every `Name` must resolve in the namespace. |

**[PORT]** `src/core/config.py:73-75, 105-110`.

**[NEW]** One entry rule per strategy. Legacy took a list of named `entry_variants` and crossed
them with the exit variants; §7.1 records why that was removed. A strategy that used to declare
several entries becomes several strategies.

**[NEW]** Signal expressions are validated at **config-parse time**, not at first evaluation.
Undefined names, forbidden syntax, and type errors are reported before any data is downloaded.

### 3.7 `exit` — required

A single mapping, not a list.

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `signal` | `str \| None` | `None` | Same grammar as the entry. |
| `stop_loss_pct` | `float \| None` | `None` | `> 0`. Percent from entry price. |
| `trailing_stop_pct` | `float \| None` | `None` | `> 0`. Percent from peak since entry. |
| `atr_stop_multiplier` | `float \| None` | `None` | `> 0`. Multiple of ATR(14). |
| `take_profit_pct` | `float \| None` | `None` | `> 0`. Percent from entry price. |
| `min_holding_days` | `int \| None` | `None` | `>= 1`. Trading days. Daily strategies only. |
| `max_holding_days` | `int \| None` | `None` | `>= 1`. Trading days. Daily strategies only. |
| `min_holding_bars` | `int \| None` | `None` | **[NEW]** `>= 1`. Bars. Any interval. |
| `max_holding_bars` | `int \| None` | `None` | **[NEW]** `>= 1`. Bars. Any interval. |

**[PORT]** `src/core/config.py:77-85`.

**Holding periods have always been counted in bars.** On daily data a bar is a trading day, so
`min_holding_days` was an accurate name by coincidence; it stops being accurate the moment a bar
is 30 minutes long. The `_bars` fields are therefore the general spelling and work at every
interval, while the `_days` fields are accepted **only when `universe.interval` is `1d`**, where
the two words denote the same quantity.

Reinterpreting `max_holding_days: 5` on 30m bars is refused rather than guessed at: reading it
as five bars would turn a week-long limit into two and a half hours, and reading it as five
*sessions* would invent a number the user never wrote.

Validation **[NEW]**: at least one exit mechanism must be set — an empty `exit` never exits and
produces a single open position. The minimum holding period must be less than the maximum when
both are set. Declaring the same bound in both spellings (`max_holding_days` *and*
`max_holding_bars`) is an error; declaring different bounds in different spellings is not.

**Stop-loss priority chain [PORT]** (`src/execution/portfolio.py:19-32`): exactly one stop type is
active, in order

1. `atr_stop_multiplier`
2. `trailing_stop_pct`
3. `stop_loss_pct`

`take_profit_pct` is orthogonal and combines with whichever stop is active.

**[NEW]** Setting more than one stop type is a **validation warning** naming which one wins. Legacy
silently discarded the others.

### 3.8 `position_sizing` — optional

| Field | Type | Rule |
| --- | --- | --- |
| `type` | `str` | One of `fixed_pct`, `fixed_cash`, `fixed_shares`. **[FIX]** Legacy took any `str` (`src/core/config.py:91`) and silently fell through to the `inf`/`amount` default for an unrecognised value (`src/execution/portfolio.py:37-48`). |
| `value` | `float` | `> 0`. For `fixed_pct`, additionally `<= 100`. |

Omitted → 100% of available cash per trade.

**Semantics, reconciled [FIX].** Legacy user documentation described `fixed_pct` as "Percent of your
total account to invest per trade" (`src/bot/handlers/explain.py:150`) and "% of current equity"
(`src/bot/generator.py:240`). The implementation maps it to vectorbt's `percent` sizing
(`src/execution/portfolio.py:41-42`), which is a percentage of **available cash**, not of total
equity. With a single position and no accumulation the two coincide only when flat. The
documentation was wrong; the mapping is retained and this spec states the true meaning:

| `type` | vectorbt `size_type` | `size` | Meaning |
| --- | --- | --- | --- |
| `fixed_pct` | `percent` | `value / 100` | Fraction of **available cash** at order time |
| `fixed_cash` | `value` | `value` | Absolute cash amount per trade |
| `fixed_shares` | `amount` | `value` | Absolute share count per trade |
| *(omitted)* | `amount` | `inf` | All available cash |

**[PORT]** `src/execution/portfolio.py:37-48`.

### 3.9 Validation error contract

**[PORT]** `src/core/config.py:112-130`. Errors are aggregated, not raised on first failure. Each is
rendered as `- <dotted.path>: <message>`; a missing field renders as "This field is required but
missing from the config."; Pydantic's `"Value error, "` prefix is stripped.

**[NEW]** Additionally: the offending YAML line number where recoverable, and a `did you mean`
suggestion for unknown keys within edit distance 2 of a valid key.

**[AMENDED — 2026-08-17.]** "Where recoverable" means: whenever the caller had the YAML text and
passed it. `build_strategy` takes an optional `source` for exactly this, and
`POST /config/validate` passes it when the request supplied `yaml`. Before this, that endpoint
parsed the YAML and then threw the text away, so the line number promised here was reachable from
the CLI and from nowhere else — an editor could mark a syntax error's line and no other.

On the wire the path and the line are **separate fields, never merged**. The engine renders a
located error as `<path> (line N): <message>`, and an interface that received only that string
would have to parse the line back out to know which field to attach the message to — meaning an
error *with* a line would lose the field attribution an error *without* one keeps, which is
backwards. Semantic errors (an unknown indicator type, a signal that does not resolve) carry a
path and no line: they are judgements about the strategy, not about one key in the file.

### 3.10 Worked example

Adapted from `src/bot/generator.py:282-319` — single-ticker, `window` on `rolling_max` sourced from
`close` as in the original.

```yaml
strategy:
  name: momentum_breakout_v2

universe:
  ticker: MSFT
  start_date: "2015-01-01"
  end_date: "2025-12-31"

execution:
  initial_capital: 10000.0
  slippage_pct: 0.1
  commission_pct: 0.05
  risk_free_rate: 0.04

indicators:
  - name: sma_long
    type: sma
    source: close
    window: 200
  - name: max_high
    type: rolling_max
    source: close
    window: 252
  - name: vol_ma
    type: sma
    source: volume
    window: 20

entry:
  signal: "(close > sma_long) & (close >= max_high) & (volume > vol_ma * 1.5)"

exit:
  signal: "close < sma_long"
  trailing_stop_pct: 5.0
  max_holding_days: 20

position_sizing:
  type: fixed_pct
  value: 100.0
```

Second example, adapted from `src/bot/generator.py:325-365`:

```yaml
strategy:
  name: silicon_momentum_pullback_v1

universe:
  ticker: NVDA
  start_date: "2023-01-01"
  end_date: "2025-12-31"

execution:
  initial_capital: 10000.0
  slippage_pct: 0.1
  commission_pct: 0.05

indicators:
  - name: sma_long
    type: sma
    window: 200
  - name: sma_short
    type: sma
    window: 20
  - name: rsi_ind
    type: rsi
    window: 14

entry:
  signal: "(close > sma_long) & (rsi_ind < 45)"

exit:
  signal: "close < sma_short"
  trailing_stop_pct: 7.5
  take_profit_pct: 15.0
  max_holding_days: 20

position_sizing:
  type: fixed_pct
  value: 100.0
```

---

## 4. Market data contract

### 4.1 The frame

The engine consumes exactly one shape. Providers adapt to it; the engine never adapts to a provider.

| Property | Requirement |
| --- | --- |
| Type | `pandas.DataFrame` |
| Index | `pandas.DatetimeIndex`, timezone-naive, strictly increasing, no duplicates, no NaT |
| Columns | exactly `Open`, `High`, `Low`, `Close`, `Volume`, in that order, flat (no MultiIndex) |
| dtype | `float64` for all five |
| Content | no NaN after preparation; `Low <= Open, Close <= High`; `Volume >= 0` |

`MarketData` additionally carries the `interval` its bars were fetched at and, for intraday
data, the `timezone` its timestamps are expressed in. Both propagate through `slice` and
`head`: the truncation harness and the walk-forward splitter go through those, and a window
that forgot its bar width would be annualised as daily — in the very code path meant to catch
that class of error.

**[PORT]** DatetimeIndex preservation is load-bearing — vectorbt derives annualisation from it.
`.reset_index()` is prohibited (`.agents/AGENTS.md` §3).

#### What a naive timestamp *means* **[NEW]**

The index is timezone-naive at every interval, but it does not denote the same thing at each:

- **Daily** — UTC, truncated to midnight. Unchanged.
- **Intraday** — **exchange-local wall-clock time**, with the IANA zone carried alongside in
  `MarketData.timezone`. A Xetra bar stamped `09:00` means 09:00 in Frankfurt.

This is not a convenience. The EU switches to summer time on the last Sunday of March and back
on the last Sunday of October; the US switches on the second Sunday of March and the first
Sunday of November. On a UTC index the same unchanging Xetra session would sit at 07:00–15:30
for part of the year and 08:00–16:30 for the rest, so "the session's last bar" — the quantity
the forced-close rule in §7.6 keys off — would move twice a year, and differently from a US
listing in the same database. Every downstream consumer wants wall-clock exchange time:
session grouping, the forced close, chart axes, and eventually a notification telling a trader
in the EU what to do at a particular local time.

Wall-clock local time stays unique and monotonic for equity sessions because the DST fall-back
hour (02:00–03:00) lies outside every exchange's trading day. That assumption is *enforced*, not
trusted: the uniqueness and monotonicity checks above are what fail if it is ever violated.
Instruments trading around the clock are out of scope for this pass (§3.3.1).

Yahoo's EU quotes are delayed roughly 15 minutes. Irrelevant to a backtest of closed bars;
recorded because live evaluation would inherit it.

**[FIX]** `float64`, not `float32`. Legacy cast to `float32`
(`src/data/market.py:38`) as a memory guardrail for multi-ticker universes. With one ticker the
saving is irrelevant, while `float32` on a compounding equity curve accumulates visible error and —
worse — makes the §2.4 exact-equality assertions flaky for reasons unrelated to causality.

The contract is validated on entry to the engine by an explicit check, not assumed. **[NEW]**

### 4.2 Retrieval

`MarketDataProvider` is a `typing.Protocol`:

```python
class MarketDataProvider(Protocol):
    def fetch(
        self, ticker: str, start: date, end: date, interval: Interval = Interval.D1
    ) -> pd.DataFrame: ...
```

`YFinanceProvider` implements it.

- **End-date inclusivity [PORT].** yfinance's `end` is exclusive; one day is added so the
  user's `end_date` is included (`src/data/market.py:9-11`).
- **`prepost=False` is pinned [NEW]**, like `auto_adjust`. Regular session only. This is also
  what the trader can actually execute: XTB trades EU stocks and ETFs during exchange hours, so
  a backtest filling on a pre-market print would model a trade nobody could place.
- **Interval limits [NEW].** yfinance does **not raise** when a range exceeds what it will
  serve — it prints the reason to stdout and returns an empty frame. Emptiness is therefore the
  only signal reaching the engine, and a bare "no data returned" would leave the user choosing
  between a wrong ticker, a holiday range, and a limit they have never heard of. The message
  names the interval's reach and the earliest fetchable date. See §3.3.1 for why this is a
  backstop rather than the primary check.
- **Column normalisation [PORT].** Labels are capitalised and the five needed columns selected
  (`src/data/market.py:30-32`). The MultiIndex branch (`src/data/market.py:22-28`) is **[DROP]**ped
  — single ticker only. If yfinance returns a MultiIndex anyway (it does for some call shapes), it is
  flattened by selecting the single ticker level, and an unexpected shape is an error rather than a
  silent reshape.
- **Empty result [PORT]** raises (`src/data/market.py:15-16`), with the ticker and date range in the
  message.

### 4.3 Gap handling

**[FIX]** Legacy applied `.ffill().bfill()` (`src/data/market.py:35`). See §10 D5.

**Target:**

1. `ffill()` only — a stale price carried forward is a defensible model of a non-trading day.
2. `bfill()` is **never** applied. Backward-filling propagates a *later* price to an *earlier* bar,
   which is look-ahead in the data layer, and for a late-IPO ticker it fabricates an entire
   pre-listing history out of the first real close.
3. Leading NaN rows — before the ticker's first real bar — are **dropped**, and the effective start
   date is reported in the result.
4. If the surviving history is shorter than `max(indicator warmup) + 30` bars, raise. A backtest that
   cannot warm up its indicators has no valid region.
5. Any NaN remaining after step 1-3 is an error, not a fill.
6. **[NEW]** yfinance's `auto_adjust` occasionally leaves High/Low a few ulps on the wrong side of
   Open/Close on the same bar — adjustment arithmetic noise, not a bad print (§10 D16). Repaired by
   snapping the offending value onto the bracket only when the discrepancy is within `1e-8` relative
   tolerance, orders of magnitude tighter than any plausible real error; a bar outside that
   tolerance is left alone and still fails the §4.1 contract.
7. **[NEW] Intraday forward-filling never crosses a session boundary.** The fill is grouped by
   local calendar date. A global `ffill` on 30-minute bars would repair a missing 09:00 print by
   copying the previous day's 17:00 row onto it: a fabricated bar that swallows the whole
   overnight gap, reports a zero return across it, and offers a stop-loss a high/low range from
   a different day to fire against. A hole at a session's *open* has no earlier bar in its own
   session to copy, so the row is **dropped**, not filled — dropping a bar loses information,
   inventing one manufactures a price a stop can fire against on a morning the market never
   printed it. The drop is logged with a count.

   In practice yfinance omits untraded intraday bars rather than emitting NaN rows, so this
   path is rarely reached. It is specified and implemented anyway: "rarely" is not "never", and
   the failure it would otherwise produce is silent.

The history is **not** reindexed onto a synthetic complete-session grid. Halts and thin
half-hours are real, and the spacing check (§7.5) uses the median precisely so that gaps do not
break it.

### 4.4 Incomplete bars

**[NEW]** A bar that has not finished forming is dropped. Yahoo does return the still-forming
bar, so this is necessary rather than theoretical.

- **Daily** — the last row is dropped if its date has reached today in UTC. Unchanged.
- **Intraday** — a bar is kept only once `timestamp + interval` has passed **in the exchange's
  timezone**. A 09:00 bar on a 30-minute Xetra strategy is complete at 09:30 Frankfurt time and
  not before.

The determinism guarantee therefore shifts from "the same history per UTC day" to "the same
history per closed bar". Both are determinism: two runs in the same half hour see the same
bars. Reading the clock in UTC instead would declare a Frankfurt bar closed two hours early.

### 4.5 Caching

**[NEW]** Optional, off by default, enabled by `--cache`. Parquet keyed by
`(provider, ticker, interval, start, end)`. Purely a redownload optimisation; it holds no results
and is not persistence in the sense excluded from this pass.

The interval joins the key so that a 30-minute request cannot be served the daily frame stored
under the same ticker and range. Adding it changed every digest and orphaned entries written
before it — acceptable for an input cache, whose worst case is one extra download.

**Known limitation, made visible by intraday.** The key holds the requested range, not the
moment of the request, so a range ending today freezes whatever had printed when it was first
fetched; later runs the same day reuse it. Daily has always behaved this way and it was nearly
invisible, since the current day's bar is dropped as incomplete anyway. On 30-minute bars an
afternoon run can reproduce the morning's history. Left as it is deliberately — a cache
invalidating on a wall clock would stop being reproducible, which is worse. The mitigations are
elsewhere: the cache is off unless asked for, and the UI defaults an intraday range to end
*yesterday* (§13), so the common path never touches a live session.

### 4.6 Sessions **[NEW]**

`cracktrade.data.sessions` groups an intraday index into trading sessions. Every function there
is a pure function of the index — no wall clock, no network, no exchange calendar, no state.
That is the causality constraint, not tidiness: these feed the forced-close rule of §7.6, and
anything they returned that was not derivable from bars already seen would be look-ahead.

A **session** is one local calendar date. This works because the index carries exchange-local
time (§4.1) and no equity session in scope crosses midnight; it would not work on a UTC index.

Session length is **measured, never assumed**. Two years of Xetra hourly bars contain nine-bar
days, an eight-bar early close, a seven-bar late open, and five-bar sessions either side of
Christmas, while the LSE runs four-bar half-days on dates Xetra is closed outright. A 30-minute
Xetra session is 17 bars; a 30-minute New York session is 13. Any bars-per-day constant is wrong
for some venue on some date.

`median_bars_per_session` is the figure annualisation is built on (§7.5). It is a median over
*completed* sessions: half-days and early closes are real and all shorter than normal, so a mean
would let a handful of them drag the figure down, and counting the trailing partial session would
do so on every single run.

**Data-quality caveat, measured not assumed:** Yahoo reports `Volume == 0` on 415 of 482 Xetra
opening bars while quoting prices normally. Volume-based indicators are unreliable on the EU
opening bar. This is recorded rather than repaired — zero is a legal volume under §4.1, and
substituting a plausible number would be exactly the authoritative-looking fiction this engine
exists to avoid.

---

## 5. Indicator layer

### 5.1 Namespace

Computation produces a namespace consumed by the signal layer:

| Key | Value |
| --- | --- |
| `open`, `high`, `low`, `close`, `volume` | the raw OHLCV Series |
| one key per single-output indicator | its `name` |
| one key per output of a multi-output indicator | `{name}_{suffix}` |

**[PORT]** `src/execution/indicators.py:56-62`.

**[FIX]** Legacy fed `df_filled = df.ffill()` to indicators while the namespace held the *raw*
series (`src/execution/indicators.py:63, 67`), so `close` in a signal and the `close` an indicator
saw could differ. With §4.3 forbidding residual NaN, the two are identical and the second frame is
removed. One frame, no divergence.

### 5.2 Registry

**[NEW]** Replaces `getattr(ta, type)` dispatch with signature introspection
(`src/execution/indicators.py:76-96`). Each entry declares:

```python
@dataclass(frozen=True)
class IndicatorSpec:
    type: str                       # YAML `type`
    params: type[BaseModel]         # per-indicator parameter schema
    outputs: tuple[str, ...]        # () for single-output
    warmup: Callable[[BaseModel], int]
    causality: Causality
    compute: Callable[..., SeriesOrFrame]
    inputs: frozenset[str]          # which of high/low/close/volume are required
```

`inputs` replaces `inspect.signature` introspection (`src/execution/indicators.py:83-90`), which was
correct in practice but silently coupled to pandas_ta's internal parameter names. Declaring inputs
makes the coupling explicit and testable.

**[PORT]** `source` selects the primary series for single-input indicators; multi-input indicators
(ATR, Stochastic, ADX, PSAR, …) receive `high`/`low`/`close`/`volume` regardless of `source`
(`src/bot/generator.py:86-90`). **[NEW]** setting `source` on a multi-input indicator is a warning,
since it has no effect.

### 5.3 Builtins

**[PORT]** `rolling_max` and `rolling_min` are engine-native, not pandas_ta
(`src/execution/indicators.py:69-74`). Parameters: `window`. Warm-up: `window - 1`.

### 5.4 Multi-output fan-out

**[PORT]** `src/execution/indicators.py:46-49, 107-109`. A pandas_ta call returning a DataFrame is
split into one namespace key per column, named `{name}_{first_token_of_column_lowercased}` — the
column label up to the first underscore. Examples:

| YAML | Namespace keys |
| --- | --- |
| `{name: my_bb, type: bbands, window: 20, std: 2.0}` | `my_bb_bbl`, `my_bb_bbm`, `my_bb_bbu`, `my_bb_bbb`, `my_bb_bbp` |
| `{name: my_macd, type: macd, fast: 12, slow: 26, signal: 9}` | `my_macd_macd`, `my_macd_macdh`, `my_macd_macds` |
| `{name: my_stoch, type: stoch, k: 14, d: 3, smooth_k: 3}` | `my_stoch_stochk`, `my_stoch_stochd` |

**[FIX]** Legacy derived these names at runtime from whatever pandas_ta happened to return, so a
library upgrade renaming a column silently renamed a namespace key and broke every signal referencing
it — at evaluation time, as an undefined-name error. Target: `outputs` is declared in the registry
and **asserted** against the actual returned columns. A mismatch is an engine error at startup
naming the indicator and both column sets, not a user-facing signal error.

### 5.5 Warm-up

**[FIX]** See §10 D1. The warm-up region is `NaN`. Each indicator's `warmup` is declared; the
strategy's warm-up is the maximum across all indicators actually referenced. No signal may be true
before `max(warmup)` — enforced in the signal layer (§6.3), not by filling.

### 5.6 Parameter passing — declared names are asserted against the library

**[NEW — decided 2026-08-18, see §10 D17.]** A user-facing parameter name is translated to the
library's spelling by `_PARAM_TO_TA` (global; currently `window` → `length`) and by
`_PARAM_TO_TA_BY_TYPE`, a per-indicator override in which one declared parameter may fan out to
*several* library keywords. `bbands.std` → (`lower_std`, `upper_std`) is the case that motivated it.

**Every pandas_ta function ends in `**kwargs`**, which makes an unknown keyword *silently inert*
rather than an error. A mis-spelled parameter therefore does not fail — it is dropped, and the
indicator is computed at the library default while the strategy file says otherwise. This is the
quietest possible defect and produces exactly the authoritative-but-wrong number the engine exists
to prevent.

So the mapping is **asserted at registration**, not trusted: `install()` walks every registered
spec, and for each declared parameter checks that every keyword it would be passed as appears in
the `inspect.signature` of the corresponding pandas_ta function. A mismatch raises
`IndicatorParameterMismatchError` at startup, naming the indicator, the parameter, the keyword that
would have been dropped, and the argument list the library does accept.

This is the parameter-side counterpart of §5.4's output assertion, and it exists because the two
have different natural failure modes: a renamed *output* column breaks a namespace lookup and is
loud, whereas a renamed *parameter* is absorbed by `**kwargs` and is silent. A pandas_ta upgrade
that renames an argument now fails at startup instead of quietly changing every result.

---

## 6. Signal layer

### 6.1 Grammar

Per §2.1. Comparisons combined with `&`/`|` must be parenthesised, because Python's precedence binds
`&` tighter than `<`. **[NEW]** An unparenthesised comparison adjacent to a bitwise operator is
detected at parse time and rejected with the corrected expression in the message — legacy left this
to a runtime error inside pandas (`src/bot/generator.py:260-263`).

### 6.2 Evaluation

The validated AST is walked against the §5.1 namespace. Names resolve to Series; comparisons and
bitwise operators produce boolean Series; arithmetic produces float Series. The result must be a
boolean Series aligned to the data index — anything else is an error naming the expression.

**[FIX]** See §10 D15. Comparison against NaN yields `False`, but that is *not* sufficient to keep
undefined values out of trading decisions, for two reasons:

- **Negation inverts it.** `~(rsi > 30)` is `True` wherever `rsi` is NaN, because the inner
  comparison was `False`. A single `~` converts "undefined" into an active signal.
- **NaN is not confined to warm-up.** Several registered indicators are undefined on interior bars
  *by design*: `psar_psarl` holds a value only while the trend is up, `supertrend_supertl` only
  while the supertrend is long. Measured over 300 bars, `psar_psarl` is NaN on 61% of post-warm-up
  bars and `supertrend_supertl` on 90%.

**Target — definedness tracking.** Evaluation carries a boolean *defined mask* alongside the value.
A `Name` node contributes `series.notna()`; every operator intersects the masks of its operands; a
`Constant` contributes all-`True`. The final signal is `signal & defined`, applied before the
next-open shift. Undefined therefore collapses to "no signal" under every operator, including
negation, rather than under comparison only.

The mask is reported as `defined_pct` per signal, so a strategy whose entry condition is undefined
on 90% of bars is visible in the output rather than merely silent.

### 6.3 Warm-up suppression

**[NEW]** Before alignment, the first `max(warmup)` positions of every signal are forced to `False`.
Warm-up suppression and the §6.2 defined mask are complementary, not redundant: the mask handles
undefined values wherever they occur, and suppression additionally covers the case where an
indicator has *converged to a value* before the library's declared warm-up has elapsed. Both are
applied.

### 6.4 Next-open alignment

**[PORT]** `src/execution/signals.py:21, 31`.

```
raw = evaluate(expr, namespace)         # decision made on the close of bar D
aligned = causal_shift(raw, 1)          # boolean lands on bar D+1
aligned = aligned.fillna(False).astype(bool)
```

vectorbt is then given `price=Open`, so the fill occurs at `Open[D+1]`.

**Conformance test [PORT]** — `tests/test_execution.py:69`. A 25-bar synthetic series whose close
jumps at index 20 such that `close > 30` is true only there, produces a first trade with
`Entry Timestamp == index[21]`. This assertion is carried over verbatim and is the canonical
next-open test.

---

## 7. Backtest engine

### 7.1 One entry, one exit

**[FIX] — decided 2026-08-16, replacing the ported behaviour.** A strategy declares exactly one
`entry` and one `exit`, so a backtest is one simulation producing one set of numbers.

Legacy (**[PORT]** `src/execution/backtester.py:23-26`) took the cartesian product of `E` entry
variants × `X` exit variants, simulated each pair independently, and reported the best of `E·X`.
Three problems, in increasing order of seriousness:

1. **The reported winner was selected on the same data every pair was measured on.** That is a
   selection step, and it inflates whichever pair wins. Nothing in the backtest path corrected for
   it; only §12.3's deflated Sharpe did, and only for optimizer runs.
2. **The two selection criteria disagreed.** The search scored candidates by taking the best
   variant under the configured objective (Calmar by default), while the winner promoted into the
   reported config was chosen by raw `total_pnl` — the metric §9.3 rejects as "scale-dependent and
   outlier-dominated". The parameters could therefore be fitted for one pair and the config
   reported for another.
3. **The cost compounded.** Each of `evaluations` fitness calls simulated all `E·X` pairs, and the
   trial count fed to §12.3 had to be multiplied by `E·X` to stay honest — so declaring variants
   raised the statistical bar the strategy had to clear in exchange for a convenience.

Comparing two entry conditions is still possible and is now explicit: write two strategies and run
both. The comparison is then visible in the run history rather than hidden inside one number.

Removed with it: `expand_variants`, `VariantPair`, `VariantSimulation`, `VariantResult`,
`BacktestResult.variants`, `BacktestResult.best`, and the optimizer's variant-pruning step.

### 7.2 Exit composition

**[FIX]** This is the largest behavioural change in the engine. Legacy composition
(`src/execution/backtester.py:35-47`):

```python
if max_holding_days:
    exits |= entries.shift(max_holding_days)
if min_holding_days:
    ignore = OR over i in 1..min_holding_days of entries.shift(i)
    exits &= ~ignore
```

Both rules key off **entry signals**, not realised positions. An entry signal that fires while
already in a position — vectorbt ignores it — still schedules a time exit `max_holding_days` later
and still suppresses exits for `min_holding_days`, both relative to a trade that never happened. And
`min_holding_days` masks only the signal and time exits; vectorbt's internal SL/TP fire regardless,
so the documented guarantee ("Never sell before holding for at least this many trading days",
`src/bot/handlers/explain.py:130`) is not delivered.

**Target — decided 2026-08-16, revising the original target.** Holding constraints are evaluated
against **realised positions**, but they **do not bind SL/TP**. A stop-loss or take-profit always
fires; `min_holding_days` suppresses only *signal* exits and *time* exits.

Rationale: a stop-loss that is disabled for the first `N` days is not a stop-loss. Binding it to the
holding constraint fattens the left tail of the return distribution and makes the reported maximum
drawdown reflect a risk control the user believes is active but which was switched off. Between the
legacy documented guarantee ("never sell before holding this many days") and the guarantee a stop
makes ("never lose more than this"), the stop wins.

**Implementation — decided during Phase 6, superseding two earlier designs.** The rules are applied
**inside the simulation**, through vectorbt's `signal_func_nb` hook, which is called once per bar
with `position_now` already reflecting everything that happened earlier. The holding rules therefore
key off realised positions *by construction*, in a single pass, with no iteration.

Two earlier designs were tried and are recorded here because the reasons they failed are the reasons
this one is right.

*Design 1 — fixed-point iteration.* Compute exits from realised positions, re-simulate, repeat until
the exits stop changing. Correct, and the original spec text. It was abandoned on measurement: the
recursion is a **chain, not a contraction**. A time exit for position *k* only becomes known once
position *k* is realised, which requires the exit of position *k-1*, so each pass resolves exactly
one more trade. A traced run on a 120-bar series advanced one trade per iteration:

```
iter1: exits@[]        -> realised@[2]
iter2: exits@[6]       -> realised@[2 7]
iter3: exits@[6 11]    -> realised@[2 7 20]
```

A history with 200 trades would need 200 full simulations per optimizer evaluation.
No iteration cap can rescue that; the algorithm is simply wrong for the problem.

*Design 2 — suppress entries that collide with a forced exit.* This was needed because vectorbt
**discards an exit that lands on the same bar as an entry** (`upon_long_conflict` defaults to
`ignore`), and `signals.clean` deletes it outright — so a `max_holding_days` exit falling on a bar
where the entry condition still held would silently vanish and the constraint would not be delivered
at all. Suppressing the entry fixed the collision but made the iteration *worse*, because the entry
set now changed between passes too.

*Design 3 — the signal function.* Both problems disappear. The rules are evaluated with the position
state in hand, so there is nothing to iterate; and the function returns one decision per bar, so an
entry and an exit can never collide and vectorbt's conflict resolution is never reached.

```
per bar i, given position_now:
    if flat:                      emit entry := entries[i];  forget the entry bar
    else:
        held := i - entry_bar
        if forced_exits[i]:                       emit exit          # session close, §7.6
        elif max_holding and held >= max_holding: emit exit          # forced
        elif held < min_holding:                  emit nothing       # suppressed
        else:                                     emit exit := exits[i]
```

`forced_exits` is all-False on a daily run. It is checked **before** the minimum-holding gate
deliberately: that gate works by suppressing exits, so a session close folded into `exits` would
be swallowed by it. See §7.6.

Stops are not consulted here: the simulation applies them independently and they always fire,
including inside the minimum-holding window.

`NonConvergentHoldingConstraintError` is **removed** from the error taxonomy. Nothing iterates, so
nothing can fail to converge.

`max_holding_days` counts **trading days** (index positions), not calendar days.

### 7.3 Signal cleaning — [DROP]

**[DROP]** Legacy applied `entries.vbt.signals.clean(exits)` (`src/execution/backtester.py:49`) to
make signals strictly alternate. This engine does **not**.

Two reasons. It is redundant: `from_signals` with `accumulate=False` and `direction='longonly'`
already ignores an entry raised while in a position and an exit raised while flat, so cleaning
changes no result. And it is harmful: when an entry and an exit fall on the same bar, `clean`
resolves the conflict by **deleting the exit**, which is precisely the bar a `max_holding_days` exit
tends to land on. Measured on a 12-bar case with entries on bars 2-9 and a forced exit on bar 6,
`clean` returned entries `[2]` and exits `[]` — the constraint erased. The signal function
(section 7.2) makes the question moot by never emitting both.

### 7.4 Stops

**Priority chain [PORT]** per §3.7.

**ATR stop [FIX].** Legacy computed it as a per-bar percent series
(`src/execution/portfolio.py:21-25`):

```python
atr = ta.atr(df.High, df.Low, df.Close, length=14)   # direct call, bypasses apply_ta
atr = atr.fillna(0)
sl_stop = ((atr * multiplier) / df.Close).shift(1).fillna(0)
```

Three defects: the direct `ta.atr` call bypassed the indicator layer entirely (§10 D6); `.fillna(0)`
on the ATR produced a zero stop distance during warm-up; and `.fillna(0)` on the resulting series
means "0% stop", which in vectorbt is not "no stop" but a stop at the entry price (§10 D7).

**Target:** ATR is computed through the registry like any other indicator, with `length=14` fixed.
The stop series is

```
sl_stop = causal_shift((ATR(14) * multiplier) / Close, 1)
```

with **NaN** in the warm-up region, meaning *no stop* — not zero. The `.shift(1)` is the same
next-open rule as everywhere else: the stop distance applied on bar *t* is derived from bar *t-1*'s
close and ATR, both known before *t* opens.

`trailing_stop_pct` and `stop_loss_pct` are scalars, `value / 100.0`
(`src/execution/portfolio.py:28, 31`). `sl_trail=True` only for the trailing case
(`src/execution/portfolio.py:29`). `take_profit_pct` → `tp_stop = value / 100.0`
(`src/execution/portfolio.py:35`).

### 7.5 vectorbt invocation

**[FIX]** Legacy passed nine arguments to `Portfolio.from_signals`
(`src/execution/portfolio.py:50-64`) and inherited every other default implicitly, making results
silently version-dependent. Target: **every** parameter that affects the result is passed
explicitly.

| Parameter | Value | Source |
| --- | --- | --- |
| `close` | `df.Close` | **[PORT]** `portfolio.py:51` |
| `entries` / `exits` | composed boolean Series | **[PORT]** |
| `price` | `df.Open` | **[PORT]** `portfolio.py:54` — the next-open fill |
| `init_cash` | `execution.initial_capital` | **[PORT]** `portfolio.py:55` |
| `size` / `size_type` | per §3.8 | **[PORT]** `portfolio.py:37-48` |
| `fees` | `commission_pct / 100` | **[PORT]** `portfolio.py:58` |
| `slippage` | `slippage_pct / 100` | **[PORT]** `portfolio.py:59` |
| `sl_stop` / `sl_trail` / `tp_stop` | per §7.4 | **[PORT]** `portfolio.py:60-62` |
| `direction` | `'longonly'` | **[FIX]** implicit default, never stated |
| `accumulate` | `False` | **[FIX]** implicit |
| `cash_sharing` | `False` | **[FIX]** implicit; irrelevant single-column but pinned |
| `size_granularity` | `None` (fractional shares permitted) | **[FIX]** implicit |
| `stop_entry_price` | `'fillprice'` | **[FIX]** implicit; see below |
| `stop_exit_price` | `'stoplimit'`, pessimistic per §2.6 | **[FIX]** implicit |
| `upon_stop_exit` | `'close'` | **[FIX]** implicit |
| `use_stops` | `True` | **[FIX]** implicit |
| `signal_func_nb` | the holding-rule function, §7.2 | **[NEW]** |
| `freq` | the run's `Calendar.freq` — `'1D'`, `'30min'`, … | **[PORT]** `portfolio.py:63` |
| `seed` | from settings | **[NEW]** |

**[FIX] `year_freq` is not a `from_signals` parameter.** Verified against the installed vectorbt
1.0.0: it is absent from that function's 60 parameters. It exists as the process-global
`vbt.settings['returns']['year_freq']` (default `'365 days'`) and as a per-call argument on the
metric methods — `sharpe_ratio`, `sortino_ratio`, `annualized_return`, `max_drawdown`,
`calmar_ratio` all accept it; `total_return` correctly does not.

The global is **never** mutated: it is process-wide state that would leak into any other vectorbt
consumer sharing the interpreter. `year_freq='252 days'` is passed explicitly at every metric call
site instead, and a test asserts that the two calendars give different Sharpe values so the argument
cannot be silently dropped.

**[FIX] `stop_entry_price`.** vectorbt defaults this to `'close'` — stop distances are measured from
the **close of the entry bar**. But the engine fills at that bar's *open*, so a configured "5% stop"
would not be 5% below the price actually paid, and would drift looser or tighter with the entry day's
own range. `'fillprice'` measures from the executed price, slippage included, which is what the
configuration means.

**[FIX] `stop_conflict_mode` does not exist** in vectorbt 1.0.0 either. The parameters that do exist
for intrabar stop resolution are `stop_exit_price` and `upon_stop_exit`. §2.6's pessimistic
conventions are expressed through those, established empirically in Phase 6.

**[FIX] Annualisation inconsistency.** Legacy passed `freq='d'` and let vectorbt annualise with its
default `year_freq` of 365 days when computing `annualized_return()`
(`src/optimization/evaluator.py:105`), while the dead metrics module used `ann_factor=252`
(`src/metrics.py:21, 31`). The two shipped numbers were annualised on different calendars. Target:
`year_freq='252 days'` everywhere — daily *trading* bars, not calendar days — and no metric computes
its own annualisation factor.

**[PORT]** Long-only. Legacy never stated this; it was a library default
(`src/execution/portfolio.py:50-64` passes no `direction`). It is now explicit, and short signals are
not expressible.

#### The trading calendar **[NEW]**

`freq` and `year_freq` were module constants — `'1D'` and `'252 days'` — while every history was
daily. They are now a `Calendar` computed once per run, because those constants are not merely
inadequate for intraday data, they are *silently* inadequate: a 30-minute Sharpe annualised on a
252-bar year is overstated by √17, roughly a factor of four, printed to two decimal places with
nothing to suggest it is anything but the truth.

| Interval | `freq` | `periods_per_year` |
| --- | --- | --- |
| `1d` | `1D` | 252 |
| intraday | `interval.pandas_freq` | 252 × `median_bars_per_session` |

**`bars_per_session` is measured from the ticker's own history, never assumed.** It is a property
of the *exchange*, not of the interval: a 30-minute Xetra session is 17 bars and a 30-minute New
York session is 13. No constant satisfies both, and half-days, early closes and late opens
disagree with any figure chosen — hence a median over completed sessions (§4.6).

`year_freq` is a `Timedelta` giving the length of a trading year *in trading time*, since vectorbt
derives its annualisation factor as `year_freq / freq`: 252 days at `1d`, and 4284 half-hours
(≈89 calendar days) at 30-minute Xetra bars. Passing a calendar year is audit finding A2; passing
a *daily* year on intraday bars is the same error one interval down.

Everything derived from the old constants takes the calendar: the risk-free per-period conversion
(exponent `1/periods_per_year`), the returns accessor, the worst-rolling-12-month window, and the
information ratio in the benchmark — which previously recovered its period count by splitting the
string `'252 days'` apart, and stopped being a number of periods the moment a period was not a day.
`settings.TRADING_DAYS_PER_YEAR` remains the daily basis and is the *sessions* factor intraday.

`DAILY` is the same object the constants were, so daily results are not merely equal to what they
were — they are produced by identical values.

**Interval agreement is checked, not assumed** (`require_interval`). A history whose median bar
spacing disagrees with the declared interval by more than 25% is rejected, naming both and the
factor by which the run's figures would have been scaled. Adjacent intervals differ by a factor of
two, so the tolerance cannot confuse 15m with 30m while still absorbing an unusually chopped-up
history. The median is used because real data has weekend, holiday and overnight gaps; on intraday
data the median is the within-session spacing, since overnight gaps are a minority of the
intervals.

### 7.6 Intraday session semantics **[NEW]**

> **An intraday strategy never holds a position overnight.** Exits are forced at the end of each
> session; entries are suppressed on the bar the forced exit lands on, and in any session whose
> close cannot yet be predicted.

The trader executes manually through a retail broker. A position left open across a close carries
gap risk the backtest cannot model and the trader did not agree to.

**The causality trap.** "The last bar of the session" naively means "no later bar exists on this
date" — a fact about the *future* of bar `t`. Implementing it that way is look-ahead, and §2.4's
harness catches it at once: truncate mid-session and every truncation point becomes a phantom
close, so a bar's decision depends on how much history follows it. The rule is therefore built
from two layers, both pure functions of the past:

1. **Learned close.** The expected close time for bar `t` is the **latest bar-open time observed
   among the 5 most recent completed sessions** — completed meaning "whose date is strictly
   earlier than `t`'s", knowable at `t`. A bar landing on that time is the session's close: the
   position is exited and no entry is taken. Taking the *latest* close across the window rather
   than the most recent is what makes a half-day inside the window harmless — it lowers no
   expectation, so the next normal session is still recognised.
2. **Safety net at the next open.** A position surviving a session boundary is closed on the first
   bar of the new session; "the previous bar has a different date" is again a fact about the past.

**The first session of a history is not traded at all.** It has no earlier session to learn from,
so its close cannot be predicted, and opening a position the engine cannot promise to close would
breach the constraint on bar one of every intraday run — and would pin `overnight_carries` at 1
forever, making it useless as a signal. Refusing to enter costs one session of warm-up, of a kind
the engine already has for indicators.

**`overnight_carries` is reported, never suppressed.** Zero is the healthy value. A non-zero count
means the venue closed earlier than the learned time — the learned close is *wrong* several times
a year by construction, since a half-day never matches the latest recent close. Two years of Xetra
hourly bars contain an early close, a late open and two five-bar December sessions; the LSE trades
four-bar half-days on dates Xetra is shut outright. Saying "a position carried overnight" when one
did is the honest outcome; guessing a shorter close to make the number nicer would be inventing an
exchange calendar the engine does not have.

**Interaction with the minimum holding period.** `min_holding_bars` works by *suppressing* exits,
so the forced close is passed to the signal function as a separate argument checked **before** that
gate rather than being OR-ed into the ordinary exit series. Folding it in would let the suppression
swallow it — and only on strategies that asked to hold for a while, which is the worst possible
population to break. A minimum at or above the measured session length is refused outright: no
position could ever reach it, so the strategy simulated would not be the one written.

**The forced exit fills at the closing bar's open**, like every other signal in the engine (§6.4).
An intraday strategy is therefore flat from the last bar's open, forgoing that final bar's move.
This is a real cost and is the conservative direction; introducing a close-fill convention for this
one case would put two execution conventions in the engine.

**The buy-and-hold benchmark is not session-constrained.** Buy-and-hold *is* an overnight position;
forcing it flat each afternoon would make it a different and much weaker hurdle. Both sides still
run through the same simulation with the same costs, and the point of the comparison is precisely
that an intraday strategy has to earn its constraint.

---

## 8. Metrics

**[FIX]** Legacy had two metric implementations. `src/metrics.py` defined a six-metric
`MetricsRegistry` (CAGR, max drawdown, win rate, Sharpe, profit factor, Sortino) that **nothing
imported** — dead code. The metrics actually shipped were computed inline in
`src/optimization/evaluator.py:98-107`. This spec defines one merged set.

| Metric | Definition | Source |
| --- | --- | --- |
| `total_trades` | closed trade count | `evaluator.py:85` |
| `win_rate_pct` | winning / total × 100, `0.0` when no trades | `evaluator.py:102` |
| `profit_factor` | gross profit / abs(gross loss); `inf` if no losses and profit > 0; `0.0` if neither | `evaluator.py:86-96` |
| `total_pnl` | sum of closed-trade PnL | `evaluator.py:88` |
| `cagr_pct` | `annualized_return() × 100`, `0.0` when NaN | `evaluator.py:105` |
| `max_drawdown_pct` | `max_drawdown() × 100`, `0.0` when NaN. Negative. | `evaluator.py:106` |
| `sharpe_ratio` | `sharpe_ratio(risk_free=<per-period>)` | `metrics.py:21` |
| `sortino_ratio` | `sortino_ratio(risk_free=<per-period>)` | `metrics.py:31` |

**[NEW]** Added to the shipped set: `final_equity`, `exposure_pct` (fraction of bars in a position),
`avg_holding_days`, `best_trade_pnl`, `worst_trade_pnl`. These cost nothing to compute and are what
one actually needs to judge whether an optimizer result is real.

**[NEW] Benchmark.** Every result carries buy-and-hold of the same ticker over the same window under
the same cost model, as a full `Metrics` instance, plus the excess return and the information ratio.
Without it the report answers "did the optimizer do something" rather than "should I invest": a
strategy returning 13% on a ticker that returned 40% would read as a success. vectorbt accepts
`benchmark_rets` on the metric methods directly, so this is a reporting decision, not a modelling
one.

**[NEW] Cash convention.** Idle cash earns nothing, while `risk_free_rate` is charged as the Sharpe
hurdle across the whole period. This is the standard excess-return frame and is kept, but it means a
strategy in the market 25% of the time is charged a full hurdle on 100% of the period. `exposure_pct`
is therefore reported adjacent to every risk-adjusted metric so the figure is interpretable, and the
convention is stated in the output.

**[NEW] Sub-period breakdown.** A per-calendar-year return table and the worst rolling 12-month
return. "All the profit came from one quarter" is the most common way a backtest misleads, and no
aggregate figure can reveal it.

**[NEW] Data vintage.** The fetch date, first and last bar, and a hash of the price frame are
recorded in every result. `auto_adjust=True` retro-adjusts the entire series on each dividend and
split, so the same backtest run a quarter apart uses different prices; recording the vintage makes a
divergence explainable rather than merely alarming.

**[FIX] Risk-free rate.** Legacy hardcoded `risk_free=0.04` (`src/metrics.py:21, 31`), ignoring
`execution.risk_free_rate` entirely (§10 D3), and passed an **annual** figure into a parameter
vectorbt interprets **per period** (§10 D4) — inflating the effective hurdle by roughly 252×, which
drives Sharpe and Sortino strongly negative for any realistic strategy.

**Target:** the configured annual rate is converted once,

```
rf_per_period = (1 + execution.risk_free_rate) ** (1 / 252) - 1
```

and that value is passed. The conversion lives in one function with a unit test asserting that a 4%
annual rate compounds back to 4% over 252 periods.

Metrics are returned as a frozen `Metrics` dataclass, not a dict. Trades are returned as a list of
`Trade` value objects, not a vectorbt `records_readable` frame — the vectorbt column names
(`Entry Timestamp`, `Exit Timestamp`, `PnL`, …) are an implementation detail of the engine layer and
do not leak into the public API.

---

### 8.1 Per-bar series

**[NEW — decided 2026-08-16.]** `run_backtest`, `optimize` and `walk_forward` take
`capture_series=False`. When true they additionally return `RunSeries`: equity, benchmark
equity, drawdown, close, calendar-month returns with an `in_market` flag per month, and the
trailing twelve-month return.

**Captured while the run executes, never recomputed.** The provider retroactively adjusts
history for splits and dividends (§4.1, D10), so a curve rebuilt later would describe different
prices than the metrics and the vintage block recorded beside it. A chart that disagrees with
the numbers under it is worse than no chart, and the only way to guarantee agreement is to take
both from one simulation.

Consequences that follow from that, and are tested:

- drawdown is derived from the captured equity curve, so its minimum **is**
  `Metrics.max_drawdown_pct` rather than a second opinion about it;
- every series shares one date index, because the charts share an x-axis;
- the rolling window is **trailing**. A centred window would place future bars at *t* — §2's
  failure mode, in a chart rather than in a metric;
- `optimize` captures the **test window only**. Charts for a search must describe the window it
  never saw, not the half it fitted;
- `walk_forward` captures **per fold and does not splice**. Each fold re-optimizes, so the folds
  are different strategies, and a continuous line through them would depict a strategy nobody
  traded.

Capture is opt-in because the CLI prints numbers. Turning it on must not change a result, which
is asserted the same way the progress hooks are.

**[AMENDED — 2026-08-17.]** `RunSeries` additionally carries `filled`: the dates within the
captured window that were forward-filled rather than observed (§4.3). Held as dates rather than
as a per-bar mask, because they are a handful out of thousands and a chart marks them
individually.

`DataVintage.filled_bars` already counts them. Counting is enough to *warn* about a history and
not enough to *draw* one: a chart given only a count has no choice but to run a straight line
through a price that never traded, which is the one thing §4.3 says a filled bar must not be
allowed to look like. When the provider tracked no provenance the tuple is empty, which is not a
claim that nothing was filled — the vintage block is where those two are told apart.

The API stores it under the series name `filled`, with a `dates` column and deliberately no
`values` column. A value per date would make it look like a measurement.

---

## 9. Optimization engine

### 9.1 Parameter discovery

**[PORT]** `src/optimization/optimizer.py:29-51`. Recursive traversal of `indicators`, `entry`,
and `exit` only. `strategy`, `universe`, `execution`, and `position_sizing` are never optimized.

`indicators` is a list, so a parameter from it is qualified by its entry's `name` —
`indicators.rsi_ind.window`. `entry` and `exit` are single mappings with no name to qualify with,
so their parameters are `exit.stop_loss_pct` and so on. `entry` holds only `signal`, which is
structural, so in practice it never yields a parameter: an entry is tuned through the indicators
its signal references. Every non-bool `int`/`float` leaf becomes a parameter.
Int-ness is recorded from the YAML literal (`optimizer.py:50`) and re-applied on injection
(`optimizer.py:59`): ints via `int(round(v))`, floats via `round(v, 2)`.

**[PORT]** Default bounds are ±50%: `(min(v·0.5, v·1.5), max(v·0.5, v·1.5))`, with `v == 0` mapping
to `[-0.5, 0.5]` (`optimizer.py:44-49`). The `min`/`max` construction handles negative values.

**[FIX]** Legacy traversed the **raw dict** without ever constructing a `ConfigModel`
(`optimizer.py:16-27`), which is how `tests/test_optimizer.py` gets away with a dict-shaped
`indicators`. Target: the config is validated first; discovery runs over the validated model's dump.

**[NEW]** Per-parameter bound overrides, because ±50% of a 200-day SMA is a 100-300 range that may
be far wider or narrower than intended. The `optimize` key is available on every `indicators`,
`entry`, and `exit`, and takes either a boolean or a mapping keyed by parameter name:

```yaml
indicators:
  - name: sma_long
    type: sma
    window: 200
    optimize:
      window: {min: 150, max: 250}   # explicit bounds for this parameter

  - name: rsi_ind
    type: rsi
    window: 14
    optimize: false                  # pin every parameter of this entry

  - name: my_macd
    type: macd
    fast: 12
    slow: 26
    signal: 9
    optimize:
      signal: false                  # pin one parameter, search the others
```

Absent an override, `optimize: true` applies and every numeric parameter of the entry is searched
with ±50% bounds. Keying by parameter name is required because an entry commonly holds several
numeric parameters and a single `{min, max}` pair could not say which one it referred to.

**[PORT] Known limitation.** Numeric literals embedded in `signal:` strings — the `1.5` in
`volume > vol_ma * 1.5`, the `45` in `rsi_ind < 45` — are **not** reachable by the optimizer. This is
the main expressiveness limit of the parameterisation and is inherited deliberately; making
signal literals optimizable requires a parameter-reference syntax in the grammar and is out of
scope for this pass.

### 9.2 Search

**[PORT]** `scipy.optimize.differential_evolution` (`optimizer.py:166-169`):

| Parameter | Value |
| --- | --- |
| `strategy` | `'best1bin'` |
| `popsize` | `15` |
| `mutation` | `(0.5, 1)` |
| `recombination` | `0.7` |
| `maxiter` | `epochs` |
| `workers` | `-1` |
| `updating` | `'deferred'` |
| `init` | Latin hypercube, `popsize × dim - 1` points, baseline config injected as member 0 (`optimizer.py:156-159`) |
| `seed` | **[FIX]** required, from settings, default `0` |

Evaluation budget ≈ `(epochs + 1) × popsize × dim`.

**[FIX] Reproducibility.** Legacy passed no `seed` while using `workers=-1` (§10 D12), so no run was
reproducible. Target: `seed` is always passed.

**[NEW] The fitness function is an object, not a closure.** A closure cannot be pickled, so
`workers=-1` fails outright with *"Can't get local object"* — which means the worker-independence
assertion below could not even run against a closure-based implementation. It is a module-level
class with `__call__`.

**[NEW] Failure tallies are not exact under parallel search.** Child processes mutate *copies* of
the fitness object, so its counters do not return. `evaluations` is taken from scipy's own `nfev`,
which is authoritative regardless; `failures` and `infeasible` are marked `counts_exact = False`
and the report says they are unavailable rather than printing a confident zero for a run that may
have failed throughout.

**[NEW] The test window is sized for the widest candidate**, not the baseline. Bounds are ±50%, so a
200-bar window can grow to 300; sizing the warm-up prefix from the baseline would let such a
candidate have its signals suppressed *inside* the scored region, quietly losing test bars. With `updating='deferred'` the population is evaluated
synchronously per generation, so results are identical regardless of worker count — this is asserted
by a test running the same optimization at `workers=1` and `workers=-1` and comparing the best
parameter vector exactly.

**[FIX] Integer parameters.** Continuous DE bounds over integers produce a piecewise-flat landscape
(§10 D13): a window of 20.0 and 20.4 are the same strategy, so the gradient information DE relies on
is destroyed over lattice-sized regions. Target: integer parameters are declared to scipy via
`integrality`, which makes DE respect the lattice directly rather than rounding after the fact.

### 9.3 Fitness

**[FIX] — decided 2026-08-16.** The objective is **pluggable**, and the default is **Calmar with a
hard trade-count floor**:

```
if trades < min_trades:   return +inf          # infeasible, not discounted
return -calmar(train)                          # annualised return / |max drawdown|
```

The floor is a **feasibility constraint**, not a multiplier: a three-trade result carries no
information regardless of how large its PnL is, and the legacy `×0.1` discount is defeated by any
fluke bigger than 10×.

**[NEW] — decided 2026-08-18. The floor has two parts, and both are configurable per run.**

```
min_trades = max(minimum, ceil(per_year * train_bars / 252))
```

with `minimum` defaulting to **20** and `per_year` to **4** (one trade a quarter). Rounded up: a
fractional trade is not a trade.

Two changes, for two separate reasons.

*The rate exists because a flat count stops constraining anything as the history grows.* Twenty
trades is a real bar over a two-year train window and no bar at all over a twenty-year one, where a
candidate clears it by trading twice a year and letting a handful of events decide the whole Calmar.
Calmar pushes in exactly that direction — the cheapest way to shrink `|max drawdown|` is to be in
the market less — so the floor is the only thing holding the search back from a strategy that barely
trades, and it must scale with the span it is a constraint about. The rate is deliberately mild. It
is a floor on how much of the window the result is evidence *about*, not an opinion about how often
a strategy ought to trade.

*Both parts are configurable because the engine does not know the strategy's intended frequency.*
A constant that suits a swing strategy is wrong for a position strategy in both directions, and
before this the floor was a module-level default reachable from no interface at all. It is now a
`TradeFloor` on `optimize`, `optimize_split` and `walk_forward`; `--min-trades` and
`--min-trades-per-year` on `cracktrade optimize` and `cracktrade walkforward`; and `min_trades` /
`min_trades_per_year` in a launch request's params, bounded there at 1000 and 500 respectively so a
typo cannot queue a search in which every candidate is infeasible.

**Resolved per split, not per run.** The floor is computed from the length of *that split's* train
window, so a walk-forward's folds carry different floors — which is the point of having a rate. The
bound objective travels on `SplitOutcome` and is what the stability surface (§12.5) re-scores
against; re-deriving it from the objective's name there would silently drop the floor and measure
degradation against a different function than the search used.

**Deliberately still not a gradient.** Above the floor there is no preference for trading more. A
graded reward on trade count is a thumb on the scale that no metric here justifies, and it drags the
search toward overtrading, which slippage then eats. The honest form of "more trades is more
evidence" is deflation by sample size (§12.3), which is already applied and is not the objective's
job.

**What is *not* changed:** `MIN_TRADES_TO_JUDGE` (§8), the separate constant below which the report
suppresses its headline verdict, stays at a flat 20 and is measured on the *test* window. The two
answer different questions — one is what the search may select, the other is what the report may
claim — and collapsing them would let a loosened search floor quietly loosen the verdict.

The objective used is recorded in `OptimizationResult`, because a number is not comparable across
objectives and the report must say which one produced it. So is the floor in force, as
`min_trades_required`: `infeasible` is uninterpretable without it, since the number that decided
those rejections is resolved per run and is not a constant a reader could look up.

**The legacy objective takes the run's floor, not its own hardcoded 5.** One run has one definition
of "too few trades to believe"; what differs between objectives is the *response* to it, and that
difference — a cliff here, a survivable 10× discount there — is the whole point of keeping legacy
selectable. Reproducing an old result exactly therefore means asking for the old floor too:
`--min-trades 5 --min-trades-per-year 0`.

**Trial counting.** The optimizer records the total number of *configurations scored*, which since
the §7.1 change is simply `evaluations`: one evaluation scores one configuration. While variants
existed each evaluation took the best of `|entry_variants| × |exit_variants|` simulations, and the
trial count had to be multiplied by that product because the variant maximum was itself a selection
step. This count feeds the Deflated Sharpe Ratio in §12.

The legacy objective is retained as a selectable option and specified here for reference. It is
**not** the default: raw PnL is scale-dependent and outlier-dominated, and its drawdown term scales
a typical −20% drawdown by only 0.8, so it disciplines risk barely at all.

**[PORT]** `optimizer.py:109-121`, per variant, maximum across variants (§7.1 removed
the maximum; the formula is reproduced here as legacy reference only):

```
if trades < 5:              pnl *= 0.1
if isnan(mdd) or mdd > 0:   mdd = 0.0
fitness = pnl * (1 + mdd)   if pnl > 0        # mdd is negative → penalises
        = pnl * (1 - mdd)   if pnl <= 0       # → penalises further
return -max(fitness)                          # negated for scipy minimisation
```

`mdd` is a negative fraction, so both branches penalise drawdown.

**[FIX] Failure scoring.** Legacy returned `0.0` from an unparseable config or a failed backtest
(`optimizer.py:77, 82`) and `0.0` when no variant produced a score (`optimizer.py:121`). Since a
genuinely losing strategy scores *negative*, an invalid configuration outranked it, and DE was
actively biased toward regions of the parameter space that do not parse (§10 D9). Target: a failure
returns `+inf` in scipy's minimisation space — strictly worse than any real result — and the failure
is counted and reported. A run where more than 20% of evaluations failed emits a warning naming the
most common exception, because that indicates bounds producing invalid configs rather than a
meaningful search.

**[FIX]** Fitness is computed on the **train window only**, and its signature accepts only
`TrainWindow` (§2.5).

### 9.4 Evaluation protocol

**[FIX]** This is the correctness centrepiece. Legacy computed `df_train`/`df_test` at an 80/20
split (`optimizer.py:20-22`), fit on train — and then computed **both** the baseline PnL
(`optimizer.py:133`) and the reported result (`optimizer.py:181-183`) on `df_full`. `df_test` was
assigned and never read. Every headline number the system produced was in-sample, on data the
optimizer had already fit to (§10 D2).

**Target protocol:**

| Stage | Data | Purpose |
| --- | --- | --- |
| 1. Split | full history → `TrainWindow` (first 80%) / `TestWindow` (last 20%) | contiguous, no shuffling |
| 2. Search | `TrainWindow` | DE finds the parameter vector |
| 3. Variant selection | `TrainWindow` | best entry × exit pair by PnL — **[PORT]** `evaluator.py:11-43` |
| 4. Prune | — | config collapsed to the winning pair — **[PORT]** `evaluator.py:45-57` |
| 5. Report | `TestWindow` | the headline numbers, computed once |
| 6. Baseline | `TestWindow` | unoptimized config on the same test window, for comparison |

Indicator warm-up for the test window is computed from train-window bars — that is past data
relative to every test bar and is correct, not leakage. The split index is chosen so that the test
window has at least `max(warmup) + 30` bars available including its warm-up prefix.

Both optimized and baseline numbers are reported on the test window, so the improvement figure
compares like with like. Legacy compared an in-sample optimized number against an in-sample baseline
(`optimizer.py:117`), which at least was consistent, but both were inflated.

**[NEW]** Train-window metrics are also reported, labelled as in-sample, next to the test metrics.
The gap between them is the single most useful overfitting diagnostic available, and hiding it would
be a disservice.

**[FIX] Walk-forward is the default protocol — decided 2026-08-16**, superseding the single-split
design above. The single 80/20 split is retained only as an explicitly selectable fast mode for
iteration, and its output is labelled as such.

A single contiguous test window is a single draw. Whether it happens to contain a bull run or 2022
dominates the result, and neither outcome carries information about the strategy. It is also a
one-shot resource that the intended workflow destroys: the user reads the test number, adjusts the
strategy, and re-runs — at which point the test window has been fit to, through the user, and the
out-of-sample label is false.

The protocol above is therefore applied **per fold** (anchored and rolling generators, fold count
configurable, default 6), and §12 reports the distribution across folds rather than a point. The
`TrainWindow`/`TestWindow` types are unchanged — the fold generator is simply another constructor
for them, so fitness and reporting need no modification.

Repeated optimization of the same strategy file against the same data range increments a run counter
recorded in the result, so re-fitting is at least visible in the output.

### 9.5 Reported result

`OptimizationResult` (a frozen dataclass, rendered by the CLI, serialisable by a future API):

- the optimized config, as a YAML string with `strategy.name` suffixed `_optimized` — **[PORT]**
  §9 of the legacy plan;
- test-window metrics, train-window metrics, and baseline test-window metrics;
- the parameter diff — `path: old → new` (**[PORT]** `optimizer.py:185-196`; the legacy filter to
  parameters belonging to the surviving variants no longer applies, since every discovered
  parameter belongs to the one strategy);
- the trade list for the test window;
- search diagnostics: evaluations performed, failures, wall time, seed, convergence message.

**[AMENDED — 2026-08-17.]** Plus `benchmark`, a `BenchmarkComparison` against buying and holding
the ticker across the **test window**, scored from `test.offset` on both sides so the hold is
credited with exactly the bars the strategy could have traded.

`baseline_test_metrics` was already there and answers a different question. It is the *user's own
configuration* measured out of sample, so the pair says whether the search did anything. Neither
says whether the result is worth owning: an optimization that lifted a strategy from 9% to 13% on
a ticker that returned 40% reads as a clear success against the baseline and is a failure. That
is audit finding B1, which §8 fixed for backtests and which an optimization run reproduced in
full — the more persuasive place to reproduce it, since an improvement figure is on screen next
to it.

The comparison and the captured benchmark curve (§8.1) are built from **one** hold portfolio.
Two simulations of the same buy-and-hold agree today and are free to diverge later, and a
benchmark line that disagrees with the benchmark number printed under it is worse than either
alone.

The field is optional in the dataclass so that results serialised before it existed still load.
Runs already in the database are not backfilled and no migration invents one: a stored run is a
record of what was computed, and a benchmark computed today from today's prices is not a fact
about a run from last month (D-16).

---

## 10. Defect register

Each entry: legacy behaviour, failure mode, target. All are fixed in this engine.

**D1 — Indicator warm-up zero-fill.**
`src/execution/indicators.py:71, 74, 109, 111` applied `.fillna(0)` to every indicator output.
Failure: over the warm-up region a rolling maximum reads `0`, so `close >= rolling_max` is true from
bar 0 and the strategy takes a spurious day-1 entry that dominates short backtests. Every
zero-crossing comparison (`macd_macdh > 0`) is similarly wrong during warm-up.
**Target:** NaN propagation (§5.5) plus explicit signal suppression until `max(warmup)` (§6.3).

**D2 — In-sample headline numbers.**
`src/optimization/optimizer.py:22` computes `df_test` and never reads it; `optimizer.py:133, 181-183`
report on `df_full`. Failure: reported PnL, CAGR and drawdown are measured on the data the optimizer
fit to; the numbers are approximately meaningless as forecasts.
**Target:** the §9.4 protocol, with type-level enforcement (§2.5).

**D3 — `risk_free_rate` ignored.**
`src/core/config.py:49` parses it; `src/metrics.py:21, 31` hardcode `0.04`. Failure: the field is
inert; changing it changes nothing.
**Target:** the configured value is used (§8).

**D4 — Annual rate used as a per-period rate.**
`src/metrics.py:21, 31` pass `risk_free=0.04` to vectorbt, which expects a per-period figure.
Failure: the hurdle is overstated by ~252×; Sharpe and Sortino are strongly negative for every
strategy, making them useless for comparison.
**Target:** `(1 + rf) ** (1/252) - 1` (§8).

**D5 — `bfill()` fabricates pre-listing history.**
`src/data/market.py:35`. Failure: for a ticker that listed mid-range, the first real close is
propagated backwards across every earlier bar, creating a flat synthetic price history; indicators
warm up on invented data and trades are taken in a period when the instrument did not trade.
**Target:** `ffill` only, drop leading NaN, error on residual NaN (§4.3).

**D6 — ATR stop bypasses the indicator layer.**
`src/execution/portfolio.py:21` calls `ta.atr(df.High, df.Low, df.Close, ...)` directly rather than
going through `apply_ta`. Failure: on multi-ticker frames it received a DataFrame where a Series was
expected and either raised or silently misbehaved. Single-ticker removes the immediate crash, but the
bypass also skipped warm-up handling and every other registry guarantee.
**Target:** ATR computed through the registry (§7.4).

**D7 — `sl_stop` zero-fill.**
`src/execution/portfolio.py:25` ends `.fillna(0)`. Failure: in vectorbt a `0.0` stop distance is not
"no stop" — it is a stop at the entry price, which triggers on the first adverse tick. Every ATR-stop
trade opened during warm-up is therefore stopped out immediately.
**Target:** NaN means no stop (§7.4). A test asserts vectorbt 1.0.0's actual handling of both `0.0`
and NaN rather than trusting this reading of it.

**D8 — Holding rules key off signals, not positions.**
`src/execution/backtester.py:35-47`. Failure: time exits and minimum-hold suppression are computed
from entry *signals*, including signals that never opened a position; and `min_holding_days` does not
bind vectorbt's internal SL/TP, so the documented guarantee is not delivered.
**Target:** fixed-point iteration over realised positions (§7.2).

**D9 — Failures score better than losses.**
`src/optimization/optimizer.py:77, 82, 121` return `0.0`. Failure: `0.0` beats any losing strategy's
negative score, so DE is attracted to parameter regions that fail to parse or fail to simulate.
**Target:** `+inf` in minimisation space, with failure counting (§9.3).

**D10 — Swallowed `UnboundLocalError`.**
`src/optimization/evaluator.py:90-91` assigns `variant_pnl` only inside `if target_ticker:`; when the
target ticker matches no column the name is unbound at `evaluator.py:113`, raising
`UnboundLocalError`, which the bare `except Exception` at `evaluator.py:117` swallows — returning
zeros that read as "this strategy made no money" rather than "this run was broken".
**Target:** no bare excepts in the engine. Exceptions are typed, caught at a boundary that can act on
them, and never converted into a plausible-looking zero.

**D11 — No enforced minimum history.**
Legacy accepted any date range. Failure: a 200-day SMA over a 60-day backtest is entirely warm-up;
with D1's zero-fill it produced trades anyway.
**Target:** validated at config time and again after data load (§3.3, §4.3).

**D12 — Non-reproducible optimization.**
`src/optimization/optimizer.py:166-169` — no `seed`, `workers=-1`. Failure: the same command
produces different strategies on consecutive runs; results cannot be verified or compared.
**Target:** mandatory seed, asserted worker-independence (§9.2).

**D13 — Continuous bounds over integer parameters.**
`optimizer.py:59` rounds after the fact. Failure: the fitness landscape is piecewise flat over
lattice cells, degrading DE's differential signal.
**Target:** scipy `integrality` (§9.2).

**D14 — Shape and precision.**
`float32` prices (`src/data/market.py:38`); `freq='d'` hardcoded (`portfolio.py:63`); long-only never
stated; `indicators` accepted as a dict in `tests/test_optimizer.py:11-14` versus the list the schema
requires.
**Target:** `float64` (§4.1); daily-only made explicit and validated (§7.5); long-only pinned
explicitly (§7.5); list shape normative with the dict shape rejected (§3.5).

**D15 — Undefined indicator values reach trading decisions.** **[NEW — found in this engine, not
legacy, by audit on 2026-08-16; see `docs/AUDIT.md` §A1.]**
`cracktrade/signals/evaluate.py` relied on NaN comparisons yielding `False`. Failure: `~` inverts
that to `True`, so `~(rsi > 30)` fires on every bar where `rsi` is undefined. Because `psar_psarl`
and `supertrend_supertl` are NaN on 61% and 90% of post-warm-up bars respectively — by design, not
by error — a plausible strategy trades on undefined data for most of its history. The symmetric
quieter failure is that `close > psar_psarl` is silently `False` on 61% of bars, so the strategy
under test is not the one the user wrote and nothing reports the difference.
**Target:** the §6.2 defined mask, plus `defined_pct` in the output.

**D16 — Sub-ulp High/Low noise from yfinance's `auto_adjust`, rejected as a bad bar.** **[NEW —
found live on 2026-08-16, `examples/spy.yaml`.]** `auto_adjust=True` retro-adjusts Open, High, Low
and Close independently. On an intermittent bar the adjusted High comes back a few ulps below the
adjusted Close (observed: `High=246.1731262207031` vs `Close=246.17312622070312`, a ~1e-12 relative
gap) — float noise from the adjustment arithmetic, not a data error, and only present on some
fetches of the same range because Yahoo's adjustment recomputes from the current split/dividend
table each call. §4.1's strict `Low <= Open, Close <= High` correctly rejected it, which is right
for a genuine bad print but made the whole backtest intermittently fail on good data.
**Target:** repaired at the data-preparation boundary (§4.3), not by loosening the §4.1 contract —
a real bad bar (wrong by orders of magnitude more than adjustment noise) must still be rejected
there.

**D17 — `bbands.std` never reached the library.** **[NEW — found on 2026-08-18 while measuring how
to widen a strategy's entry condition.]** The registry declares Bollinger Bands as taking `std`, and
`_ta_compute` passed it to `pandas_ta.bbands` under that name. pandas_ta 0.4.71b0 spells the
envelope width `lower_std` / `upper_std` and has **no `std` argument at all** — but, like every
pandas_ta function, it ends in `**kwargs`, so the unrecognised keyword was accepted and discarded.
Failure: every Bollinger strategy ever run by this engine computed **2.0-sigma bands regardless of
what its YAML said**, and the returned column labels (`BBL_20_2.0_2.0`) confirmed the default had
been used. Nothing raised, nothing warned, and the resulting backtest is a plausible, authoritative
and wrong number — the exact output this project's design constraint forbids. Worse for the
optimizer: a search over `std` explored a dimension that did nothing, so every candidate along it
scored identically and the reported "optimal" width was whichever the tie-break happened to return.
**Target:** a per-indicator parameter alias map (§5.5) fans `std` out to both library keywords, and
a registration-time assertion (also §5.5) rejects any declared parameter that its pandas_ta function
does not accept. The general lesson is the one §5.4 already applies to outputs: what the registry
declares must be *checked* against the library, never assumed to match. Outputs failed loudly on
their own because a renamed column breaks a namespace lookup; parameters had no such backstop, and
now they do.

---

## 11. Public API surface

The library's contract. The CLI is one consumer; a future HTTP interface is another.

Validation is split across two modules, because meaning cannot be checked where shape is.
`cracktrade.config` validates structure and is imported by the indicator and signal layers, so it
cannot import them back. `cracktrade.strategy` sits above all three and composes them: it is the
entry point interfaces should use, and a strategy that loads through it is valid in every sense the
engine checks — before any market data is fetched.

```python
# cracktrade.config -- structural validation only
def read_strategy_file(path: Path) -> Strategy: ...
def parse_strategy(data: Mapping[str, Any]) -> Strategy: ...
def dump_strategy(strategy: Strategy) -> str: ...

# cracktrade.strategy -- structural + semantic (registry, grammar)
def load_strategy(path: Path) -> Strategy: ...
def build_strategy(data: Mapping[str, Any]) -> Strategy: ...
def register_validator(validator: SemanticValidator) -> None: ...

# cracktrade.data
class MarketDataProvider(Protocol):
    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame: ...
def load_history(strategy: Strategy, provider: MarketDataProvider) -> MarketData: ...

# cracktrade.backtest
def run_backtest(strategy: Strategy, data: MarketData, *, seed: int = 0) -> BacktestResult: ...

# cracktrade.optimize
def optimize(
    strategy: Strategy,
    data: MarketData,
    settings: OptimizationSettings,
) -> OptimizationResult: ...
```

Result models (`cracktrade.domain`) are frozen dataclasses with no pandas or vectorbt types in their
public fields: `Metrics`, `Trade`, `YearReturn`, `BenchmarkComparison`, `DataVintage`,
`VariantResult`, `BacktestResult`, and — from Phase 8 — `OptimizationResult`, `ParameterChange`,
`SearchDiagnostics`.

**[AMENDED — 2026-08-17.]** "No pandas or vectorbt types" includes **numpy scalars**, and the
boundary must coerce rather than assume. `np.bool_` is not a `bool`, `np.float64` is not a
`float`, and neither is caught by an `isinstance` check for the type the field declares. The
serializer's last resort is `str(value)` (§11 above), so an uncoerced value does not fail — it
renders as the string `"False"`, which is **truthy in JavaScript**. `Trade.is_open` shipped this
way: every closed trade told a client it was open, and §8's rule that open trades are never
counted in an aggregate would have silently dropped the entire trade list while every screen
looked correct.

Coerce at construction, where the surrounding fields already do. Two repo tests hold the line:
one asserts `is_open` and `is_winner` serialise as JSON booleans, and one walks an entire
serialised result asserting no value came out as `"True"`, `"False"`, `"None"`, `"nan"` or
`"inf"` — because the next numpy scalar to leak in will look exactly as harmless as this one
did.

`cracktrade.strategy.required_warmup(strategy)` returns the bars a strategy needs before any
indicator is defined. Interfaces pass it, plus a margin, to `load_history`, which is how D11 is
enforced without the CLI reimplementing the calculation.

### 11.2 Progress and cancellation

**[NEW — decided 2026-08-16.]** `optimize` and `walk_forward` accept a `RunControl`
(`cracktrade.control`) carrying two optional callables:

```python
RunControl(on_progress: (stage: str, percent: float) -> None, should_stop: () -> bool)
```

Both are the caller's, because the engine knows nothing about terminals, queues or HTTP
requests. `RunControl()` — the default — installs neither, and the engine then behaves exactly
as it did before hooks existed.

**Hooks must not change results.** Asserted, not reasoned about: `tests/test_control.py`
optimizes the same strategy with and without hooks at the same seed and compares the resulting
YAML, metrics and parameter changes. If watching a run altered it, the numbers a user saw being
computed would not be the numbers they would otherwise have got.

**Cancellation is cooperative and coarse.** It is checked between DE generations and between
folds — never inside one — so a stopped run leaves nothing half-computed. It raises
`RunCancelled` and produces **no result at all**. A search halted at generation three is not a
cheaper search, it is an unfinished one, and reporting its intermediate numbers would present a
search that never finished choosing as though it had. Verified against scipy 1.18: returning
`True` from the `differential_evolution` callback halts the search, so cancellation is recorded
in a flag rather than inferred from `result.message`.

---

### 11.1 Conformance test list

Derived from the three legacy test files plus one regression test per defect.

**Ported:**
1. Next-open execution — signal at index 20 → entry at index 21 (`tests/test_execution.py:69`).
2. SMA computes the expected trailing mean (`tests/test_indicators.py:64-78`).
3. MACD fans out to `_macd`, `_macdh`, `_macds` (`tests/test_indicators.py:80-93`).
4. ATR auto-receives high/low/close (`tests/test_indicators.py:95-104`).
5. VWAP auto-receives high/low/close/volume (`tests/test_indicators.py:106-115`).
6. `rolling_max` / `rolling_min` produce correct windows (`tests/test_indicators.py:167-177`).
7. Parameter discovery finds exactly the numeric leaves, with ±50% bounds
   (`tests/test_optimizer.py:23-45`).
8. Parameter injection preserves int-ness and does not mutate the source config
   (`tests/test_optimizer.py:47-78`).

**New — causality:**
9. Truncation equivalence across the whole registry (§2.4).
10. Property-based truncation equivalence over generated strategies (§2.4).
11. `causal_shift` raises on negative periods.
12. Repo contains no `.shift(-` or `center=True` outside the helper.
13. Every forbidden AST node type is rejected, one test per node type.
14. `close.shift(-1) > close` fails to parse.
15. Passing a `TestWindow` to fitness fails type-checking (mypy-asserted).

**New — one per defect:**
16. D1 — `close >= rolling_max(252)` produces no entry before bar 252.
17. D2 — reported metrics differ from train metrics on a strategy that overfits.
18. D3 — changing `risk_free_rate` changes Sharpe.
19. D4 — 4% annual compounds to 4% over 252 periods.
20. D5 — a late-listing ticker produces no bars before its first real close.
21. D6 — ATR stop matches a registry-computed ATR exactly.
22. D7 — NaN `sl_stop` opens no stop; `0.0` behaviour asserted against vectorbt 1.0.0.
23. D8 — no realised position closes earlier than `min_holding_days` *by signal or time exit*, and
    every `max_holding_days` trade lasts exactly that long; a stop still fires inside the window.
    Plus: the holding rules cost exactly one simulation (§7.2).
24. D9 — a config that fails to parse scores worse than a losing config.
25. D10 — no bare `except` in the engine (AST-scanned).
26. D11 — insufficient history raises.
27. D12 — same seed → identical result at `workers=1` and `workers=-1`. For evolution the bar is
    higher: `workers=1` and `workers=2` agree on the trial count and the failure tallies too, not
    only on the winner (§16.4).
28. D13 — integer parameters take only integer values across the search.
29. D14 — non-daily index rejected; dict-shaped `indicators` rejected; dtype is `float64`.
30. D17 — every declared indicator parameter is a keyword its pandas_ta function accepts (§5.6),
    and `bbands` at 3.0 sigma produces a strictly wider envelope than at 1.0 sigma with an
    unchanged midline.

---

## 12. Validation and robustness

**[NEW — decided 2026-08-16.]** The engine's discipline against *look-ahead* bias (§2) has no
counterpart against *selection* bias, which is the failure mode that actually costs money in a
strategy optimizer. §2 guarantees that no number was computed from the future. This section
guarantees that a number which survived a search of thousands of candidates is reported with the
evidence needed to judge whether it is signal or the maximum of noise.

Every instrument here consumes data the engine already produces. None requires new market data.

### 12.1 Walk-forward folds

Per §9.4. Two generators, both producing `(TrainWindow, TestWindow)` pairs with contiguous,
non-shuffled, strictly ordered windows:

- **anchored** — train start fixed at the first bar, train end advancing per fold;
- **rolling** — train window of fixed length sliding forward.

Default 6 folds. Each fold runs the full §9.4 protocol independently: its own search and its own
single test evaluation. Warm-up for a test window is drawn from bars
preceding it, which is past data relative to every test bar.

### 12.2 Fold dispersion

The reported object is a distribution, not a point: per-fold test metrics, their median and IQR, and
the **fold win rate** (folds with positive test return / total). A strategy positive in 6 of 6 folds
and one whose entire edge sits in fold 3 are different objects and must not print the same headline.

### 12.3 Deflated Sharpe Ratio

Bailey & López de Prado. Adjusts the observed Sharpe for the number of trials, the sample length,
and the skew and kurtosis of the return series, yielding the probability that the true Sharpe exceeds
zero.

The trial count is the one recorded in §9.3 — `evaluations` —
not the number of DE generations. Understating it understates the deflation.

Motivation: the maximum Sharpe over `N` independent trials on **pure noise** is inflated by roughly
`sqrt(2 * ln N)` standard errors. At `N = 5000` that is about 2.9σ. A search with no edge whatsoever
produces an impressive optimum as a matter of arithmetic, and nothing in §9 could previously say so.

### 12.4 Probability of Backtest Overfitting

CSCV (combinatorially symmetric cross-validation) over the §12.1 fold matrix: the frequency with
which the configuration ranked best in-sample lands below the median out-of-sample. Reported as a
probability. A PBO above 0.5 means the selection procedure is worse than choosing at random and the
result must not be presented as a recommendation.

**An uncomputed PBO fails.** CSCV splits the slices into halves every possible way, so fewer than
four slices yields no partitions at all and the probability stays at its initialised zero — which
is the *best* possible value and compares below the 0.5 bar. `is_computed` distinguishes the two,
`is_acceptable` requires it, and the check reports "not computed — too few slices" rather than
`PBO 0.00`. Reachable from a three-fold walk-forward as well as from evolution; see §16.8.

### 12.5 Parameter stability

Differential evolution returns a point. A financially usable optimum is a **plateau**; a sharp spike
is a curve fit, and the two are indistinguishable in a point report.

After the search, each optimized parameter is perturbed independently across a local grid (±10%,
±20%, integer parameters to the neighbouring lattice points) and the objective re-evaluated on the
train window. The result carries the degradation surface. A result where a single-parameter ±10%
perturbation removes more than half the objective is **flagged as unstable** in the output.

### 12.6 Bootstrap confidence intervals

Stationary block bootstrap over the test-window trade sequence, giving intervals on mean trade
return and on CAGR. Block resampling rather than i.i.d. resampling because trade outcomes are
serially dependent.

A 20% test window may hold six trades. Six trades support no conclusion, but a point estimate
printed to two decimal places implies one. Below `min_trades` (§9.3) the headline verdict is
**suppressed** rather than qualified.

**[NEW — decided 2026-08-16.]** An interval on mean fold return that **straddles zero is a
failed check** and therefore blocks credibility. This is the same standard §12.3 already
applies through the deflated Sharpe — a result that cannot be distinguished from no edge is
not a result — and it was previously computed and displayed without being allowed to affect
the verdict. Note the direction: this makes credibility *harder* to obtain, never easier. No
check is ever added in the other direction without saying so here.

### 12.7 Cost sensitivity

The headline metrics are recomputed at 1×, 2× and 3× the configured `slippage_pct`. Modelling
liquidity- and volatility-dependent slippage properly is out of scope; establishing whether the edge
survives a tripling of costs is three extra backtests and answers the question that matters. An edge
that dies at 2× costs is not tradeable.

### 12.8 The checks, and reporting order

**[NEW — decided 2026-08-16.]** The checks are a **structured, contractual part of the result**:
`ValidationReport.checks` is a tuple of `Check(name, label, passed, plain, stat, detail)`, and
`failures` and `is_credible` are both *derived* from it.

This is the single definition of the verdict. Interfaces render checks; none of them recomputes
pass or fail from the underlying statistics. A second implementation of the verdict is a second
verdict, and the two eventually disagree — in public, on the screen someone is using to decide
where to put money. `name` is a stable identifier interfaces may branch on; `plain` is part of
the output, not documentation, because a failure nobody understands is a failure nobody acts on.

The tuple is emitted in the order below. The CLI leads with the verdict, not the equity curve:

1. benchmark comparison (§8) — did it beat buy-and-hold;
2. fold dispersion (§12.2) — was it consistent;
3. PBO and deflated Sharpe (§12.3, §12.4) — is it distinguishable from noise;
4. parameter stability (§12.5) — is it a plateau;
5. cost sensitivity (§12.7) — does it survive friction;
6. the headline metrics.

A result failing (1), or with PBO > 0.5, or flagged unstable, is reported as such before any
profitable-looking number is shown.

### 12.9 Per-fold trades

**[NEW — decided 2026-08-17.]** `FoldResult` carries `trades`: that fold's own out-of-sample
trade list. Nothing is recomputed to produce it — the runner already evaluates each fold as a
full backtest and previously kept only the metrics.

**These may be read per fold and must never be pooled across folds.** Every fold re-optimizes
from scratch, so a fold's trades belong to that fold's parameter vector and to no other. A
histogram of all folds' trades together, or a spliced per-trade equity line, would describe a
configuration that was never run — the same reason §8.1 refuses to splice fold equity curves,
and the reason `ValidationReport` publishes a distribution rather than a pooled result.

`ValidationReport.total_trades` remains a *count* summed across folds, which is legitimate: it
says how much evidence the exercise produced in total, and makes no claim that the trades came
from one strategy.

An interface offering a fold selector therefore scopes its trade-level views to the selected
fold, and shows nothing rather than a pooled aggregate when no fold is selected.

### 12.10 Comparing versions of one strategy

**[NEW — decided 2026-08-17.]** An interface that tabulates a strategy's versions against each
other is running a search, and the rules that make one honest belong here rather than in a
screen.

**One run speaks for a version, and it is the latest, not the best.** Optimization and
walk-forward runs are seeded searches: running one three times against an unchanged config
produces three different answers. Reporting the maximum over them is choosing the best of *N*
trials, uncounted by §12.3's trial count and invisible in the result — so a version would be
rewarded for having been run more often, on the same screen that warns against exactly that.
Recency is not a function of the outcome; it also reads the freshest price data and is the
answer the user last saw. Where a version has more than one run of the kind, the count is shown.

**A movement between two versions may only be computed between comparable runs**: the same run
kind, the same objective, and — where applicable — the same fold count and scheme. Where the
nearest earlier version's run is not comparable, the comparison walks back to one that is and
names the versions it stepped over; where none exists, it reports no movement and says why. It
never falls back across run kinds. A version with no run of the selected kind reports nothing,
not zero and not its neighbour's figures, and below the trade floor (§8) the trade count is the
only figure it shows.

**Two runs whose price-frame digests differ (§4.4) carry a caveat**, because prices are
retroactively adjusted and a version can appear to have improved when only the history moved.

**A walk-forward has no combined drawdown, Sharpe or excess over buy-and-hold**, and an
interface must leave those blank rather than assemble one. `ValidationReport` publishes per-fold
metrics and a benchmark and nothing else, for the reason §8.1 gives: each fold re-optimizes, so
there is no single equity curve to measure. Averaging folds, or borrowing `DeflatedSharpe.observed`
— which is a *per-period* Sharpe where `Metrics.sharpe_ratio` is annualised — would produce a
number that looked exactly like the backtest column beside it and meant something else.

---

## 13. Interface surface

**[NEW]** The CLI is one consumer of the library, not a layer with logic of its own.

### 13.1 Output formats

Every result command takes `--format table|json|yaml` and `-o FILE`. Serialisation lives in
`cracktrade.serialize`, not in the CLI, because the planned HTTP interface must produce the same
shape from the same objects.

Derived properties are part of the serialised form. `is_credible`, `failures` and
`fold_win_rate` are computed rather than stored, and a field-only walk would omit exactly the
parts that carry the verdict. Each type declares which of its properties are contractual.

**JSON has no infinity.** `profit_factor` is legitimately infinite for a strategy with no losing
trades. Non-finite floats serialise as `null` rather than as `Infinity`, which most parsers
reject.

### 13.2 Stream discipline

**Stdout carries the requested output and nothing else.** Diagnostics, progress and verdicts go
to stderr, so `cracktrade backtest s.yaml --format json | jq` works without filtering.

This was a defect until Phase 9: `RichHandler` defaults to a stdout console, so every log line
landed in the middle of the JSON document. A test asserts the handler's console is a stderr one,
because capturing output would pass under a lucky harness.

Progress bars are suppressed unless stderr is a TTY and the format is `table`.

### 13.3 Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | internal error — a bug; traceback preserved |
| 2 | configuration invalid or unreadable |
| 3 | market data unavailable or off-contract |
| 4 | simulation or search failed |
| 5 | an operation was refused as look-ahead |
| 6 | the run succeeded but the result is not credible (`--strict` only) |
| 130 | interrupted |

Code 6 is deliberately not an error: the run worked, the strategy did not. It is returned only
under `--strict` so a validation run can gate a pipeline without every ordinary invocation
looking like a failure.

**The error boundary is part of the invocable surface.** `main(argv)` runs the app and maps
exceptions to codes, so a caller embedding the CLI gets the same behaviour as the console
script. Typer vendors click privately, so click's exception types are not importable; in
standalone mode click converts everything it handles into `SystemExit` while an engine error
propagates untouched, and catching those two covers the surface using only public API.

---

## 14. Persistence

**[NEW — decided 2026-08-16.]** §1.3 dropped result persistence from the *engine*. This section
adds it to the *interface* layer. The distinction is load-bearing: nothing in §§2–13 gains a
database dependency, the engine remains callable with no storage at all, and every byte stored
here is either a strategy config the user wrote or a result the engine serialized (§13.1).

PostgreSQL 15+. The DDL lives in `src/cracktrade/api/db/migrations/`; the migration chain is
the canonical schema (§14.6) and this section is the canonical *semantics*.

### 14.1 What is stored, and in what form

Four tables: `strategy` (identity + lineage), `strategy_version` (append-only config history),
`run` (one execution against one exact version), `run_series` (per-bar chart series).

**Results are stored verbatim as `jsonb`** — exactly `cracktrade.serialize.to_dict` output,
contractual derived properties included. They are never exploded into columns and never
recomputed. Two reasons, both non-negotiable:

- prices are retroactively adjusted (§4.1, D-10 in §10), so a figure re-derived later would
  describe different data than the run's own vintage block claims. A stored result is a record
  of a measurement, not a cache of one;
- the serialized form is already the cross-interface contract (§13.1). A second, columnar
  description of the same values is a second thing that can drift.

Columns are promoted out of `jsonb` only where a *list view* filters or sorts on them
(`status`, `kind`, `version`, `is_credible`, `suppressed`). Those are copies, written in the
same transaction as the result they come from, never edited afterwards.

### 14.2 Append-only, enforced

Configuration history and run records are append-only, and the schema enforces it rather than
trusting the application:

- `strategy_version` and `run_series` reject `UPDATE` and `DELETE` outright (trigger);
- `run` rows mutate only while non-terminal — status, progress, worker lease, and the single
  result-landing update. Identity fields (`kind`, `version`, `number`, `params`, `seed`,
  `queued_at`) are frozen at insert, and the whole row freezes on reaching a terminal status;
- nothing is deleted **piecemeal**. No version, no run and no series can be removed on its
  own, at any layer. The single exception is deleting a whole strategy, which takes its entire
  history with it and is described in §14.8.

  **[AMENDED — 2026-08-18.]** This bullet previously read "nothing is ever deleted. There is
  no delete operation at any layer." That was true when written and is no longer: a registry
  that only ever grows is one nobody can read, and the list screen is the working surface of
  this application. What the append-only guarantee was actually defending — that a stored
  result cannot be revised, and that a history cannot be rewritten to explain a result it did
  not produce — is untouched. Deleting everything is not rewriting anything.

**Restoring an old config appends; it never rewinds.** A restore writes a new head version whose
config is a copy of the target and whose `restored_from` records the source. Runs made against
the previous head stay attached to it. History that could be rewritten could not be used to
explain a result, which is the only reason to keep it.

### 14.3 Identity, versions, lineage

- Strategy names are globally unique and case-sensitive (amends §3.2).
- Versions are numbered `1..N` per strategy with no gaps, and are immutable once written.
- Runs carry a per-strategy display number shared across all kinds, so `#14` is unambiguous
  within a strategy without naming its kind.
- Lineage is explicit and shape-checked: a **fork** records parent strategy + the exact version
  copied; a **promotion** records parent strategy + the originating run. A fork starts a fresh
  history at v1 and does not inherit the parent's versions.
- A promotion snapshots whether its source run was uncredible at promotion time. That snapshot
  is historical fact and is never updated; the UI's warning is cleared by the new strategy's own
  validation passing, not by editing the past.

### 14.4 Derived truths — never stored

Two facts the UI displays everywhere are computed at read time, because storing them creates a
second copy that can disagree with the first:

**Staleness.** A run is stale when its `version` is below the strategy's current head. It
describes a config that no longer exists.

**Verdict.** A strategy is:

| Verdict | Condition |
| --- | --- |
| `credible` | latest succeeded walk-forward **against the head version** has `is_credible` |
| `not_credible` | that run exists and is not credible |
| `unvalidated` | runs exist, but no succeeded walk-forward against the head |
| `never_run` | no runs at all |

Only a walk-forward against the *current head* can validate a strategy. A credible run against
v3 of a strategy now at v7 leaves it `unvalidated`, because it says nothing about the config
that exists now (§12 is the authority on what a verdict means; this is only where it attaches).
`unvalidated` is not a neutral state and must never render as one.

Version change summaries and diffs are likewise computed from the stored configs, never stored.

The verdict's *supporting detail* is read from the verdict run's stored result and never
recomputed: `checks` and `failures` are the engine's own (§12.8). A verdict rendered without
its failures is a badge with no reason, which teaches the reader to ignore the badge, so the
API sends the failures with every `not_credible` verdict rather than on request.

### 14.5 Run lifecycle

`queued → running → succeeded | failed | cancelled`. The database is the queue: a worker claims
the oldest queued run with `FOR UPDATE SKIP LOCKED`, holds a lease (`claimed_by` +
`heartbeat_at`), and lands the terminal state — result, series, and promoted columns — in one
transaction. `cancel_requested` is the API's only write into a live run; the worker observes it
between generations and folds and ends the run `cancelled`, with no result and no error.

A run whose worker dies is failed honestly on lease expiry (`engine_failure`, worker terminated
mid-run) and never silently re-queued: a re-run fetches data again and is therefore a different
measurement (§14.1).

`queued_at`, `started_at`, `heartbeat_at`, and `finished_at` are all written with
`clock_timestamp()`, never `now()`. `now()` is the enclosing transaction's start time, frozen
for the transaction's whole duration; a lease held across a worker's setup and execution is not
a single-statement transaction, so `now()` would report when the transaction began rather than
when the write actually happened. This was found, not assumed: a read on the worker's own
connection (the lease sweep, checking for abandoned runs) once ran outside any unit of work,
leaving the connection idle-in-transaction indefinitely; every later write on that connection
became a savepoint inside it rather than a transaction of its own, so `now()` returned an
ever-more-stale instant. It surfaced as a live evolution run recording nine milliseconds
elapsed for a search that ran for seven seconds. The read is now wrapped like every other repo
call; `clock_timestamp()` on the wall-clock columns is the second, independent line of defence
against the same class of bug recurring elsewhere.

Failures record the engine's own error text verbatim, plus a category mapping 1:1 onto the exit
codes of §13.3:

| Category | Exit code |
| --- | --- |
| `config_invalid` | 2 |
| `market_data` | 3 |
| `engine_failure` | 4 |
| `causality_violation` | 5 |

Exit code 6 (§13.3, "succeeded but not credible") has no counterpart here by design: an
uncredible run is a `succeeded` run whose verdict says so. Encoding a verdict as a failure would
make the two indistinguishable from a queue's point of view.

### 14.6 Migrations

Forward-only, ordered SQL files applied by the engine in `api/db/migrate.py`, each in its own
transaction, serialized by advisory lock, recorded in a ledger with a sha256 checksum.

The chain refuses to run rather than guess when: an applied file's checksum has changed (edited
after application), a new file is numbered below an applied one (history rewrite), or the ledger
holds a version with no matching file (database ahead of code).

**There are no down migrations.** Rolling back applied DDL on an append-only database would be a
fiction — the data the rollback destroys is precisely the data the design promises to keep.
Recovery is a new forward migration.

### 14.7 Promotion, and the verdict that travels with it

A succeeded `optimize` or `walk_forward` can be promoted: its winning configuration becomes a
new strategy (`origin: promoted`), recording the parent and the originating run. A `backtest`
cannot — it runs the configuration exactly as written, so promoting it would only copy the
parent. Promotion is one transaction: strategy, v1 from `optimized_yaml`, and a backtest
already queued against it, so a promoted strategy is never displayed with no numbers at all —
an empty result invites the reader to supply the run's numbers from memory, and those describe
a different config.

The promoted config is renamed to the new strategy inside its own `strategy.name`, so the
registry and the config agree; two strategies sharing one internal name would collide on
export. The chosen name is refused when taken rather than adjusted: a strategy appearing under
a name nobody chose is worse than being asked to choose again.

**`origin_not_credible` is snapshotted at promotion time and never updated.** It is set unless
the source run is a walk-forward that itself returned `is_credible`. Promoting an *optimize*
result always sets it, even when the parent strategy's own walk-forward passed: the search
moved the parameters, so the run that passed measured different values. A verdict is about a
configuration, not about a lineage.

The warning a promoted strategy displays is **derived** from that snapshot plus the strategy's
current verdict: it is shown while `origin_not_credible` holds and the strategy's own verdict
is not `credible`. Only the strategy's own credible walk-forward against its own head clears
it, and editing that head brings it back — because the clearance was about a configuration, and
the configuration has changed. Nothing rewrites the snapshot; the past stays as it was recorded.

### 14.8 Deleting a strategy — the one exception

**[NEW — decided 2026-08-18.]** A strategy can be deleted, whole. It takes its versions, its
runs, and their captured series with it, and it cannot be undone. Nothing smaller is
deletable: there is no way to remove one version, one run, or one series, because that is the
operation that would let a surviving result be explained by a config that no longer says what
it said.

**Why this is not a soft delete.** An `archived` flag was the alternative and was rejected. It
keeps the audit trail perfectly and solves nothing the user asked for: the rows stay, the name
stays taken, and every query in the application grows a filter that some future screen will
forget — at which point an archived strategy reappears in exactly the list it was hidden from.
A user deleting a mistake wants it gone, and a delete that only pretends is the kind of
authoritative-looking half-truth §0 exists to prevent.

**The escape hatch is scoped, not opened.** The append-only triggers of §14.2 still refuse
`DELETE`. They make one exception: a transaction that has declared *which strategy it is
purging*, by setting the transaction-local `cracktrade.purge_strategy_id`, may delete that
strategy's own rows and no others. Three properties follow, and all three are pinned by tests
in `tests/api/test_schema.py`:

- an ordinary `DELETE` — a stray statement, a buggy repository, a `psql` session — sets
  nothing and is refused with the message it was refused with before;
- a purge of A cannot reach B's versions, runs, or series, however the statement is written.
  The permission names a strategy rather than granting a mode, so it is not a window during
  which everything is deletable;
- PostgreSQL reverts the setting at `COMMIT`/`ROLLBACK`, so the permission cannot outlive its
  transaction or be inherited by the next borrower of a pooled connection.

`UPDATE` is not affected at any point. During a purge the rows can go; not one of them can be
rewritten first.

**Two refusals, both 409.** The service layer decides whether a delete may proceed:

| Refused when | Because |
| --- | --- |
| a run is `queued` or `running` | the worker holds a lease on it; removing the version it is measuring would surface as an engine failure for a run that had not failed. Cancelling is the caller's move — cancelling someone's run as a side effect of a delete they might reconsider is worse than making them say so |
| a fork or a promotion descends from it | the child's lineage is a foreign key into rows this would remove, and "forked from X v3" is a historical fact, not a crumb worth leaving pointed at nothing. Deleting children is a decision to be made child by child |

Descendants are found by asking the database what points here — both the parent link and the
promoted child's `origin_run_id` — rather than by trusting the shape the create path writes.

**Deliberately not refused: having succeeded or failed runs.** That is the ordinary state of a
strategy worth deleting. A guard permitting only never-run strategies would refuse in exactly
the case the feature exists for.

The response is a receipt — the name, and how many versions, runs and series went — rather
than an empty `204`. An irreversible operation should be able to say how much it did, and a
user who expected one run and reads eleven has learned something while it still matters.

---

## 15. HTTP interface

**[NEW — decided 2026-08-16.]** The second consumer of the library, alongside the CLI (§13). It
follows the same rule: **it parses and renders, and owns no engine logic**. Where a number
appears, it is the engine's number, serialized by `cracktrade.serialize` and passed through
unaltered.

The interface contract (endpoint shapes, request/response bodies, status codes) lives in
`docs/API.md`. This section records only what is normative and would otherwise drift.

### 15.1 Result payloads are the engine's serialization

Any response embedding a run result embeds `to_dict` output verbatim, including the contractual
derived properties of §13.1 (`is_credible`, `failures`, `fold_win_rate`, `improvement_pct`,
`at_bound`, …), with non-finite floats as `null`.

The API must not recompute a verdict, a derived statistic, or a pass/fail from underlying
fields. Where the UI needs structure the engine does not yet expose — the per-check verdict
table is the case in point — the fix is a contractual property on the result model, not a
calculation in the interface. Verdict logic exists in exactly one place (§12), or it will
eventually exist in two versions that disagree.

Comparing two *configurations* is the one thing the interface computes, and it is not an
exception to the rule: a diff is a relationship between two stored documents, not a judgement
about a result (§14.4). An `optimize` result already carries its parameter moves with their
search bounds, so those are read rather than recomputed; a walk-forward carries the winning
config but no single set of bounds — each fold re-optimizes independently — so its diff reports
the moves with the bounds absent rather than inventing a range that was never searched.

Parameters are addressed in the **flattened** form the engine uses throughout
(`indicators.rsi_ind.window`), on both sides of that diff and in `searchable_parameters`. The
stored canonical `config` nests an indicator's type-specific settings under `params`; the
interface must not leak that shape into a parameter path, or one field would have two addresses
depending on which run produced it.

**[AMENDED — 2026-08-17.]** Two diffs exist, and the second is why the rule above needs a
mechanism rather than only a prohibition. `GET /strategies/{id}/diff` compares two versions of
one strategy. `POST /config/diff` compares two *configurations*, neither of which need be a
version — the promote dialog's case, where one side is what a search emitted and is not a
version of anything until the user adopts it. It refuses an invalid side with a 4xx rather than
answering 200 as `/config/validate` does: there is no half-typed state to support, and a diff
against something that is not a strategy has nothing to say.

Both sides of a configuration diff are canonicalised **through the YAML writer**, and the
comparison runs on that form. Canonicalising through the pydantic model instead satisfies the
"describe both sides the same way" requirement and violates the paragraph above, because
`model_dump` is where the `params` nesting lives — a promote dialog would name
`indicators.sma_long.params.window` while the editor offers to search
`indicators.sma_long.window`, presenting one parameter as two. The YAML form is also what the
user is reading in the pane beside the diff, so a listed change corresponds to a visible line.
A repo test pins the three surfaces to one path.

**[AMENDED — 2026-08-17.]** The rule binds the *version* diff too, and it was not being
followed there. `GET /strategies/{id}/diff` and the history timeline's change summaries read
the stored `config`, which is `model_dump` output — so they addressed a window as
`indicators.sma_long.params.window` while the other three surfaces called that same leaf
`indicators.sma_long.window`. It is the second address the paragraph above prohibits, arrived
at by reading a stored document rather than by canonicalising badly. Both now route the stored
form through the YAML writer before comparing, and **one** function answers "what changed
between these two configs" — for the timeline, for the diff, and for the save path's
nothing-changed refusal.

The diff's **YAML panes are re-dumped from the stored configs** rather than served as the
stored text, for the same reason. An imported version keeps the uploaded file's own formatting
(deliberately: reading back an import should return what the user wrote), so pairing it with a
later save set a flow-style document beside canonical block style — every line highlighted, and
a structured summary correctly reporting one moved number sitting above a pane in which nothing
could be found. `GET /strategies/{id}/versions/{v}` is unaffected and still returns the stored
text. Only the comparison canonicalises, because only the comparison needs both sides described
the same way.

A version the *current* engine can no longer validate — an indicator retired from the registry,
say — cannot be canonicalised, and is still a truthful record of what the user believed. The
comparison falls back to the stored form for that pair rather than failing, both sides or
neither: canonicalising one side alone would report every indicator parameter as removed from
one address and added at another, which reads as a rewrite of the strategy.

### 15.2 Guarantees carried into the API surface

- **One delete, and it is the whole strategy.** `DELETE /strategies/{id}` removes a strategy
  with its versions, runs and series, guarded and irreversible (§14.8). No endpoint deletes a
  version or a run in its own right, and none removes one without its strategy.

  **[AMENDED — 2026-08-18.]** Previously "No delete. No endpoint deletes a strategy, version,
  or run." Narrowed rather than withdrawn — see the note in §14.2 for what the guarantee was
  actually protecting.
- **Suppression is honest end to end.** Below `MIN_TRADES_TO_JUDGE` closed trades (§8), the
  withheld figures are not sent — not in a detail response and not in a list row. A client
  cannot render a suppressed number it was never given.

  **[CLARIFIED — 2026-08-17.]** This governs what the interface *composes*: headlines and list
  rows, which omit the keys outright. It does **not** extend to the embedded `result` blob,
  because §15.1 requires that to be the engine's serialisation passed through unaltered, and
  the two rules cannot both hold for the same bytes. §15.1 wins there: stripping keys out of a
  stored result would mean the artifact on disk and the artifact on the wire differ, and a
  client could no longer check one against the other. Below the floor the blob therefore still
  carries every figure, and `has_enough_trades_to_judge` travels with the metrics that contain
  them so a reader can tell.

  A client rendering the blob is consequently responsible for the floor, and must apply it at
  one chokepoint rather than at each figure — the web UI does this in `lib/result.ts`, which
  declines to hand a screen the metrics at all rather than handing them over with a flag asking
  politely that they not be shown. Trade *counts* stay visible on both sides of the line: a
  count is the evidence for the suppression, not a claim about performance.
- **Staleness and verdict travel with every run and strategy rendered**, derived per §14.4.
- **Optimistic concurrency on config saves.** A save states the version it was based on and is
  refused if the head has moved, because a silent last-write-wins on an append-only history
  loses an edit while appearing to succeed. A save that changes nothing creates no version.

  **[AMENDED — 2026-08-17.]** The check belongs **in the insert**, not in the service ahead of
  it. `strategy_version.version` is computed as `max(version) + 1` within the statement, and the
  service compared the head to the caller's stated base beforehand; under `READ COMMITTED` those
  are two different reads. A second saver whose insert begins after the first commits recomputes
  the maximum against the *new* head, takes the number after it, and succeeds — no collision, so
  the unique constraint never fires. Both requests are answered `201`, both declaring they
  edited v1, and the second has silently discarded the first.

  This was not a theoretical window. It was found by widening the service-side gap by about two
  milliseconds — the canonicalisation above — after which a two-thread save raced it open on the
  first attempt; before that it had sat unreproduced under twenty-five runs of the same test,
  which is exactly how a defect of this shape hides. The append now carries the expected head
  (`0` when writing a strategy's first version) and checks it in the same statement that writes,
  so either the guard sees a moved head and writes nothing, or two inserts overlap closely
  enough to compute the same number and the unique index rejects one. Both are reported as the
  same conflict; the difference between them is only how close the race was.
- **Stream discipline** (§13.2) applies to the API processes: diagnostics to stderr, structured,
  never into a response body.

### 15.3 Operational surface

**Request identity.** Every response carries `x-request-id`; every log line written while
handling that request carries the same id; every problem+json body repeats it. A user reporting
a failure has, on screen, the token that finds the exact log lines — without guessing from a
timestamp which of several concurrent requests was theirs. A client-supplied id is honoured so
a trace survives a proxy, but only after being bounded and reduced to unreserved characters: a
header is attacker-controlled input and a log file is read by tools that split on newlines. An
over-long id is *replaced*, never truncated — a truncated id is a different id wearing the
caller's.

**Health.** `GET /api/v1/health` reports database reachability and migration state, `200` when
both hold and `503` otherwise, with the reason in the body. Two consequences follow, and both
are load-bearing:

- It reads without writing. `plan` creates the ledger when it is absent, which is correct
  before migrating and wrong for a check — an observation that creates a table has changed what
  it was asked to observe. `inspect` is the read-only twin.
- It takes the connection *pool*, not a unit of work. The unit-of-work dependency borrows a
  connection before a route body runs, so a health route declaring it would fail with an
  unexplained 500 in exactly the case the endpoint exists to describe.

For the same reason the pool opens **without waiting** for its first connection: a server that
refuses to start because the database blipped cannot be asked why it is unhappy. Start-up
validation happens where it can act — `cracktrade-api serve` and `worker` both check
reachability and the migration state *before* the call that never returns, and refuse to
proceed against a half-applied schema.

**Shutdown.** On `SIGINT`/`SIGTERM` the worker stops claiming and its in-flight run ends
`cancelled` at the next checkpoint. That is a more accurate record than being killed and swept
as abandoned a minute later: an operator stopped it deliberately, and `cancelled` says so while
`engine_failure` would not. A second signal exits immediately, and the run it interrupts is
failed honestly on lease expiry (§14.5) — which is what a killed worker should leave behind.

### 15.4 Process and access decisions

| # | Decision |
| --- | --- |
| D-6 | psycopg 3 with raw SQL, no ORM. The schema is hand-written; an ORM is a second description of it. |
| D-7 | Hand-rolled forward-only migration engine (§14.6). |
| D-8 | The migration chain is the canonical DDL; no second schema file. |
| D-9 | Two processes, one codebase: API server and worker. The database is the queue; no broker. |
| D-10 | Live progress via Postgres `LISTEN`/`NOTIFY` relayed as SSE; polling is the documented fallback. |
| D-11 | FastAPI. Pydantic DTOs at the edge only; internal types are frozen dataclasses, as in the engine. |
| D-13 | The data layer is synchronous, and routes are `def` so Starlette runs them in its threadpool. One repository implementation serves both the server and the worker; an async server plus a sync worker would require two, and two descriptions of one fact eventually disagree. The `LISTEN`/`NOTIFY` relay is the exception and owns its own async connection. |
| D-12 | Separate `cracktrade-api` entry point (`serve`, `worker`, `db`). The `cracktrade` CLI is untouched. |
| D-5 | No authentication. Single user, bound to localhost. Revisit only if that changes. |

Layering, enforced by review and a repo test: `routes → services → repos → db`, no skips and no
cycles. Engine calls happen in `services/` and the worker only; repositories never import the
engine, routes never import repositories.

---

## 16. Evolutionary strategy generation

**[NEW] — decided 2026-08-18.** §9 optimizes a strategy the user wrote. This section specifies
building one from nothing: the user names a ticker and a genetic algorithm composes conditions
from a curated library, choosing structure and numbers together.

The feature's difficulty is not the search. It is that structure search is a machine for
manufacturing exactly the output the project exists to prevent — a number that looks
authoritative and is not. A parameter search of a few thousand vectors already needs §12.3's
deflation to be readable; a search that also chooses *which conditions the strategy is made of*
reaches a far larger space, and the maximum of enough draws is impressive on a random walk. So
the specification below is mostly about evidence, and the search itself is the short part.

Implemented in `src/cracktrade/evolution/`, exposed as `cracktrade evolve TICKER`.

### 16.1 What is evolved, and what is not

Evolved: the `indicators` list, the `entry` signal, and the `exit` rule.

Not evolved: `universe`, `execution`, `position_sizing` — the same three sections §9.1 never
optimizes, for the same reason. They are the user's statement of what they are trading and with
what, not a hypothesis about the market. They arrive as a **chassis**
(`evolution/genome.py:Chassis`) and are passed through unchanged.

**The ticker is never searched.** Letting evolution choose among tickers would add cross-asset
selection bias on top of the structure search §16.6 is already deflating for, and the two are
not separable after the fact. One run composes for one symbol.

### 16.2 The block library

A **block** (`evolution/blocks.py`) is one tradeable condition: the indicators it needs, bounded
ranges for their parameters and its own thresholds, and a signal expression tying them together.
Twenty-nine blocks, in a fixed order.

**Every expression in the library is written by hand.** Evolution picks blocks and moves numbers
inside them; it never composes an expression. This is the safety argument for the whole feature:
each expression is checked against §2.1's grammar by a repo test, so a genome cannot compose its
way to something the four look-ahead layers would have to catch.

**The order of `BLOCKS` is part of the reproducibility contract.** A genome stores block
*indices*, so reordering the tuple silently reinterprets every stored genome and every recorded
seed. Add to the end.

Three consequences of the library being curated, all deliberate:

- **The space is bounded and countable.** "How many distinct strategies were considered" has an
  exact answer, which is what §16.6's deflation consumes. Open-ended expression evolution would
  not have one. §16.3's variable-length condition chains multiply this space further — a genome
  may compose up to five entry conditions and, separately, up to five exit conditions — but the
  count §16.6 deflates by is still the number of distinct configurations *actually evaluated*,
  not a theoretical enumeration of the reachable space, so a larger reachable space does not by
  itself weaken the deflation.
- **There are no breakout blocks.** `rolling_max` includes the current bar, so
  `close > rolling_max(close, n)` is false on every bar of every history, and the shifted form
  the comparison actually wants is unexpressible by design (§2.1). Channel *position* stands in:
  Bollinger percent-B and Donchian's fractional position both express "near the top of the
  range" without reading a bar the decision could not have seen.
- **Blocks come in opposed pairs and carry no role.** Neither member of a pair is labelled entry
  or exit. A trend strategy enters above its average and leaves below it; a mean-reversion
  strategy does the reverse, and a library that pre-assigned roles would have excluded one of
  those families by construction.

Where two genes are only meaningful relative to each other — the fast and slow windows of a
crossover — their ranges are **disjoint** rather than constrained. Overlapping ranges would let
the search produce a genome whose "fast" average is the slower of the two: a valid strategy that
means the opposite of what its block name says, and that no reader would catch.

### 16.3 The genome

Variable-length chains (`evolution/genome.py`): one to five entry conditions, and —
independently — zero to five exit conditions, each chain joined pairwise by an
independently-drawn combinator (`&` or `|`), plus an optional stop (one kind out of
fixed/trailing/ATR, so §3.7's priority chain can never shadow a gene the search paid for), an
optional take-profit, and optional holding bounds.

A slot holds a block choice **together with** its gene values, and the slot is still the unit of
crossover — exchanging a block index without its genes would hand the child an arity that does
not match its block and numbers that mean something else. What is new relative to the genome's
original two-slot shape is that a chain's own **length** is now also something the operators
choose, not a fixed count of named positions; §16.4 covers how. Rendering folds a chain
left-associatively, re-parenthesising the running expression at every step (`((a) & (b)) | (c)`),
so the grammar's precedence trap — `&`/`|` bind more tightly than comparison — can never bite
regardless of chain length.

**Rendering is total.** Every genome the operators can produce renders to a strategy that passes
structural and semantic validation, and `tests/test_evolution.py` asserts it over the reachable
space. This is not cosmetic: an unrenderable genome would score `INFEASIBLE` and steer the
search away from a region of the library rather than reporting the bug in it.

Totality is maintained by `repair`, which fixes the two schema rules that are not expressible as
independent gene bounds — an exit with no mechanism at all gets a holding cap, and a holding
floor at or above the cap yields to it. Both repairs are deterministic; one that consulted the
random number generator would make reproducibility depend on how often it was needed.

**Thresholds inside expressions are searchable here, and are not searchable afterwards.** §9.1's
known limitation — numeric literals in a `signal:` string are unreachable to the optimizer —
does not bind evolution, which controls the rendering. The consequence is asymmetric and worth
stating: an evolved strategy's thresholds are frozen the moment it becomes YAML, so a subsequent
`cracktrade optimize` on the output tunes the indicator windows and leaves every threshold where
evolution put it.

### 16.4 The search

An elitist generational GA (`evolution/search.py`): tournament selection, per-position uniform
crossover within each variable-length condition chain, per-slot mutation, and the best few
genomes carried forward untouched and unscored. Defaults: population 40, 25 generations, 2
elites, tournament 3, crossover 0.9, mutation 0.2 per slot. A mutating condition slot either
swaps its block outright (30%) or nudges the numbers inside the block it has.

**Crossover picks a chain's length from one parent, never from neither.** A fixed-shape slot
exchanges position by position because both parents always have that position; a variable-length
chain cannot, since the child needs a length before any position-wise exchange means anything. So
one parent is chosen by coin flip as the *structure donor* — the child's chain length is exactly
that parent's own length — and then every position is filled independently, from either parent
where both have a slot there, otherwise from whichever does. A child's length therefore always
traces back to an actual parent rather than to an unconstrained recombination.

**Mutation may grow or shrink a chain by one condition, in addition to the existing content
mutation.** Equally weighted between growing and shrinking when both are legal at the chain's
current length, generalising the old toggle between a single optional slot being present or
absent — which is how the search reaches both simpler and more elaborate strategies at any length
between the bounds, not only strictly more elaborate ones. Shrinking removes a randomly chosen
existing slot, not always the newest, so no earlier slot becomes permanent once added. Nothing in
this operator resists growth, though: an extra condition essentially never hurts *in-sample*
fitness, so the GA will tend to drift chains toward the five-condition ceiling over enough
generations regardless of whether the added complexity earns its keep out-of-sample. This is an
accepted, currently unaddressed risk — countering it would mean a parsimony term in the fitness
function (§16.5), which is a separate change from the genome and operators described here.

**Hand-written rather than taken from a framework.** DEAP is untyped, which is a poor fit for a
strict-mypy codebase, and the working agreement to verify library behaviour rather than assume
it is a bad trade for two hundred lines of operators whose behaviour is this easy to assert
directly.

**Seeded and worker-count independent.** Everything stochastic comes from one
`random.Random(seed)`, and a test asserts that the same seed produces the same winner end to end
— defect D12 is not less of a defect for being evolutionary.

The search *is* parallelised, and for the same reason §9.2's `workers=-1` is safe. The GA is
generational: a generation is bred in full from the seeded RNG before any of it is scored, and no
score is read until every genome in the generation has one. That is exactly the property DE gets
from `updating='deferred'`, so a generation may be scored across a process pool and reassembled
by position with an identical result. `run_evolution` takes an optional `evaluate_batch` and
otherwise knows nothing about processes; the pool lives in `evolution/parallel.py`.

**Unlike §9.2, the counts stay exact.** The optimizer reports `counts_exact=False` under
parallelism because scipy's pool mutates copies of the fitness object and their tallies never
return. Evolution cannot accept that: `distinct_configurations` is the trial count §12.3 deflates
by, and the finalists are what §12.4 is computed across, so a count that moved with the worker
count would move a published verdict with it. The division of labour is therefore inverted —
the **parent** renders, digests and deduplicates, and the children do nothing but simulate and
return a value (`Scored`, or the failing exception's type name). `Fitness.evaluate_batch` merges
them in genome order, which is the order serial scoring would have produced, and its contract is
that the resulting state is indistinguishable from having scored each genome in turn. A test
asserts `workers=1` and `workers=2` agree on the strategy, the holdout metrics, the generation
trace, the trial count, the evaluation and failure tallies, the deflated Sharpe and the PBO.

**`workers` is an argument to `evolve()`, deliberately not a `GaSettings` field.** Search
semantics may shape the result; worker count must not, and a setting that cannot reach the
operators cannot be blamed for having done so.

**More workers is not reliably faster, and `-1` is sized from the budget rather than the core
count.** A worker spends ~2.5s importing vectorbt and numba before scoring anything, paid per
worker, while the dedup cache is served in the parent — so a converging population offers a late
generation far fewer new configurations than its size, and surplus workers idle having already
cost their start-up. Measured on 32 cores: a 40/5 search was *faster* serially (9.6s vs 13.4s at
eight workers), 100/30 ran 102s serially and 28s at eight, and 300/30 ran 290s serially, 57s at
eight and 50s at sixteen. `plan_workers` therefore allows one worker per
`EVALUATIONS_PER_WORKER` (500) of `GaSettings.budget`, capped at the core count, so a short
search gets no pool at all. An explicit count is obeyed as given. Pinning BLAS threads changed
none of these numbers; the cost is interpreter start-up, not thread contention.

### 16.5 What evolution may see

`evolution/protocol.py`. Three separations, in order of importance.

**The holdout is untouchable.** The last `holdout_fraction` of history (default 0.2) is split
off before the first genome is drawn and is evaluated exactly once, on the winner, after every
choice is final. It is a `TestWindow`; the fitness function accepts a tuple of `EvolutionWindow`
and nothing else, so handing evolution the holdout fails mypy — §2.5's discipline applied to a
second search. The holdout is always the *most recent* stretch: one taken from the middle would
be surrounded by data the search had seen, and autocorrelation alone would leak the regime.

**Fitness is a typical segment, not a total.** The evolution region is cut into `segments`
contiguous windows (default 4) and a genome scores the **median** of its per-segment objective.
A mean would let one spectacular segment carry a genome that lost money in the other three,
which is precisely the genome a structure search is most likely to find and least likely to be
right about. Four rather than three because §12.4's CSCV needs at least four slices to produce
any combinations at all.

**The trade floor is one constraint over the whole region.** Segments are short, so §9.3's floor
applied per segment would reject nearly everything for a reason that has nothing to do with the
market. It is applied once to the summed trade count and sized from the whole evolution region;
the per-segment objective is bound with `min_trades=0`. Above the floor there is still no
gradient rewarding more trades, for the reason §9.3 gives.

Every scored window carries a warm-up prefix sized from `library_warmup()` — the widest window
any block can ask for, currently 200 bars — not from the winning genome. §9.2 sizes the
optimizer's test window from the widest candidate for the same reason: a genome whose warm-up
exceeded the prefix would have its signals suppressed *inside* the scored region and quietly
lose bars it should have been able to trade.

A run where every candidate is infeasible raises rather than returning. There is no result, and
a result nobody may believe is worse than none.

### 16.6 What is reported

`EvolutionResult`. The headline is `holdout_metrics`, computed once. Beside it:

- the **benchmark**, buying and holding the same ticker across the same holdout bars;
- the **deflated Sharpe**, whose trial count is the number of **distinct rendered
  configurations**, identified by digest. Elites survive generations untouched and crossover
  rediscovers configurations, and counting those repeats as separate looks would *understate*
  the deflation — it would treat one candidate examined ten times as ten independent looks;
- the **probability of backtest overfitting**, over a segments-by-finalists matrix built from
  the distinct feasible genomes of the final population. Nothing is re-simulated: their
  per-segment Sharpes were kept during the search. Infeasible finalists are excluded, since a
  candidate that could never have been selected is not an alternative the selection passed over;
- **parameter stability**, **cost sensitivity**, and **bootstrap intervals**, all as in §12;
- the **per-segment metrics** of the winner, labelled in-sample, and never mixed into the
  checks. The search chose this strategy *because* of those numbers.

`checks`, `failures` and `is_credible` have the same shape and the same strict conjunction as
`ValidationReport`'s (§12.8), and every check is computed on the holdout. The conjunction matters
more here than there: a parameter search starts from a strategy someone believed in, while this
returns the best of a large number of guesses, so "most of the checks passed" is the *expected*
output of a search with no edge at all. Expect most runs to be NOT CREDIBLE.

Two honest caveats travel with the report rather than living only in this document:

- **The holdout is one contiguous draw**, with the single-draw weakness §9.4 rejected for
  optimization. Evolution consumes the folds, so a terminal holdout is the honest option
  available — and re-running evolution on the same ticker and reading the holdout again spends
  it, at which point the strategy has been fitted to it through the user.
- **The stability surface is not the fitness function.** It scores the objective on the
  evolution region as a single window, because no single-window objective reproduces a median
  across segments. The question it answers — does a 10% nudge destroy the result — is unchanged,
  but its numbers are objective values, not fitness values. It also perturbs only the parameters
  §9.1's discovery can reach, so it understates how many numbers the winner depends on.

`failed_candidates` counts genomes that could not be rendered or simulated. Unlike §9.2's
failure tally, **any** non-zero value is a defect in the block library or in `repair` rather
than a fact about the market, and both the log and the report say so.

### 16.7 Amendment to §12.3 — non-finite trial Sharpes

**[FIX] — found 2026-08-18.** `deflated_sharpe` computed the cross-trial variance over the trial
Sharpes as given. A candidate with a handful of trades whose returns happened to have no
variance produces an *infinite* Sharpe; `np.var` over an array containing an infinity is `nan`,
the luck threshold becomes `nan`, and the statistic is reported as `P=nan`. That renders as a
failed check rather than as a broken computation, which is the wrong kind of wrong for a number
the report treats as a verdict.

Non-finite trial Sharpes are now dropped before the variance is computed, falling back to the
estimator variance when fewer than two survive — which the result already flags as
`variance_estimated`. The defect was reachable from walk-forward too; evolution merely hits it
more often, because a segment is short enough to produce a degenerate candidate regularly.

### 16.8 Amendment to §12.4 — an uncomputed overfitting probability

**[FIX] — found 2026-08-18.** `OverfittingProbability.is_acceptable` compared the probability
against the 0.5 bar without asking whether it had been computed. Below four slices CSCV produces
no partitions, the probability stays at its initialised `0.0`, and the check reported a green
tick with `PBO 0.00` beside it — a statistic that never ran, rendered as the best possible
result. Pre-existing and reachable from a three-fold walk-forward; evolution surfaced it by
making the slice count a launch parameter.

`is_computed` is now part of the type, `is_acceptable` requires it, and both check renderings say
"not computed — too few slices". An unknown reads as a failure, never as a pass.

The API refuses fewer than four segments at launch rather than accepting a run that cannot pass
its own verdict. The engine still permits one segment: the CLI is for someone deliberately
probing the machinery, and the check tells them what it did.

## 17. Evolution as a run kind

The library-level feature is §16. This section is what the API and the web UI add on top, and it
is normative for the boundaries between them.

### 17.1 The chassis is a strategy

An evolution run is launched against an ordinary strategy and pinned to an exact version, like
every other run. It reads exactly four things from that version — the ticker, the date range, the
execution costs, and the position sizing rule — and composes everything else. `chassis_for` is the
only place that mapping exists.

Two consequences, both deliberate:

- **The base config's signals are ignored.** A chassis with an RSI crossover contributes nothing
  to the search but its market and its frictions. Starting from someone's opinion would make the
  trial count the deflation divides by describe a different search than the one that ran.
- **Staleness over-reports.** A run is stale when the strategy's head moves past the version it
  ran against, and that rule is not narrowed to the four fields evolution actually read. Editing
  an entry signal marks an evolution run stale although nothing about it changed. Over-flagging is
  the safe direction; the run view states which parts of the base config were inputs.

### 17.2 A verdict does not travel to the chassis

An evolution run computes `is_credible` and the schema permits it to (`run_credible_only_validated`
admits `walk_forward` and `evolve`). The `strategy_overview` verdict lateral deliberately does
**not**: it still reads walk-forward runs alone.

That asymmetry is the point. The composed strategy exists in the run's result, not in the chassis.
A chassis reading CREDIBLE would be claiming a verdict for signals it does not contain, and every
screen in the application would repeat it without being wrong to trust it. The composition becomes
validatable by being **promoted** into a strategy of its own — where §14.7 applies unchanged, and
its own walk-forward is the only thing that clears the warning.

An evolution run's own credible verdict does not clear that warning either. A holdout is one
contiguous draw evaluated once (§16.6); a walk-forward asks whether the strategy survives being
re-fitted and re-tested across the whole history. The weaker evidence does not retire the stronger
requirement.

### 17.3 History is sized by the library

`load_market_data` sizes `min_bars` from `library_warmup()` for an evolution run, not from
`required_warmup(strategy)`. A chassis declares almost no warm-up of its own while the search can
reach for any block in the library, the longest of which looks back 200 bars. Sizing from the
chassis would accept a window too short to evolve in and fail somewhere inside the search —
recorded as an engine fault, for what is really a history that should never have been accepted.

### 17.4 What the reporting layer must not do

- **The headline obeys the trade floor.** Below it the holdout figures are omitted from the
  payload, exactly as for a backtest (§15.2). The composition is not a measurement and survives
  suppression.
- **There is no config diff.** A chassis is not a starting point; every indicator and both signals
  are new. Rendering that as a diff reads as "look how much was changed" when the truth is "none
  of this was there". The run view shows the composed configuration whole.
- **Fold 0 is the holdout, not the run.** An evolution run captures one series bundle and it covers
  the holdout only. Anything rendering those points must say so, or it shows a partial history in
  a frame that means "the whole backtest" everywhere else.

### 17.5 What the web UI must keep separate

The run view (`EvolutionView`) is arranged around one distinction, and the arrangement is
normative because getting it wrong is not a cosmetic problem.

**The holdout leads; the segments come after the verdict, under their own heading, with the
"none of these are evidence" caption.** The segments are the selection criterion. Presented
beside the holdout they read as four more confirmations, which turns a search of N candidates
into what looks like five independent results. They are shown at all because the spread across
them is what fitness selected on, and a winner carried by one segment differs from a steady one
in a way the median cannot express.

**The trial count is stated where the reader is choosing it, not only where it is applied.** The
launch dialog's population and generations fields say that `population * generations` is the
number the deflated Sharpe divides by, so a bigger search raises its own bar. Every other run
kind's budget control trades time for thoroughness; this one also trades away credibility, and a
form showing only the runtime invites the user to turn it up.

**Evolution is excluded from the history tab by type**, not by a filter — `ComparableKind` in
`web/src/features/history/delta.ts` omits it. That screen asks how one strategy changed across
its versions; a column of evolution holdout returns across four versions is four different
strategies under one name.

**Evolution ranks last in the charts tab's default selection** (`SERIOUSNESS`), and its charts
carry a banner. Not because its evidence is weak — the holdout is genuinely unseen — but because
those curves describe a composition the strategy in the page header does not contain, drawn over
the holdout rather than the full date range every other run charts.
