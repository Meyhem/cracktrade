# cracktrade

A deterministic, look-ahead-free swing-trading backtester and optimizer for single-ticker,
YAML-defined strategies on daily OHLCV data.

The engine is a library. Interfaces are consumers of it: a CLI today, an HTTP API later. All
logic lives in `src/cracktrade/`; interfaces only parse input and render typed results.

## Status

Under construction, phase by phase. See [PLAN.md](PLAN.md) for the phase list and the decisions
behind it, and [docs/ENGINE_SPEC.md](docs/ENGINE_SPEC.md) for the normative engine contract.

## Design commitments

- **No look-ahead bias, structurally.** Signal expressions are AST-whitelisted so no method call
  or index is expressible; indicators declare warm-up and causality; one alignment helper that
  refuses negative shifts; and a truncation-equivalence harness that proves values at bar *t*
  do not change when future bars are appended. See `ENGINE_SPEC.md` section 2.
- **Reproducible.** Seeded search, worker-count-independent results.
- **Honest numbers.** Parameters are fit and variants selected on a train window; headline
  metrics are reported on a test window that is touched exactly once.
- **No silent zeros.** Failures raise; they are never converted into a plausible-looking result.

## Development

```bash
uv sync --all-groups
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run mypy
```
