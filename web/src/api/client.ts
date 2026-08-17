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
 * dev server proxies that prefix straight through to the API.
 */
export const api = createClient<paths>({ baseUrl: '/' })

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
