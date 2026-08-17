import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { queryKeys, type StrategyListFilters } from '../../api/keys'
import type { CreatedStrategy, StrategyDetail, StrategyList, StrategyRow } from '../../api/types'

export type StrategyListResult = Omit<StrategyList, 'strategies'> & {
  strategies: StrategyRow[]
  totals: Record<string, number>
}

export function useStrategies(filters: StrategyListFilters) {
  return useQuery({
    queryKey: queryKeys.strategies.list(filters),
    queryFn: async (): Promise<StrategyListResult> => {
      const query: Record<string, string> = {}
      if (filters.search) query.search = filters.search
      if (filters.verdict) query.verdict = filters.verdict

      const result = await api.GET('/api/v1/strategies', { params: { query } })
      return unwrap(result) as unknown as StrategyListResult
    },
  })
}

export function useStrategy(strategyId: string) {
  return useQuery({
    queryKey: queryKeys.strategies.detail(strategyId),
    queryFn: async (): Promise<StrategyDetail> => {
      const result = await api.GET('/api/v1/strategies/{strategy_id}', {
        params: { path: { strategy_id: strategyId } },
      })
      return unwrap(result)
    },
  })
}

export type CreateStrategyInput = {
  name: string
  ticker: string
  start_date: string
  end_date: string
  seed: 'minimal' | 'empty'
}

export function useCreateStrategy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: CreateStrategyInput): Promise<CreatedStrategy> => {
      const result = await api.POST('/api/v1/strategies', { body: input })
      return unwrap(result)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
    },
  })
}

export function useImportStrategy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: { yaml: string; filename?: string }): Promise<CreatedStrategy> => {
      const result = await api.POST('/api/v1/strategies/import', {
        body: input.filename
          ? { yaml: input.yaml, filename: input.filename }
          : { yaml: input.yaml },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
    },
  })
}

export function useForkStrategy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: {
      strategyId: string
      name: string
      version?: number
    }): Promise<CreatedStrategy> => {
      const result = await api.POST('/api/v1/strategies/{strategy_id}/fork', {
        params: { path: { strategy_id: input.strategyId } },
        body:
          input.version === undefined
            ? { name: input.name }
            : { name: input.name, version: input.version },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
    },
  })
}
