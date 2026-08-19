# Intraday fixtures

Real yfinance responses, captured once and committed so that **every intraday test runs
offline**. A test that reaches the network is a bug even when it passes: it makes the suite
depend on Yahoo's uptime, on the wall clock, and on a 60-day rolling window that silently
erases the data a test was written against.

Each file is the *raw provider shape* — timezone-aware index in exchange-local time,
capitalised OHLCV columns, `int64` volume — i.e. what `YFinanceProvider.fetch` returns, before
`cracktrade.data.loader` normalises it. They are served to the engine through `StaticProvider`.

Captured **2026-08-19** with `auto_adjust=True, actions=False, prepost=False`.

| File | Ticker | Interval | Range | Rows | What it is for |
| --- | --- | --- | --- | --- | --- |
| `sap_de_30m.parquet` | SAP.DE (Xetra) | 30m | 2026-06-26 → 2026-08-19 | 643 | The primary EU intraday fixture. 17 bars per session. Ends with a *partial* forming session (one bar on the capture day), which is what exercises incomplete-bar dropping. |
| `sap_de_1h.parquet` | SAP.DE (Xetra) | 1h | 2024-09-19 → 2026-08-19 | 4328 | Two years, the longest history Yahoo serves at this interval. Spans four DST transitions, the Dec 24/31 Xetra closures, a 5-bar Dec 30 half-day, an early close (2026-01-30), and a late open (2026-04-20). Long enough for walk-forward and evolution tests. |
| `vod_l_1h_halfdays.parquet` | VOD.L (LSE) | 1h | 2024-12-02 → 2025-01-31 | 368 | LSE half-days: Dec 24 and Dec 31 are 4-bar sessions closing at 12:30 London time while Xetra is shut outright. The fixture for the forced-close safety net. |
| `spy_30m.parquet` | SPY (NYSE Arca) | 30m | 2026-06-26 → 2026-08-18 | 481 | US contrast. 13 bars per session against Xetra's 17 — the fixture that fails any code which hardcodes a bars-per-day constant. |

## Measured facts these fixtures encode

Established by probing the live API on 2026-08-19, and pinned by
`tests/test_intraday_fixtures.py` so a yfinance upgrade that changes any of them breaks a test
rather than a backtest.

1. **Timestamps are exchange-local and timezone-aware**, labelling the bar's *open*:
   `Europe/Berlin` for `.DE`, `Europe/Amsterdam` for `.AS`, `Europe/London` for `.L`,
   `America/New_York` for US listings.
2. **Local session times are stable across DST transitions.** Xetra reads 09:00–17:30 local on
   both sides of the March and October switches; LSE reads 08:00–16:30 local. This is the
   entire reason intraday timestamps stay exchange-local rather than being converted to UTC:
   the EU switches on the last Sunday of March/October and the US on the second Sunday of
   March / first Sunday of November, so a UTC index would move EU session boundaries by an
   hour for several weeks each year.
3. **Sessions are not a fixed length.** Over two years of Xetra 1h data: 478 nine-bar sessions,
   one eight-bar (early close), one seven-bar (late open), two five-bar (Dec 30), and the
   partial capture-day session. LSE adds 4-bar Dec 24/31 half-days. Bars per session must be
   *measured* and the close time must never be assumed constant.
4. **Beyond the range limit, yfinance returns an empty frame — it does not raise.** The
   message ("The requested range must be within the last 60 days") is printed to stdout, not
   attached to an exception. 15m and 30m are limited to 60 days, 1h to 730 days. Measured
   2026-08-19 against live Yahoo: at 15m a start 59 days back is served and 60 days back is
   not; at 1h, 729 is served and 730 is not.
   The limit is on the **age of the requested range, not the size of one response**: a 24-day
   15m request sitting 61–85 days back returns zero bars. Chunking therefore cannot reach
   further back than a single request can — see spec §4.2.
5. **30m is resampled from 15m by yfinance**, so it inherits the 60-day limit rather than
   getting its own.
6. **The still-forming bar of the current session is included** in the response.
7. **Zero-volume bars cluster on the session's first bar.** 415 of 482 Xetra 09:00 bars carry
   `Volume == 0`; the price fields are populated normally. Volume-based indicators are
   therefore unreliable on the EU opening bar — see the caveat in `docs/ENGINE_SPEC.md`. This
   is a data-quality fact about Yahoo's EU coverage, not a bug in the engine, and zero volume
   is a legal value under the frame contract.
8. **The last bar of a session is shorter than the interval** at 1h: Xetra's 17:00 bar covers
   17:00–17:30. It is a real bar with real prices; nothing in the engine depends on bars being
   equal-length, and the spacing check uses the *median* precisely so that this does not
   matter.

Yahoo's EU quotes are delayed roughly 15 minutes. Irrelevant to backtesting — recorded because
the eventual live-notification work inherits it.
