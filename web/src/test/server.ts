import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { strategyRow, testMeta } from './fixtures'

/**
 * The HTTP boundary, stubbed for tests only.
 *
 * The app itself never uses mocks — it is built against the real API — but a component test
 * that reaches the network is a test that fails for reasons unrelated to the component. Only
 * this directory knows msw exists.
 */
export const server = setupServer(
  http.get('*/api/v1/meta', () => HttpResponse.json(testMeta)),
  http.get('*/api/v1/strategies', () =>
    HttpResponse.json({
      strategies: [strategyRow()],
      totals: { all: 1, credible: 0, not_credible: 0, unvalidated: 1, never_run: 0 },
    }),
  ),
  http.get('*/api/v1/runs', () => HttpResponse.json({ runs: [], total: 0 })),
)

/** The API's own error envelope, for tests that assert on failure paths. */
export function problem(init: {
  status: number
  slug: string
  title: string
  detail: string
  errors?: { path: string; message: string; line?: number; suggestion?: string }[]
}) {
  return HttpResponse.json(
    {
      type: `https://cracktrade.invalid/errors/${init.slug}`,
      title: init.title,
      status: init.status,
      detail: init.detail,
      request_id: 'req-test',
      ...(init.errors ? { errors: init.errors } : {}),
    },
    { status: init.status, headers: { 'content-type': 'application/problem+json' } },
  )
}
