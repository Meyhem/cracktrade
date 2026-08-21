# Parallel prospecting ticks

**Status:** design, approved 2026-08-21. Amends ENGINE_SPEC §19.7.

## 1. The problem, measured

Prospecting runs on one core of a 32-core machine, and does so at three independent layers.

**Tick scheduling is serial by decision.** `run_forever` is one loop in one process: claim a run,
else claim one prospecting tick, release, repeat. §19.7 rejected a second worker process on the
grounds that both halves are CPU-bound and would contend. That premise is what this design
revisits: the cores are idle, so there is nothing to contend for.

**The GA inside a tick is serial by an unnoticed interaction.** `prospect_once` passes `workers`
through to `evolve`, and the default is `-1` ("choose for me"). But `plan_workers` sizes the pool
from the *budget*, never the core count: `min(cpu_count, max(1, budget // 500))`. The largest real
session ran population 50 x 10 generations = budget 550, giving `min(32, 1)` = **one worker**.
Population would have to reach 91 before a second process is handed out. All three recorded
sessions ran fully serial.

**Transfer is a serial loop** over 8-10 sibling simulations.

### Measured baseline

From `prospect_candidate.discovered_at` on the three recorded sessions:

| session | params | ticks | median tick | min | max |
| --- | --- | --- | --- | --- | --- |
| `5e3dd970` | pop 50 x 10, 30m | 154 | 22.2s | 18.4s | 31.1s |
| `febbd8bf` | pop 30 x 10, 1h | 131 | 17.3s | 13.5s | 37.5s |
| `823e0c3a` | pop 30 x 10, 1h | 16 | 18.6s | 14.2s | 22.7s |

`5e3dd970` ran 156 ticks in 58 minutes with no idle gaps (156 x 22.2s = 57.7 min), so it is a
clean 100% duty cycle on one core. The tight spread says the work is compute-bound rather than
waiting on the provider.

### Why throughput is the right lever

Discovery rate, not tick rate, is the goal. Across all 314 candidates there are **9 survivors
(2.9%)**, and the survival rate is **flat in pass depth** -- the 18th search on a ticker yields
about as often as the 1st:

| nth search on a ticker | 1-3 | 4-6 | 7-9 | 10-12 | 13-15 | 16-19 |
| --- | --- | --- | --- | --- | --- | --- |
| searches | 72 | 59 | 57 | 57 | 42 | 21 |
| survivors | 3 | 1 | 1 | 0 | 2 | 2 |

So discovery is linear in ticks/hour and indifferent to how tickers are allocated. Two
consequences. First, parallelism is the only lever that moves it. Second, cores must **not** be
spent on deeper searches per ticker: §19.1 measured a 40-fold budget increase producing no better
strategy, the rate above is flat, and §9.3's deflation threshold rises with the trial count, so a
bigger search raises its own bar while not raising its result.

Nine survivors is a thin base. It is enough to rule out depth; it is not enough to prefer any
allocation over §19.7's round-robin, which this design therefore leaves alone.

## 2. Decision: batch cursor reservation with a sliding window

The worker claims a session as it does today, then runs P ticks concurrently in a process pool
instead of one tick serially.

**Rejected: cursor slots as a leased work queue.** Each `(session, ordinal)` becomes an
individually claimable slot with its own lease, preserving "a dead worker loses one tick"
exactly. It is the more faithful design and was rejected on cost: a schema change and a second
lease mechanism, against a failure mode that round-robin already recovers from (section 6).

**Rejected: multi-process workers with a sharded universe.** `claim_session` already uses
`FOR UPDATE SKIP LOCKED` and documents that "two workers never prospect the same session", so N
worker processes would each take a different session today with zero code changes. Rejected as a
*design* because it fragments the leaderboard across N sessions and cannot speed up a single
sweep. It remains available as an operational stopgap and needs no work to use.

## 3. Concurrency model

