# cracktrade web UI

React + TypeScript + Mantine single-page app over the cracktrade HTTP API. Charts are Apache
ECharts; the config editor is CodeMirror 6.

`docs/UI_PROMPT.md` in the repository root is the design brief this implements, and it is
normative about _why_ each screen looks the way it does. The short version:

> A number that looks authoritative and is not is the worst possible output of this product.

Concretely, that means several things this codebase does deliberately and that a well-meaning
refactor would undo:

- **Suppressed figures are absent, not hidden.** Below the trade floor the API omits the
  numbers it will not stand behind, so there is nothing to accidentally render. Do not
  reconstruct them client-side.
- **The verdict is never recomputed here.** `checks` comes from the engine; this side formats
  it. A second implementation of the verdict is a second verdict, and the two eventually
  disagree in public.
- **`unvalidated` is not a neutral state.** It means nobody has checked yet, and it is styled
  to say so.
- **Engine constants come from `/meta`.** The trade floor, the objectives, the indicator
  registry and the "what this cannot tell you" limits are served, never restated in
  TypeScript.

## Running it

The UI needs the API, the worker and Postgres. From the repository root:

```bash
docker compose up -d
uv run cracktrade-api db migrate
uv run cracktrade-api serve      # :8000
uv run cracktrade-api worker     # in another shell; runs execute here
npm --prefix web run dev         # :5173, proxies /api to :8000
```

The API is loopback-bound with no CORS by design, so the dev server proxies rather than the
server relaxing.

## Checks

```bash
npm run check
```

Typecheck (strict, plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`), oxlint
with warnings denied, prettier, and vitest. `../scripts/check.sh` runs this and the Python gate
together; that is the one to run before pushing.

## Types

`src/api/schema.gen.ts` is generated from the running server and committed:

```bash
npm run gen:api
```

Regenerate it when the API changes and commit the diff. `docs/API.md` has drifted from the
implementation in places — the OpenAPI document is the contract.

Fields FastAPI documents as open records (`headline`, `progress`, the indicator registry)
arrive as `unknown` and are narrowed by hand in `src/api/types.ts`. The verbatim engine
`result` blob is not part of the HTTP schema at all and is narrowed with runtime guards.

## Layout

| path              | what lives there                                                    |
| ----------------- | ------------------------------------------------------------------- |
| `src/api/`        | typed client, error normalisation, query keys, SSE, engine metadata |
| `src/lib/`        | pure logic, unit-tested — suppression, diffing, comparability rules |
| `src/components/` | shared UI                                                           |
| `src/features/`   | one directory per screen                                            |
| `src/charts/`     | ECharts infrastructure and the chart set                            |
