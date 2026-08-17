import { createBrowserRouter, Navigate } from 'react-router'
import { AppShell } from '../components/AppShell'
import { StrategyListPage } from '../features/strategies/StrategyListPage'

/**
 * Routes.
 *
 * Strategy tabs, run views and the charts tab are added as their phases land; the shape is
 * fixed now so every screen is linkable from the start — a charts view that cannot be sent
 * to someone else is a screenshot waiting to be taken out of context.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    Component: AppShell,
    children: [
      { index: true, element: <Navigate replace to="/strategies" /> },
      { path: 'strategies', Component: StrategyListPage },
    ],
  },
])
