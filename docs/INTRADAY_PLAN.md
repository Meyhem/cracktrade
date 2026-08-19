# Intraday support — phased implementation plan

> **Status:** input document, not normative. `docs/ENGINE_SPEC.md` remains the only contract.
> Every phase below that changes behaviour must update the spec **in the same commit**. Per
> project convention, this plan is deleted once the build it describes is finished.

## 0. What is being built, and for whom

cracktrade currently backtests **daily bars only**, and enforces that deliberately at four
layers. This plan adds sub-daily bars — **15m, 30m, 1h** — alongside `1d`, across every run
kind (backtest, optimize, walk-forward, evolve), the API, the worker, and the web UI.

**The trader is in the EU and trades EU-listed stocks and ETFs through XTB** (manually — there
is no broker integration; cracktrade says *when*, the trader executes). This is not a detail,
it is a design driver:

- Sessions are **Xetra 09:00–17:30 CET/CEST (8.5h → 17 30m bars)**, Euronext 09:00–17:30,
  LSE 08:00–16:30 UK time — *not* NYSE's 09:30–16:00 ET (6.5h → 13 bars). **Never hardcode
  13 bars/day or any US session constant.** Bars-per-session must be measured from data.
- EU and US **DST transitions happen on different dates** (EU: last Sunday of March/October;
  US: second Sunday of March / first Sunday of November). Any conversion of intraday
  timestamps to UTC shifts session boundaries by an hour for part of the year. Intraday
  timestamps therefore stay **exchange-local** (see Phase 2).
- EU exchange calendars have their own half-days and closures (Xetra is closed Dec 24 and 31;
  LSE half-days Dec 24/31). The session logic must not assume a fixed closing time holds
  forever (see Phase 3, forced close).
- Yahoo's EU quotes are ~15-minute delayed. Irrelevant for backtesting; record it in the spec
  anyway, because the eventual notification phase will inherit it.

Decisions already made with the user (do not re-litigate):

| Decision | Choice |
| --- | --- |
| How the user declares it | explicit `universe.interval: 15m \| 30m \| 1h \| 1d`, default `1d` |
| Intervals in this pass | 15m, 30m, 1h, 1d |
| Data source | yfinance only; ranges beyond its limits refused loudly |
| Overnight positions (intraday) | forbidden: force-flat at session close, entries suppressed on the session's last bar |
| Instruments | real stocks/ETFs with defined sessions (no CFDs/forex/24h in this pass) |
| Scope | historical analysis only; live evaluation and notifications are a later phase |

### Hard constraints that shape everything

1. **yfinance intraday history limits:** 15m and 30m → last ~60 calendar days only; 1h →
   last ~730 days. ~60 days of 30m Xetra data is roughly 40 sessions × 17 bars ≈ **680 bars**.
   That is thin. The project's core value ("a number that looks authoritative and is not is
   the worst possible output") demands loud, visible honesty gating (Phase 4), not silence.
2. **The four look-ahead defence layers are untouchable:** AST-whitelisted signal grammar,
   causal indicators, the single `causal_shift`, and the truncation-equivalence harness
   (`uv run pytest -m causality`). Every phase must leave them green. The forced session-close
   exit is the one place where a naive implementation *would* introduce look-ahead — Phase 3
   spells out the causal design; do not improvise there.
3. **Quality gates:** `uv run ruff check . && uv run ruff format . && uv run mypy &&
   uv run pytest`, and `npm --prefix web run check` for the UI (`./scripts/check.sh` runs
   both). Every phase ends with all of it green, committed, and pushed. Never push red.
4. **Verify library behaviour, never assume it.** vectorbt 1.0.0 and yfinance both have
   defaults that are wrong for this engine. When a behaviour matters, pin it with a test that
   fails on a library upgrade.

---

## Phase 0 — Probe yfinance intraday and pin what you find

**Goal:** replace assumptions with measured facts before any engine code changes.

Write a throwaway probe script (do not commit it; commit its *findings* as fixtures + tests).
Fetch 15m, 30m, 1h for at least: `SAP.DE` (Xetra), `ASML.AS` (Euronext Amsterdam), `VOD.L`
(LSE), `SPY` (US, as contrast). For each, record:

1. **Actual range limits.** Ask for more than the documented limit and capture the failure
   mode — yfinance may raise, return empty, or silently truncate. The loader must convert
   whichever it is into a clear `DataUnavailableError`/validation message, never silence.
2. **Index shape.** Expect tz-aware timestamps in exchange-local time (`Europe/Berlin`,
   `Europe/Amsterdam`, `Europe/London`, `America/New_York`). Confirm whether timestamps label
   the bar **open** (they should). Confirm what the *last* bar of a Xetra session is (a
   17:00–17:30 bar? a closing-auction print at 17:30+?) and whether LSE emits a 16:30+ auction
   bar. The session-close logic in Phase 3 depends on this answer.
