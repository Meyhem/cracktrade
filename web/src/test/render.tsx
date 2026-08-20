import type { ReactElement, ReactNode } from 'react'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { render, type RenderResult } from '@testing-library/react'
import { MetaContext, type MetaContextValue } from '../api/metaContext'
import { theme } from '../theme/theme'
import { testMeta } from './fixtures'

/**
 * Render a screen with everything the app provides it.
 *
 * `env="test"` on the provider matters: it disables Mantine's transitions, which are driven
 * by requestAnimationFrame and therefore never complete in jsdom. Without it, every assertion
 * about a modal or a tooltip would time out for reasons that have nothing to do with the code
 * under test.
 */
export function renderWithProviders(
  ui: ReactElement,
  options: { route?: string; meta?: Partial<MetaContextValue['meta']> } = {},
): RenderResult & { queryClient: QueryClient } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  const meta = { ...testMeta, ...options.meta }
  const metaValue: MetaContextValue = {
    meta,
    defaultsFor: (kind) => meta.defaults[kind],
    indicator: (type) => meta.indicators.find((entry) => entry.type === type),
    indicatorOutputs: (type, name) => {
      const description = meta.indicators.find((entry) => entry.type === type)
      if (!description || description.outputs.length === 0) return [name]
      return description.outputs.map((output) => `${name}_${output}`)
    },
    stopPriority: (field) =>
      meta.exit_fields.find((entry) => entry.name === field)?.stop_priority ?? null,
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MantineProvider env="test" theme={theme}>
        <QueryClientProvider client={queryClient}>
          <MetaContext value={metaValue}>
            <ModalsProvider>
              <Notifications />
              {children}
            </ModalsProvider>
          </MetaContext>
        </QueryClientProvider>
      </MantineProvider>
    )
  }

  const router = createMemoryRouter(
    [
      // Param-carrying paths come first: a screen that reads `useParams` gets nothing from
      // the catch-all, and the failure looks like an unmatched request rather than a route
      // that never bound its parameter.
      { path: '/prospect/:sessionId', element: <Wrapper>{ui}</Wrapper> },
      { path: '*', element: <Wrapper>{ui}</Wrapper> },
      { path: '/strategies/:id', element: <div>strategy detail</div> },
    ],
    { initialEntries: [options.route ?? '/strategies'] },
  )

  return { ...render(<RouterProvider router={router} />), queryClient }
}
