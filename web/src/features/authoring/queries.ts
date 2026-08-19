import { useMutation } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import type { GenerateConfigResponse } from '../../api/types'

export type GenerateInput = {
  instruction: string
  /** The file being revised. Absent for a new strategy. */
  baseYaml?: string
}

/**
 * Ask the server to write a configuration.
 *
 * A mutation rather than a query, and deliberately: it is expensive, it is not idempotent, and
 * it must never be fired by a component mounting. Nothing is cached either — two identical
 * prompts are two different drafts, and serving the second from cache would make the Regenerate
 * button silently do nothing.
 *
 * The result is *not* stored anywhere on the server, so nothing is invalidated on success.
 * That is the point of the endpoint: what comes back is a proposal, and it becomes a strategy
 * only when the user adopts it through import or a saved version.
 */
export function useGenerateConfig() {
  return useMutation({
    mutationFn: async (input: GenerateInput): Promise<GenerateConfigResponse> => {
      const result = await api.POST('/api/v1/config/generate', {
        body: input.baseYaml
          ? { instruction: input.instruction, base_yaml: input.baseYaml }
          : { instruction: input.instruction },
      })
      return unwrap(result)
    },
  })
}
