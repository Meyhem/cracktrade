import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { IMMUTABLE, queryKeys } from './keys'
import type { ConfigDiffResponse, ConfigMapping, ValidateResponse } from './types'

/** One side of a diff: a stored mapping, or YAML a search emitted. Exactly one. */
export type ConfigSource = { config: ConfigMapping } | { yaml: string }

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
    queryKey: queryKeys.validate('config', JSON.stringify(config ?? null)),
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
 * The same question, asked about YAML text.
 *
 * The editor's own validator. It sends the text rather than a parsed mapping for two reasons:
 * the server reports a syntax error on the line that caused it, and a structural error against
 * text carries the line it came from (spec §3.9) — which is what the YAML pane's gutter marks.
 * Parsing client-side first and sending the mapping would throw both away.
 *
 * There is deliberately no second, client-side implementation of validity. What makes a
 * configuration a strategy is the engine's judgement (spec §3.1), and a form that decided for
 * itself would agree with the engine until the schema next changed, then disagree silently —
 * in the one screen whose job is to say what is wrong.
 *
 * The previous answer is kept on screen while the next one is fetched. That is a statement
 * about errors only: an error the user has not fixed yet stays visible instead of blinking out
 * on each keystroke and back in. It is emphatically *not* a licence to act on it — the save
 * control gates on whether the answer describes the text currently in the editor, because
 * "valid" about the text of half a second ago is not a claim about the text on screen.
 */
export function useValidatedYaml(text: string | undefined) {
  return useQuery({
    queryKey: queryKeys.validate('yaml', text ?? ''),
    enabled: text !== undefined,
    queryFn: async (): Promise<ValidateResponse> => {
      if (text === undefined) throw new Error('no configuration to validate')
      const result = await api.POST('/api/v1/config/validate', { body: { yaml: text } })
      return unwrap(result)
    },
    placeholderData: keepPreviousData,
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

/**
 * Compare two configurations that need not be versions of anything.
 *
 * The promote dialog's diff. Both sides are canonicalised server-side before comparison, so
 * omitted defaults and key order do not read as changes a search made (spec section 15.1).
 */
export function useConfigDiff(from: ConfigSource | null, to: ConfigSource | null) {
  return useQuery({
    queryKey: ['config', 'diff', JSON.stringify(from), JSON.stringify(to)],
    enabled: from !== null && to !== null,
    queryFn: async (): Promise<ConfigDiffResponse> => {
      if (from === null || to === null) throw new Error('two configurations are required')
      const result = await api.POST('/api/v1/config/diff', { body: { from, to } })
      return unwrap(result)
    },
    ...IMMUTABLE,
  })
}