3. **The forming bar.** Fetch during EU market hours if possible: does the response include
   the current, still-forming bar? (Expect yes.) Phase 2 must drop it.
4. **Holes and zero rows.** Count missing bars within sessions, all-NaN rows, zero-volume
   bars. EU volume data on Yahoo can be spotty — decide from evidence whether volume-based
   indicators need a documented caveat.
5. **DST edges.** Pull a 1h range spanning late March or late October and confirm the local
   session still reads 09:00–17:30 across the switch (it should, in local time — that is the
   point of staying local).
6. **Half-days/closures.** Confirm Dec 24/31 behaviour for Xetra (closed) vs LSE (half-day)
   in the 1h data (730-day window reaches back that far).

**Deliverables:** small parquet/CSV fixtures checked into `tests/` capturing one EU ticker's
30m and 1h shapes (a few sessions each, including a DST-adjacent stretch), plus a written
summary of findings appended to this plan (or the spec section drafts). All later phases'
tests run **offline** against fixtures via `StaticProvider` — no network in the test suite.

**Definition of done:** findings written down; fixtures committed; no engine changes yet.

### Phase 0 findings — DONE (probed 2026-08-19)

Full prose write-up lives in **`tests/fixtures/README.md`**; the facts are pinned by
`tests/test_intraday_fixtures.py`. Fixtures: `sap_de_30m`, `sap_de_1h` (2 years, the full 1h
reach), `vod_l_1h_halfdays`, `spy_30m`. Read them through `tests/intraday.py`.

Confirmed as the plan assumed: exchange-local tz-aware timestamps labelling the bar **open**
(`Europe/Berlin` / `Europe/Amsterdam` / `Europe/London` / `America/New_York`); Xetra 09:00–17:30
= **17** 30m bars against NYSE's **13**; local session times unchanged across the EU's March and
October DST switches; the still-forming bar of the current session is included in the response
and must be dropped; Xetra shut Dec 24/31 while LSE trades 4-bar half-days.

**Five corrections to the plan's assumptions — later phases must use these, not the guesses
above:**

1. **Over-limit requests return an empty frame; they do not raise.** yfinance prints "The
   requested range must be within the last 60 days" to *stdout* and hands back an empty
   DataFrame. Phase 2.2 cannot catch an exception — it must turn the empty response into a
   `DataUnavailableError` whose message names the interval's limit.
2. **30m is resampled from 15m by yfinance** ("30m resampled from 15m" appears in its own
   error text), so 30m inherits the **60-day** limit rather than having its own. The plan's
   55-day margin for both is right for the right reason.
3. **Session length is not constant, and not only on the obvious holidays.** Two years of Xetra
   1h: 478 nine-bar sessions, one eight-bar (early close 2026-01-30), one seven-bar (late open
   2026-04-20), two five-bar (Dec 30, closing 13:00). This is stronger evidence for Phase 3.3's
   two-layer design than the plan had — the safety net is not an edge case, it fires several
   times a year.
4. **Zero-volume bars cluster on the EU opening bar:** 415 of 482 Xetra 09:00 1h bars report
   `Volume == 0` with prices quoted normally. Volume-based indicators are unreliable on the EU
   open. Document it in the spec; do not repair it — zero is a legal volume and inventing a
   number here is exactly the failure this engine exists to avoid.
