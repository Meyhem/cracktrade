import { createBrowserRouter, Navigate } from 'react-router'
import { AppShell } from '../components/AppShell'
import { StrategyListPage } from '../features/strategies/StrategyListPage'
import { StrategyLayout } from '../features/strategy/StrategyLayout'
import { RunsTab } from '../features/runs/RunsTab'
import { RunViewPage } from '../features/runs/RunViewPage'
import { ConfigTab } from '../features/config/ConfigTab'
import { ChartsTab } from '../features/charts/ChartsTab'
import { ComingSoon } from '../components/ComingSoon'

/**
 * Routes.
 *
 * Every tab is its own path so that a view can be sent to someone else. The charts tab in
 * particular takes the run and fold in its query string: a chart shared as a screenshot has
 * lost the one thing that says which configuration it describes.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    Component: AppShell,
    children: [
      { index: true, element: <Navigate replace to="/strategies" /> },
      { path: 'strategies', Component: StrategyListPage },
      {
        path: 'strategies/:strategyId',
        Component: StrategyLayout,
        children: [
          { index: true, element: <Navigate replace to="config" /> },
          { path: 'config', Component: ConfigTab },
          { path: 'optimizations', element: <RunsTab kind="optimize" /> },
          { path: 'backtests', element: <RunsTab kind="backtest" /> },
          { path: 'validation', element: <RunsTab kind="walk_forward" /> },
          { path: 'charts', Component: ChartsTab },
          { path: 'history', element: <ComingSoon what="The version history" /> },
          { path: 'runs/:runId', Component: RunViewPage },
        ],
      },
    ],
  },
])
