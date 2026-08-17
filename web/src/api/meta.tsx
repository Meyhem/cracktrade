import { useMemo, type ReactNode } from 'react'
import { useSuspenseQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { queryKeys, IMMUTABLE } from './keys'
import { MetaContext, type MetaContextValue } from './metaContext'
import type { EngineMeta } from './types'

/**
 * Engine constants, fetched once.
 *
 * The trade floor, the significance bar, the objectives and the indicator registry are all
 * served by `/meta` precisely so this side never restates them: a UI that says "16 of 20
 * needed" and an engine that suppresses at some other number cannot be told apart from a
 * working one until it has already misled someone.
 */
function fetchMeta(): Promise<EngineMeta> {
  return api.GET('/api/v1/meta', {}).then((result) => unwrap(result) as unknown as EngineMeta)
}

export function MetaProvider({ children }: { children: ReactNode }) {
  const { data: meta } = useSuspenseQuery({
    queryKey: queryKeys.meta,
    queryFn: fetchMeta,
    ...IMMUTABLE,
  })

  const value = useMemo<MetaContextValue>(
    () => ({
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
    }),
    [meta],
  )

  return <MetaContext value={value}>{children}</MetaContext>
}