`run_forever`'s loop shape is unchanged. `claim_and_tick` becomes `claim_and_sweep`: claim the
session (same lease, same heartbeat thread), run the pool, release.

**Reservation replaces the post-hoc cursor advance.** A new repo method

```
reserve_ordinals(session_id, owner, count) -> tuple[Reservation, ...]
```

issues one `UPDATE` advancing `cursor_index`/`passes_completed` by `count`, guarded by
`claimed_by = owner`, returning the `(ticker, ordinal)` pairs derived from the pre-update
rotation. `record_tick` correspondingly drops its cursor advance and keeps the candidate insert
and the `ticks_completed`/`ticks_failed` counters, still ownership-checked.

This split is what makes the design work: reservation becomes the serialization point, and it is
atomic and cheap, so it no longer has to share a transaction with a 22-second search.

**Sliding window, not a batch barrier.** The worker reserves P up front and dispatches P into a
`ProcessPoolExecutor`; as each future completes it writes that candidate in its own transaction
and immediately reserves and dispatches one more. A naive reserve-N/run-N/await-all would let the
18-31s spread gate every wave, costing up to ~40% at the tail. The sliding window removes that
entirely.

**P comes from measurement, not from `cpu_count`.** See section 8.

## 4. History prefetch

Every tick builds a fresh `SweepHistory` with `cache=None` and fetches its home ticker plus all
family siblings and controls -- about 11 for semiconductors. Today that is ~27 provider calls per
minute; at 12x it would be ~320. `SweepHistory`'s own docstring warns that a second fetch of the
same ticker "could return *different bars*, which would make the transfer report an average over
two histories."

**Parent-side threaded prefetch over a rolling horizon.** The sliding window of section 3 has no
discrete waves to batch over, so prefetch is defined against a *horizon* instead: the parent keeps
the next P reserved ordinals resident, computes the union of tickers their families need, and
fetches the missing ones through a bounded thread pool (the work is I/O-bound, so threads, not
processes). Each child is passed the frames for its own ticker's family and builds its
`SweepHistory` with `_loaded` pre-populated. As a slot frees and one more ordinal is reserved, the
horizon advances and usually needs no new fetches at all, because consecutive tickers in a
round-robin universe overwhelmingly share a family.

Three things fall out. Fetching stays in one process, so there is one rate-limit budget and one
place to throttle. Intra-family duplication collapses -- eight concurrent semiconductor ticks
fetch 88 histories today and 11 under this design. And ticks sharing a horizon see identical bars
for shared tickers, which is strictly more consistent than the status quo.

**Staleness is bounded by a TTL, and `last_bar_seen` is honest regardless.** A horizon entry older
than the TTL is refetched rather than reused, so a long-running sweep never searches against a
history frozen at its first tick -- the hazard §19.7 and `FrameCache` both warn about. The TTL is
set from the measurement and defaults to 60s, comfortably under one tick median. Independently of
the TTL, each candidate's `last_bar_seen` is read from the frame that tick actually used, so it
cannot overstate what the search saw whatever the cache did.

**Rejected: enabling `FrameCache`.** It is off by default deliberately and its docstring flags a
staleness hazard -- a cache outliving the session would let the sweep prospect a history that
stopped at the first tick while the leaderboard claimed to be current. Wave scope avoids this;
disk cache walks into it.

**Rejected: serial parent-side fetch.** It re-serializes the thing being parallelized.

## 5. Run priority and cancellation

§19.7 promises a user's backtest waits at most one search. Preserved by **draining, not
cancelling**: when a run is queued the worker stops reserving new ordinals, lets in-flight ticks
finish (bounded by the longest remaining, ~31s), writes them, and releases the session. Nothing
paid for is discarded and the bound is unchanged.

The same for `stop_requested`, which matches §19.7's existing rule that a stopping session "never
stops in the middle of a search whose result would be paid for and discarded". For SIGTERM, drain
with a timeout and then abandon; abandoned reservations behave as in section 6.

