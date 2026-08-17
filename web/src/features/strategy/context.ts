import { createContext, use } from 'react'
import type { StrategyDetail } from '../../api/types'

/** The strategy every tab under `/strategies/:id` is about, fetched once by the layout. */
export const StrategyContext = createContext<{ strategy: StrategyDetail } | null>(null)

export function useStrategyContext(): StrategyDetail {
  const value = use(StrategyContext)
  if (!value) throw new Error('useStrategyContext must be used inside <StrategyLayout>')
  return value.strategy
}
