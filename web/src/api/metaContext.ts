import { createContext, use } from 'react'
import type { EngineMeta, IndicatorDescription, RunDefaults, RunKind } from './types'

/**
 * The engine-metadata context, kept apart from its provider component so that a file which
 * exports a component exports only components.
 */
export type MetaContextValue = {
  meta: EngineMeta
  /** Launch defaults for one run kind. */
  defaultsFor: (kind: RunKind) => RunDefaults
  indicator: (type: string) => IndicatorDescription | undefined
  /**
   * The names one configured indicator contributes to the signal namespace.
   *
   * A single-output indicator contributes its own name; a multi-output one fans out to
   * `<name>_<output>` and does *not* contribute the bare name.
   */
  indicatorOutputs: (type: string, name: string) => string[]
  /** Where an exit field sits in the stop chain; null when it is not a stop. */
  stopPriority: (field: string) => number | null
}

export const MetaContext = createContext<MetaContextValue | null>(null)

export function useMeta(): MetaContextValue {
  const value = use(MetaContext)
  if (!value) throw new Error('useMeta must be used inside <MetaProvider>')
  return value
}
