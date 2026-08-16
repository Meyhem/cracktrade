# cracktrade

A trading-strategy optimization engine. A user writes a strategy in YAML, optimizes its
parameters, and decides where to put real money based on what this prints. That last clause is the
whole design constraint: a number that looks authoritative and is not is the worst possible output.

Multi-interface by design — a shared core library, a CLI on top, an HTTP API later. Everything of
substance lives in the library; interfaces only parse and render.

## Working agreements

**`docs/ENGINE_SPEC.md` is the contract.** It is normative, not a summary. If the code and the
spec disagree, one of them is a bug — decide which, fix it, and record the decision in the spec.
Never let them drift silently. It is also the only normative document: the phased build plan and
the reverse-engineering spec that preceded it were deleted once the build finished, so a decision
that is not in the spec is not recorded anywhere. `docs/AUDIT.md` is a dated review, kept as
history rather than as a live checklist.

**Verify library behaviour, never assume it.** Several defaults in vectorbt 1.0.0 are actively
wrong for this engine and were found only by probing: `signals.clean` deletes an exit that collides
with an entry, `stop_entry_price` measures from the entry bar's close rather than the fill price,
and `year_freq` is not a `from_signals` parameter at all. When behaviour matters to a result, pin
it with a test that fails on a library upgrade.

**Measure before trusting an algorithm.** The specced fixed-point iteration for holding constraints
was correct and unusably slow — it resolves one trade per pass. Tracing it is what revealed that.

**Report honestly.** If a test fails, say so with the output. If something was skipped, say that.
Prefer a named allowlist with tight bounds over loosening an assertion.

## Non-negotiable: no look-ahead bias

Four enforcement layers, described in spec §2. The short version:

- signal expressions are AST-whitelisted — `Call`, `Attribute`, `Subscript` are rejected, so
  `.shift(-1)` and `.iloc[t+1]` are not expressible;
- indicators are causal by construction, with forward-projected outputs dropped, not exposed;
- exactly one `causal_shift` in the engine, which raises on negative periods; a repo test asserts
  no other module calls `.shift`;
- a truncation-equivalence harness proves values computed on `history[:t+1]` match those computed
  on the full history, bit for bit.

Do not weaken any of these. Selection bias gets the same treatment (spec §12).

## Commands

```bash
uv run ruff check . && uv run ruff format . && uv run mypy && uv run pytest
```

```bash
uv run pytest -m causality
```

mypy runs strict; ruff has the full rule set enabled. Both must be clean, and the test suite green,
before anything is considered done.

## Git

Commit and push at the end of every completed phase, feature, or fix — no need to ask.

- Run the full quality gate above first. **Never push a red tree.**
- Commit message: a short imperative subject, then a body explaining *why*, including any design
  that was tried and rejected and the reason. End with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Update any affected spec section in the same commit as the code, never in a follow-up.
- Push to `main` (`git@github.com:Meyhem/cracktrade.git`).
- Work in progress mid-phase does not need committing; a phase boundary does.
