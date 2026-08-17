import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { queryKeys } from '../../api/keys'
import type { SavedVersion } from '../../api/types'

/**
 * Saving an edit.
 *
 * The body carries `yaml`, never the parsed mapping. The server stores what it is given as the
 * version's `config_yaml`, so sending the text is what preserves the user's comments and
 * layout in the history; sending a mapping would store the canonical re-serialisation and the
 * file they wrote would exist nowhere.
 *
 * `base_version` makes the save optimistic (spec §15.2). A head that moved underneath comes
 * back as a 409 explaining which version it is now, rather than as a silent overwrite of
 * somebody's work.
 */
export function useSaveVersion(strategyId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: {
      baseVersion: number
      yaml: string
      note?: string
    }): Promise<SavedVersion> => {
      const result = await api.POST('/api/v1/strategies/{strategy_id}/versions', {
        params: { path: { strategy_id: strategyId } },
        body:
          input.note === undefined
            ? { base_version: input.baseVersion, yaml: input.yaml }
            : { base_version: input.baseVersion, yaml: input.yaml, note: input.note },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      // The head moved, so every run against it is now stale and the detail header is wrong.
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.versions.all(strategyId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
    },
  })
}
