import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { IMMUTABLE, queryKeys } from './keys'
import type { ConfigMapping, ValidateResponse } from './types'

/**
 * What the engine makes of a configuration.
 *
 * The editor's endpoint, used well before the editor exists, because it is also the only
 * honest source for "is there anything here for a search to move". The launch dialog's guard
 * needs that count, and the alternative — walking the config in the client looking for numeric
 * leaves — would be a second implementation of the optimizer's parameter discovery (spec
 * section 9.1) that agrees with the engine right up until an indicator gains a field.
 *
 * Cached as immutable: validating a fixed configuration is deterministic, so the answer for
 * one config is the same answer forever, and the key is the config itself.
 */
export function useValidatedConfig(config: ConfigMapping | undefined) {
  return useQuery({
    queryKey: queryKeys.validate(JSON.stringify(config ?? null)),
    enabled: config !== undefined,
    queryFn: async (): Promise<ValidateResponse> => {
      // `enabled` already prevents this; the check is what makes that readable to the compiler.
      if (config === undefined) throw new Error('no configuration to validate')
      const result = await api.POST('/api/v1/config/validate', { body: { config } })
      return unwrap(result)
    },
    ...IMMUTABLE,
  })
}

/**
 * How many numeric fields the search is allowed to move.
 *
 * `null` while unknown, which is not the same as zero and must not render as it: a dialog that
 * says "nothing to search" because a request is in flight has told the user something false
 * about their configuration.
 */
export function useSearchableCount(config: ConfigMapping | undefined): number | null {
  const { data } = useValidatedConfig(config)
  return data ? data.searchable_parameters.length : null
}