5. **At 1h the last bar of a session is half-length** (Xetra's 17:00 bar covers 17:00–17:30;
   SPY's 15:30 likewise). Harmless — nothing depends on equal-length bars and the spacing check
   is on the median — but it means "9 bars/session" at 1h is 8.5 hours, and the annualisation
   formula counts *bars*, not hours, which is the correct thing to count.

---

## Phase 1 — Config schema: `universe.interval`

**Goal:** the YAML gains the field; everything downstream can ask a strategy for its interval.

Files: `src/cracktrade/config/models.py`, `src/cracktrade/strategy.py`,
`src/cracktrade/validate/` (semantic validators), tests.

1. Add an `Interval` StrEnum to `config/models.py`: values `"15m"`, `"30m"`, `"1h"`, `"1d"`.
   Add `interval: Interval = Interval.D1` to `Universe`. Default keeps every existing
   strategy and example valid — this is deliberate backwards compatibility; do not make the
   field required.
2. Expose helpers on the enum (single source of truth — no scattered if-chains):
   - `pandas_freq` → `"15min"`, `"30min"`, `"1h"`, `"1D"`. **Warning:** `"30m"` is *not* a
     valid pandas offset alias, and pandas 2.x deprecated `T`/`H`; use `min`/`h`. Getting
     this wrong changes every time-based vectorbt metric silently.
   - `bar_timedelta` → `pd.Timedelta` for spacing checks.
   - `is_intraday` → bool.
   - `max_lookback` → the yfinance limit *with safety margin*: 15m/30m → **55 days**, 1h →
     **700 days**, 1d → unlimited. Margin because Yahoo's cutoffs move within the day; a
     strategy that validates at 09:00 must not fail at 17:00.
3. Range validation: a `model_validator` (or semantic validator) rejecting
   `start_date < today − max_lookback` for intraday intervals, with a message that states the
   provider limit and the earliest permitted date. "Today" here must be computed in **UTC**
   with the margin absorbing the difference — do not reach for exchange timezones at config
   level (the config layer doesn't know the exchange yet).
4. Holding fields: add `min_holding_bars` / `max_holding_bars` (int ≥ 1) to `ExitRule`.
   Semantic rules, enforced with clear messages:
   - `_days` fields valid **only** when `interval == 1d` (on daily, days ≡ bars — map the
     existing `_days` fields to the bar-denominated internals unchanged);
   - `_bars` fields valid for any interval; specifying both `_days` and `_bars` for the same
     bound is an error;
   - existing daily strategies with `_days` keep working byte-for-byte.
5. `strategy_warnings` additions where applicable (shadowed-stop pattern is the template).

**Spec:** §3.3 (universe), §3.x (exit rule fields) updated in the same commit.

**Definition of done:** old YAMLs parse unchanged; an intraday YAML with an out-of-range
`start_date` is refused at *validation* time (before any download) with an actionable
message; gates green; committed.

### Phase 1 outcome — DONE, with one deliberate deviation

Item 3 above (reject `start_date < today − max_lookback` at parse time) was **not implemented
as written**, because it would have made a stored strategy stop parsing on a timer: the API
re-parses stored YAML on every read, so a saved 30m strategy would have become unreadable — and
taken the detail view of every run it produced with it — 55 days after being written. Replaced
by three checks that together are stricter, not weaker:

1. **Parse time, deterministic:** the range's *width* must not exceed the interval's reach.
   Catches the mistake that actually happens (switching an existing multi-year daily strategy
   to intraday) and never goes stale.
2. **Warning:** `strategy_warnings` emits a `universe.start_date` warning when the range sits
   further back than the provider still serves, naming the earliest usable date. This is what
   the editor shows.
3. **Fetch time, loud:** Phase 2.2's `DataUnavailableError` naming the limit.

**Phase 6 must mirror the width check client-side, and surface the warning — not reimplement a
client-side "too old" hard error.** Rationale is recorded in spec §3.3.1.

Also landed: `ExitRule.min_holding` / `max_holding` properties return the bound in bars from
whichever spelling declared it, so `holding_bounds` and everything downstream stay
interval-agnostic. `_days` and `_bars` for the *same* bound is an error; for different bounds it
is fine.

---

## Phase 2 — Data layer: fetch, normalise, cache intraday bars

**Goal:** `load_history` returns a valid intraday `MarketData` for an EU ticker.

Files: `src/cracktrade/data/provider.py`, `loader.py`, `contract.py`, `cache.py`, new
`src/cracktrade/data/sessions.py`, tests.

### 2.1 Contract (`contract.py`)

- `MarketData` gains `interval: Interval` and `timezone: str | None` (IANA name, e.g.
  `"Europe/Berlin"`; `None` allowed for `1d` where it never mattered). `slice`/`head` must
  propagate both — the truncation harness and walk-forward splitter go through them.
- The index stays **timezone-naive**, but the *meaning* changes for intraday: naive
  **exchange-local** wall-clock time, with the zone carried in `timezone`. Record this
  loudly in spec §4.1. Rationale (spell it out in the spec): converting to UTC shifts EU
  session boundaries by an hour across the EU's DST dates, which differ from the US's; every
  downstream consumer (session grouping, charts, eventually notifications) wants wall-clock
  exchange time. Wall-clock local time is unique and monotonic for EU/US equity sessions —
  the DST fall-back hour (02:00–03:00) is outside trading hours. Keep the existing
  uniqueness + monotonicity checks as the enforcement of that assumption.
- `validate_frame` grows an optional interval-aware spacing check (or this stays in
  `require_interval`, Phase 3 — pick one home, do not duplicate).

### 2.2 Provider (`provider.py`)

- `MarketDataProvider.fetch` gains `interval: Interval`. Pass `interval.value` to
  `yfinance.download` — yfinance accepts `15m/30m/1h/1d` spellings directly. Pass
  **`prepost=False` explicitly** (pinned, like `auto_adjust`): regular session only, no
  pre/after-market, which is also what XTB's stock/ETF trading gives the trader.
- The `end + 1 day` inclusive-end adjustment stays as is.
- Translate the limit-exceeded failure mode measured in Phase 0 into
  `DataUnavailableError` with a message naming the limit. This is the backstop; Phase 1's
  validation should normally catch it first.
- `StaticProvider` gains the `interval` parameter (fixture frames are keyed per interval).

### 2.3 Loader (`loader.py`) — the trap-dense file. Read the current code before editing.

- **`_normalise_index` currently does `tz_convert("UTC").tz_localize(None)` then
  `.normalize()`. `.normalize()` truncates timestamps to midnight — applied to intraday data
  it silently destroys every bar time and collapses the day to duplicate timestamps.** For
  intraday: capture `str(index.tz)` into the `timezone` field, drop the tz with
  `tz_localize(None)` **without converting to UTC** and **without `.normalize()`**. The
  existing UTC+normalize path remains for `1d` byte-for-byte — daily results must not change.
- **`_drop_incomplete_bar`** currently drops "today's" bar by UTC date. For intraday, drop
  the trailing bar whose close (`timestamp + interval`) has not yet passed in the
  **exchange's** timezone (use the captured zone; "now" via `datetime.now(zone)`). The
  determinism guarantee shifts from "same history per UTC day" to "same history per closed
  bar" — update the docstring and spec §4.3. The `today` test-override parameter becomes a
  `now` override.
- **Forward-fill must become session-aware.** Today's `ffill()` is global; on intraday data
  a hole at the open would be filled from **the previous day's close, across the overnight
  boundary** — inventing a bar that pins the whole overnight gap onto a fabricated print, on
  which a stop could then fire. Rule: group by local calendar date and ffill **within each
  session only** (`frame.groupby(index.normalize()).ffill()` shape — but note yfinance
  usually *omits* untraded bars rather than emitting NaN rows, so per Phase 0 findings this
  path may be nearly dead for intraday; implement it correctly anyway). Rows still NaN after
  within-session fill (e.g. a hole at a session open) are **dropped, not filled** — a
  missing opening bar must not be synthesised from yesterday. The `filled` provenance mask
  must remain aligned after the drops; extend the existing reindex logic carefully and test
  it.
- Do **not** reindex onto a synthetic complete session grid. The engine tolerates missing
  bars (real halts exist); the spacing check uses the median precisely so gaps don't break
  it.
- `load_history` threads `interval` from `strategy.universe` through `_fetch` to the
  provider and into `MarketData`.

### 2.4 Sessions (`data/sessions.py`, new)

Small, pure, heavily-tested utilities used by Phase 3:

- `session_ids(index) -> np.ndarray` — integer id per bar, grouped by local calendar date.
- `bars_per_session(index) -> pd.Series` — bar counts of *completed* sessions.
- `median_bars_per_session(index) -> int`.
- Everything here is a pure function of the (exchange-local) index — no wall clocks, no
  network, no state.

### 2.5 Cache (`cache.py`)

- Interval joins the key: `f"{provider}|{ticker}|{interval}|{start}|{end}"`. This changes
  digests and orphans existing daily entries — acceptable (it is an input cache), note it in
  the commit body.
- **Nuance to document in the module docstring:** for an intraday range whose `end` is
  today, the cached raw frame freezes mid-session; later runs the same day reuse it and see
  fewer bars than a fresh fetch would return. Same behaviour as daily today, but far more
  visible intraday. Acceptable for v1 — but say it out loud in the docstring, and have the
  UI's default date ranges end *yesterday* for intraday (Phase 6) so the common path never
  hits it.

**Spec:** §4.1, §4.3 rewritten for interval-awareness in the same commits.

**Definition of done:** `load_history` on the Phase 0 fixtures returns valid intraday
`MarketData` with correct timezone metadata, session-safe filling, and a green causality
suite; daily behaviour bit-for-bit unchanged (existing tests prove it); gates green;
committed.

---

## Phase 3 — Backtest engine: annualisation, session close, datetimes

**Goal:** `run_backtest` produces honest numbers on intraday bars. The hardest phase; do not
rush it, and touch nothing in `signals/` grammar or the `causal_shift` contract.

Files: `backtest/portfolio.py`, `metrics.py`, `results.py`, `runner.py`, `benchmark.py`,
`holding.py`, new `backtest/sessionclose.py`, `domain/__init__.py`, `serialize.py`,
`settings.py`, tests.

### 3.1 Interval gate (`portfolio.py`)

Replace `require_daily_bars(data)` with `require_interval(data)` which checks the median bar
spacing against `data.interval.bar_timedelta` (tolerance: exact match on the median; the
median is robust to overnight/weekend gaps). Keep the two informative error messages'
spirit: say what was found, what was declared, and why it matters. A daily strategy fed
intraday bars (or vice versa) must still die loudly — this check is what stands between the
user and a silently mis-annualised Sharpe.

### 3.2 Annualisation — no more constants

`FREQ = "1D"` and `YEAR_FREQ = "252 days"` stop being module constants and become a small
frozen dataclass computed once per run, e.g. `Calendar(freq: str, periods_per_year: float)`:

- `1d`: `freq="1D"`, `periods_per_year=252.0` — unchanged, and existing daily metric tests
  must pass untouched (they are the regression net).
- intraday: `freq=interval.pandas_freq`,
  `periods_per_year = 252.0 × median_bars_per_session(index)`. **Measured, not assumed** —
  17 bars/session on 30m Xetra, 13 on NYSE, and the number is per-*ticker*. Record the
  formula in spec §7.5.
- `year_freq` for vectorbt must be expressed as a timedelta-like string; compute it as
  e.g. `f"{365.25 * bars_per_year_in_freq_units}..."` — concretely: vectorbt accepts
  `year_freq=pd.Timedelta(...)`; derive it as
  `pd.Timedelta(interval.bar_timedelta * periods_per_year)`. Pin with a test that a flat
  1%-per-bar synthetic intraday series annualises to the analytically expected figure —
  do not eyeball it.

Thread the calendar through every consumer of the old constants (grep for `FREQ`,
`YEAR_FREQ`, `TRADING_DAYS_PER_YEAR`):

- `metrics.per_period_risk_free` → exponent `1/periods_per_year`.
- `metrics.extract_metrics` accessor call → the calendar's freq/year_freq.
- `metrics._ROLLING_YEAR` (worst-rolling-12m window) → `round(periods_per_year)`. On a
  60-day intraday history this window exceeds the data and the stat correctly returns
  `0.0` — leave that; the UI will grey it out (Phase 6).
- `metrics.yearly_returns` — works as-is (groups by calendar year).
- `benchmark.py` — `periods = float(YEAR_FREQ.split()[0])` breaks; take periods from the
  calendar.
- `evolution/runner.py::_per_period` — `sqrt(TRADING_DAYS_PER_YEAR)` →
  `sqrt(periods_per_year)`; the deflated-Sharpe machinery is calibration-sensitive, so rerun
  its tests attentively (Phase 4).
- `settings.TRADING_DAYS_PER_YEAR` stays for the daily path; nothing intraday may import it.

### 3.3 Forced session-close exit (`backtest/sessionclose.py`, new) — **the causality-critical piece**

Semantics (record verbatim in a new spec section):

> An intraday strategy never holds overnight. Exits are forced on the last bar of each
> session; entries are suppressed on that bar (an entry signal there would fill at the next
> session's open — the very overnight carry we forbid).

**The trap:** "last bar of the session" naively means "no later bar exists on this date" —
which is a fact about the *future* of bar *t*, i.e. look-ahead. The truncation-equivalence
harness will catch it: truncate history mid-session and every mid-day truncation point
becomes a phantom "session close". **Do not implement it that way.**

**Causal design (two layers, both pure functions of the past):**

1. **Learned close.** For bar `t`, the expected session-close bar time is the **latest
   bar-open time observed among the most recent N completed sessions** (N = 5; a completed
   session at time `t` is one whose calendar date is strictly before `t`'s date — knowable
   at `t`). If bar `t`'s time equals the learned close time → force exit on `t`, suppress
   entry on `t`. First sessions of the history, with nothing learned yet, force-exit on
   nothing — the safety layer covers them.
2. **Safety net at next open.** If a position survives a session boundary anyway (half-day
   that closed earlier than learned — e.g. LSE Dec 24 — or the learn-nothing warm-up), force
   exit on the **first bar of the new session** (bar whose local date differs from the
   previous bar's — a fact about the *past*, hence causal). The trade record then shows the
   overnight carry honestly instead of hiding it. Count these into the result as
   `overnight_carries` (a new result field): zero is the healthy value; a non-zero value on
   an EU half-day is *visible*, matching the project's honesty principle.

Both signals are computed on the exchange-local naive index via `data/sessions.py` and
composed with the strategy's own signals **after** the signal layer's causal shift, as
plain boolean masks: forced exits OR-ed into exits, last-bar mask AND-NOT-ed onto entries.

**Interaction warnings:**

- `holding.py`'s `holding_signal_nb` **suppresses exits** to enforce `min_holding_bars`. A
  forced session-close exit must **never** be suppressed. Pass the forced-exit mask as a
  separate signal-function argument that bypasses the min-holding gate (extend
  `holding_signal_nb`'s signature), and add a semantic validation error for
  `min_holding_bars` values that could span a session (≥ shortest plausible session, e.g.
  ≥ 8 bars on 1h Xetra) so the user hears about the conflict at validation, not in a
  surprising backtest.
- `simulate` delivers one decision per bar through `signal_func_nb`, so vectorbt's
  entry/exit conflict resolution is never reached (existing docstring) — the known
  `signals.clean` collision quirk therefore cannot bite here, but **pin a test anyway**: a
  strategy whose exit signal fires on the same bar as the forced close must exit exactly
  once.
- Stops (`sl/tp/trailing`) keep their per-bar pessimistic conventions unchanged — an
  intraday bar hides intrabar sequence exactly like a daily bar does; the spec §2.6 wording
  generalises, it does not change.

**Causality proof:** extend the truncation-equivalence harness (`-m causality`) with
intraday cases whose truncation points fall **mid-session**, **on a learned close bar**, and
**on a session's first bar**. Bit-for-bit equality, as everywhere else.

### 3.4 Domain datetimes (`domain/__init__.py`, `serialize.py`)

`Series.dates`, `Trade.entry_date/exit_date`, `DataWindow.first_bar/last_bar`,
`FoldSummary.first_test_bar/...` etc. are `datetime.date` — intraday trades at 09:30 and
11:00 would collapse onto one date. Change these fields to `datetime` uniformly:

- `serialize.py` already ISO-formats `date`; `datetime` is a `date` subclass, so
  `isinstance(value, date)` catches both — but verify the emitted string carries the time
  (`isoformat()` does) and **pin the two shapes with a test**: daily runs must keep emitting
  bare `YYYY-MM-DD` (construct dates as `date`, never midnight `datetime`, on the daily
  path) so existing stored run results and the UI's date parsing stay compatible.
- `metrics.extract_trades` (`index[...].date()` calls) → keep `.date()` on the daily path,
  pass through `.to_pydatetime()` intraday. `avg_holding_days` and `Trade.holding_days` are
  **bar counts** already (`exit_idx − entry_idx`); rename to `avg_holding_bars` /
  `holding_bars` throughout domain, serializer, API schemas and UI — an "avg holding days:
  5" reading that means "5 × 30min" is precisely the kind of authoritative-looking lie the
  README forbids. This is a breaking API field rename; it shows up in the
  `schema.gen.ts` diff (Phase 5), which is the intended review mechanism.

### 3.5 Runner (`runner.py`, `results.py`)

Swap `require_daily_bars` → `require_interval`, build the `Calendar` once, wire
`sessionclose` masks in for intraday strategies, thread `overnight_carries` into the result.

**Definition of done:** an intraday fixture backtest runs end-to-end; every position closes
same-session (assert on trade records); a half-day fixture produces exactly one
`overnight_carry`; annualisation matches analytic expectation on synthetic data; **daily
snapshots unchanged**; full causality suite green including new mid-session truncations;
gates green; committed.

---

## Phase 4 — Optimize, walk-forward, evolve: thin-data honesty

**Goal:** all search machinery runs on intraday bars and *says so* when the data is too thin
to trust.

Files: `optimize/windows.py`, `optimize/runner.py`, `optimize/objective.py`,
`evolution/runner.py`, `api/worker/execute.py` (result plumbing), tests.

1. **Windows are already bar-denominated** (`split`, `train_fraction`, `warmup`,
   `min_test_bars=30`) — the mechanics carry over. But 30 bars of 30m ≈ **1.8 Xetra
   sessions**, a meaningless test window. Make the floor session-aware: intraday
   `min_test_bars = max(30, 5 sessions × median_bars_per_session)`. Same treatment for
   walk-forward fold sizing — a fold's test window below ~5 sessions is refused with a
   message that says how much history would be needed.
2. **Trade floors** (`DEFAULT_TRADE_FLOOR`) are per-window trade counts — re-examine against
   intraday trade frequency but do not loosen; the floor is a defence, and 60 days of 30m
   producing 3 trades is exactly the situation it exists to flag.
3. **Deflated Sharpe / evolution:** `_per_period` fix from Phase 3.2; rerun the evolution
   calibration tests. `library_warmup()` is bar-denominated — verify it is sane at 30m (a
   200-bar warmup is ~12 Xetra sessions, fine) and that the Phase 1 range validator's
   arithmetic still guarantees enough bars post-warmup.
4. **`history_limited` flag.** Every run result gains `history_limited: bool` plus a short
   `history_note: str | None` ("40 sessions of 30m bars is below the 120-session threshold
   for a trustworthy walk-forward; provider limit is ~60 days for this interval — consider
   1h, which reaches back ~2 years"). Thresholds (put them in the spec, tune there):
   backtest/optimize → flag below ~60 sessions; walk-forward/evolve → flag below ~120
   sessions. **Flag, never block** — but the flag must be impossible to miss in the UI
   (Phase 6). On 15m/30m this flag will be on for effectively every walk-forward; that is
   truthful, and 1h (700-day reach) is the honest recommendation the note carries.
5. Worker: thread the new fields through `execute.py` into the result payload (JSONB — no
   DB migration needed).

**Definition of done:** all four run kinds succeed on 1h EU fixture data; walk-forward on a
30m fixture carries `history_limited: true`; evolution deflation tests green; gates green;
committed.

### Phase 4 findings — DONE

Six deviations from the plan above, all recorded in the spec.

1. **`history_limited` / `history_note` became one object**, `HistoryScope(interval, sessions,
   bars, limited, note)`, on every result as `history`. Two loose parallel fields would have let
   a client render the note without the flag, or the flag without the interval it is about. Spec
   §12.11.
2. **Evolution was broken on intraday before this phase, in two ways neither of which the plan
   anticipated.** `Chassis` had no `interval` at all, so every evolved strategy came back daily
   whatever was asked for; and `mapping()` emitted `min/max_holding_days`, which Phase 1 refuses
   outright on an intraday strategy, so the first genome to draw a holding cap would have failed
   to parse. The genome's own fields are now named `min_holding_bars`/`max_holding_bars` too —
   they were always bar counts.
3. **`repair` gained a session-aware clamp on the minimum holding period.** A Xetra hour is nine
   bars against a gene drawn to twenty, so most genomes drawing a floor rendered to a strategy
   §7.6 refuses — and the search logged the losses as *a defect in the block library*. Clamped
   after the draw rather than by narrowing the gene, so a daily search is bit-for-bit unchanged.
4. **The evolution segment floor needed the same session treatment as the test window**, which
   the plan did not list. Left at 60 bars, an evolution over eight weeks of 30-minute history
   divides successfully and returns a strategy chosen between thousands of structures on a
   fortnight of market. The floor is now 60 *sessions*, which refuses it. The practical
   consequence: evolution is available at 1h and 1d, not at 15m or 30m.
5. **`cracktrade evolve` gained `--interval`, and its `--start` default is derived from it.**
   The plan treated the CLI as out of scope, but a fixed twelve-year default is wider than the
   provider reach at every intraday interval, so the command would have been unusable without an
   option the help text does not mark as mandatory.
6. **`library_warmup()` verified sane at 30m** as the plan asked: 200 bars is ~12 Xetra sessions.
   It is not the binding constraint at any interval — the segment floor in (4) is.

---

## Phase 5 — API and worker plumbing

**Goal:** the HTTP surface knows about intervals; the generated client types make every
change reviewable.

Files: `api/schemas/`, `api/routes/`, `api/worker/execute.py`, then
`web/src/api/schema.gen.ts` regeneration.

1. Strategies travel as YAML text — **no strategy-table migration**. The `interval` reaches
   the API as part of the config; the validate endpoint returns Phase 1's new
   errors/warnings automatically.
2. Surface `interval` in run/strategy summary payloads (it comes from the stored config;
   expose it so lists and run views can label bars without parsing YAML client-side).
3. Result schema additions: `history_limited`, `history_note`, `overnight_carries`, renamed
   `avg_holding_bars`/`holding_bars`, datetime-bearing series/trade timestamps.
4. Regenerate the client types with the server running (`npm --prefix web run gen:api`, dev
   stack via the `dev` skill). **The `schema.gen.ts` diff is the review artifact** — read
   it; every changed field must be one this plan intended. Commit it with the server change.
5. `docs/API.md` is already noted as drifting; touch the sections you change, but the
   OpenAPI document remains the contract.

**Definition of done:** OpenAPI reflects all changes; regenerated types compile under the
web gate; both gates green; committed.

---

## Phase 6 — Web UI

**Goal:** the trader can create, run, and *correctly read* an intraday strategy.

Files: `web/src/features/strategies/ComposeStrategyModal.tsx` (+ `NewStrategyModal`,
`ImportStrategyModal` where relevant), `features/runs/` (view, headline, columns),
`features/charts/`, `features/glossary/`, tests.

1. **Compose flow:** a "Bar interval" select — `15m`, `30m`, `1h`, `1d (daily)` — with an
   inline hint per choice: "15m/30m — data reaches back ~55 days only", "1h — reaches back
   ~2 years", and for all intraday: "positions are closed at session end; timestamps are in
   the exchange's local time". Default stays `1d`. Default date range becomes
   interval-dependent: intraday defaults to `max_lookback` ending **yesterday** (see the
   Phase 2.5 cache nuance); daily keeps its current years-back default.
2. **Client-side range validation** mirroring Phase 1's limits (server remains the
   authority; the client check only saves a round-trip). Message text should name the
   earliest permitted date, like the server's.
3. **Charts:** the x-axes are ECharts `type: 'time'` fed ISO strings — datetimes work
   unchanged. Overnight/weekend gaps will render as flat empty stretches; **accept this in
   v1** (a session-collapsing category axis is a cosmetic follow-up, not this pass). Verify
   tooltips show date+time for intraday. The worst-rolling-12m and yearly-return charts
   grey out / annotate when the stat is `0.0`-because-insufficient-window rather than
   plotting a misleading zero.
4. **History-limited banner:** when `history_limited` is true, the run view shows a
   prominent warning banner with `history_note`, styled like an error, not a footnote. This
   is the UI face of the project's core principle — do not soften it.
5. **`overnight_carries`:** show on the run view when non-zero ("2 positions carried
   overnight due to early session closes — see trades"), invisible when zero.
6. **Labels:** everything that said "days" and now means bars says "bars" ("Avg holding:
   14 bars"); trade tables render times for intraday. Glossary entries: *bar interval*,
   *session close*, *history-limited*, *overnight carry*.
7. Vitest coverage: modal validation per interval, series parsing with datetime strings,
   banner rendering. `npm --prefix web run check` green.

**Definition of done:** full flow in the browser against the dev stack — compose a 30m
`SAP.DE` strategy, run a backtest, see intraday timestamps, the forced-close behaviour in
trades, and the history-limited banner on a walk-forward; both gates green; committed.

---

## Phase 7 — Spec, examples, cleanup

**Goal:** the paper trail matches the code, and this plan retires.

1. Sweep `docs/ENGINE_SPEC.md` for every section the phases touched (§2.6 wording
   generalised, §3.3, §3.7/3.x exit fields, §4.1, §4.3, §7.2, §7.5, §8, §12 thresholds, new
   session-semantics section) — most updates should already have landed with their phases;
   this sweep is verification, not the mechanism.
2. New example: `examples/sap_intraday.yaml` — `SAP.DE`, `interval: 30m`, a short-window
   momentum entry, session-close semantics implicitly on. Comment the data-limit constraint
   inline in the YAML.
3. `README.md` / `docs/API.md` touch-ups where they mention "daily".
4. Delete `docs/INTRADAY_PLAN.md` (this file) — per project convention, finished build
   plans go; decisions live in the spec or nowhere.

---

## Standing warnings — read before every phase

- **Only `causal_shift` may shift.** A repo test asserts no other module calls `.shift`.
  Session masks must be computed by grouping/comparison, not by shifting series — if you
  need "previous bar's date", derive it from index positions, not `.shift()`.
- **Never weaken** the AST whitelist, the causal-indicator layer, or the truncation
  harness's exact equality. If the harness fails, your change is wrong — not the harness.
- **`uv run pytest -m causality` after every engine phase**, full gates before every commit,
  `./scripts/check.sh` before every push. Never push red. Commit message: imperative
  subject, a body that says *why* (including rejected designs), and the
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer.
- **Daily behaviour is a frozen regression surface.** Any diff in existing daily test
  expectations means you broke compatibility — stop and rethink; do not update the
  expectation.
- **vectorbt 1.0.0 quirks already paid for** (do not rediscover them): `year_freq` is a
  per-metric-call argument, not a `from_signals` parameter; `stop_entry_price` defaults to
  measuring from the entry bar's close, the engine pins `'fillprice'`; `signals.clean`
  deletes exits that collide with entries (the engine's `signal_func_nb` path sidesteps it —
  keep it that way).
- **pandas offset aliases:** `"30m"`/`"1h"` YAML spellings are user-facing only; pandas
  wants `"30min"`; `T`/`H` are deprecated. One mapping, in `Interval`, nowhere else.
- **EU specifics, again:** exchange-local naive timestamps; DST divergence from the US;
  17-bar Xetra days; Xetra closed Dec 24/31 while LSE half-days; ~15-minute delayed EU
  quotes on Yahoo; volume quality per Phase 0 findings. When in doubt, the fixture from a
  real EU ticker is the arbiter — not intuition, and not US-centric documentation.
- **Tests run offline.** All intraday tests use Phase 0 fixtures through `StaticProvider`.
  A test that hits the network is a bug even when it passes.