Cancellation reaches children through a `multiprocessing.Event` bridged to each child's
`RunControl`.

Incidentally the lease gets *more* reliable: the parent's main thread now orchestrates rather than
searches, so heartbeats are no longer competing with a multi-second numba call.

## 6. Amendment to ENGINE_SPEC §19.7

§19.7 currently states, and defends, "**A dead worker loses one tick, not the session.**" Because
the cursor advances at reservation rather than at completion, that is no longer true and the spec
must say so rather than silently disagree with the code.

The invariant becomes:

> **A dead worker loses up to P in-flight ticks, not the session.** Completed ticks are already
> durable in their own transactions, so nothing measured is lost. The abandoned tickers are
> skipped for the current pass and return on the next one -- with a 12-ticker universe and 12-way
> parallelism, roughly P ticks later. The cost is bounded and self-healing, which is what makes
> reservation acceptable in place of a per-slot lease.

Also to be recorded in §19.7: the "one worker, runs first" decision is amended. Its reasoning --
that two processes would contend for the same cores with nothing arbitrating -- is retained for
*separate worker processes*, and is not what this design does: one worker still owns the session
and arbitrates its own pool, and still checks for queued runs before every reservation.

## 7. Testing

**The headline test: parallelism must be observationally invisible.** A session run at P=1 and at
P=8 must produce bit-identical candidates per ordinal. Seeds are `session.seed + rotation.ordinal`
and nothing about a process boundary may change a result. This is the test that protects the
project's central constraint -- a number that looks authoritative and is not is the worst possible
output.

Also required:

- `reserve_ordinals` atomicity under genuinely concurrent transactions: no ordinal is ever issued
  twice.
- Reservation refused when the lease has been lost mid-flight, writing nothing.
- Pass-boundary arithmetic: a reservation spanning the end of the universe increments
  `passes_completed` correctly.
- Abandoned reservations skip their tickers and the next pass recovers them.
- Prefetched frames are identical to what a child would have fetched for itself.
- Draining on a queued run bounds the wait to one tick.

The four look-ahead layers are untouched: they live inside `prospect_once`, which crosses the
process boundary unchanged. The `causality` suite and the truncation-equivalence harness must stay
green without modification, and the repo test asserting no other module calls `.shift` still
applies.

## 8. Open questions the measurement settles

Before implementation, run N concurrent single-tick processes at N = 1, 4, 8, 16 and record wall
time per tick.

1. **What TTL does the prefetch horizon need?** Long enough that consecutive ordinals in a family
   reuse their fetches, short enough that no search runs against visibly stale bars. The default
   of 60s is a starting point, not a measurement.
2. **Does throughput scale?** 10-12x is extrapolated from single-process timings. Twelve
   concurrent numba/vectorbt processes may contend on memory bandwidth in a way one process never
   shows. If the answer is 5x, P is 6, not 12, and nothing else in this design changes.
3. **Does determinism survive?** BLAS and numba thread counts are deliberately unpinned;
   `parallel.py` records that pinning was perf-neutral but determinism was never checked. If
   bit-identity fails at P>1, `threadpoolctl` becomes a dependency and pinning becomes part of the
   child initializer.
4. **What is the search/transfer split within a tick?** Not isolated. It decides whether
   parallelizing the sibling loop is worth anything on top, which this design otherwise leaves
   alone.

Memory is not expected to bind: ~0.5 GB per vectorbt process against 115 GB free.

## 9. What this design deliberately does not do

- It does not change `PROSPECT_SETTINGS` or spend cores on larger populations (section 1).
- It does not change round-robin allocation (section 1).
- It does not touch `EVALUATIONS_PER_WORKER`, whose docstring already flags that it was measured
  against the old two-condition genome and needs re-measuring. That is separate work.
- It does not parallelize the sibling transfer loop, pending question 3 above.
