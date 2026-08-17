import { Suspense } from 'react'
import { Center, Loader, MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router'
import { ApiError } from './api/errors'
import { MetaProvider } from './api/meta'
import { router } from './routes/router'
import { theme } from './theme/theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 404 or a 409 is an answer, not a hiccup; retrying it just delays the screen that
      // has to explain it. Genuine transport failures are still worth one more attempt.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status > 0 ? false : failureCount < 2,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
})

export function App() {
  return (
    <MantineProvider defaultColorScheme="auto" theme={theme}>
      <QueryClientProvider client={queryClient}>
        <ModalsProvider>
          <Notifications position="top-right" />
          <Suspense
            fallback={
              <Center h="100vh">
                <Loader />
              </Center>
            }
          >
            <MetaProvider>
              <RouterProvider router={router} />
            </MetaProvider>
          </Suspense>
        </ModalsProvider>
      </QueryClientProvider>
    </MantineProvider>
  )
}
