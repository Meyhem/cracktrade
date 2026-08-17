import createClient from 'openapi-fetch'
import type { paths } from './schema.gen'
import { networkError, toApiError } from './errors'

/**
 * The one HTTP client.
 *
 * Paths and payloads are typed from the server's own OpenAPI document (`npm run gen:api`),
 * so a route that changes shape breaks the build rather than the screen. Errors are
 * normalised in middleware so no caller ever inspects a status code.
 *
 * The generated paths already carry the `/api/v1` prefix, so the base is the origin: the
 * dev server proxies that prefix straight through to the API. The origin is spelled out
 * rather than left relative because `fetch` outside a browser — jsdom under vitest — rejects
 * a relative URL, and a client that only works in one of the two is a client whose tests
 * prove nothing.
 */
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // Resolved per call rather than captured when this module loads. openapi-fetch otherwise
  // snapshots `globalThis.fetch` at construction, which freezes in whatever was installed at
  // import time — under test that is the real one, and every mocked request would quietly go
  // to the network instead.
  fetch: (request) => globalThis.fetch(request),
})

api.use({
  async onResponse({ response }) {
    if (!response.ok) throw await toApiError(response.clone())
    return response
  },
  onError({ error }) {
    return networkError(error)
  },
})

/**
 * Unwrap an openapi-fetch result.
 *
 * The middleware above throws on every non-2xx, so `data` is present whenever this is
 * reached. The check remains because the types do not know that, and a silent `undefined`
 * reaching a chart is exactly the class of bug this app exists to avoid.
 */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.data === undefined) {
    throw result.error instanceof Error
      ? result.error
      : new Error('The API returned no body where one was required.')
  }
  return result.data
}
