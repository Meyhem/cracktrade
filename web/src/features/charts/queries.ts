import { useQueries, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { IMMUTABLE, queryKeys } from '../../api/keys'
import type { SeriesCatalog, SeriesPoints } from '../../api/types'

/**
 * Captured series, fetched one name at a time.
 *
 * `IMMUTABLE` is not a cache tweak here: the database rejects an UPDATE on `run_series`
 * (spec §14.2), so a series that has been read once cannot change, and refetching it is pure
 * waste. It also means the fold selector is free — moving between folds of a walk-forward
 * re-reads nothing already seen.
 */
export function useSeriesCatalog(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.runs.seriesCatalog(runId ?? ''),
    enabled: runId !== null,
    ...IMMUTABLE,
    queryFn: async (): Promise<SeriesCatalog> => {
      const result = await api.GET('/api/v1/runs/{run_id}/series', {
        params: { path: { run_id: runId ?? '' } },
      })
      return unwrap(result)
    },
  })
}

async function fetchSeries(runId: string, name: string, fold: number): Promise<SeriesPoints> {
  const result = await api.GET('/api/v1/runs/{run_id}/series/{name}', {
    params: { path: { run_id: runId, name }, query: { fold } },
  })
  return unwrap(result)
}

export type SeriesBundle = Record<string, SeriesPoints | undefined>

/**
 * Every series a chart group needs, for one fold, as one object.
 *
 * A missing entry is a *fact about the run* rather than a loading state — an optimization
 * captures only its test window, and a run recorded before a series existed simply has none —
 * so the bundle resolves to `undefined` for those names and `state.ts` turns that into the
 * labelled empty frame §5.2 asks for.
 */
export function useSeriesBundle(
  runId: string | null,
  fold: number | null,
  names: readonly string[],
): { bundle: SeriesBundle; isPending: boolean } {
  const enabled = runId !== null && fold !== null

  const results = useQueries({
    queries: names.map((name) => ({
      queryKey: queryKeys.runs.series(runId ?? '', name, fold ?? 0),
      enabled,
      ...IMMUTABLE,
      // A 404 is the honest answer for a series this run never captured, and retrying it
      // three times only delays the empty frame that is the correct thing to show.
      retry: false,
      queryFn: () => fetchSeries(runId ?? '', name, fold ?? 0),
    })),
  })

  const bundle: SeriesBundle = {}
  names.forEach((name, index) => {
    bundle[name] = results[index]?.data
  })

  return { bundle, isPending: enabled && results.some((result) => result.isPending) }
}

/**
 * One series, read for every fold at once.
 *
 * Only the monthly grid needs this. It is the single Group C chart whose windows can be laid
 * end to end without compounding across a fold boundary (see `stitchMonthly`), and drawing it
 * means holding every fold's months at the same time.
 */
export function useSeriesAcrossFolds(
  runId: string | null,
  name: string,
  folds: number[],
): { perFold: { fold: number; points: SeriesPoints | undefined }[]; isPending: boolean } {
  const results = useQueries({
    queries: folds.map((fold) => ({
      queryKey: queryKeys.runs.series(runId ?? '', name, fold),
      enabled: runId !== null,
      ...IMMUTABLE,
      retry: false,
      queryFn: () => fetchSeries(runId ?? '', name, fold),
    })),
  })

  return {
    perFold: folds.map((fold, index) => ({ fold, points: results[index]?.data })),
    isPending: runId !== null && results.some((result) => result.isPending),
  }
}

/** The URL of a stored series as CSV — the same bytes the chart was drawn from. */
export function seriesCsvHref(runId: string, name: string, fold: number): string {
  return `/api/v1/runs/${runId}/series/${name}.csv?fold=${fold}`
}
