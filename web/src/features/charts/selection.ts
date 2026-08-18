/**
 * Which run the charts describe, and which fold of it.
 *
 * A tab that quietly draws the newest run is how someone ends up judging a configuration they
 * have since edited, so the selection is explicit, stated in words, and part of the URL — a
 * chart shared as a screenshot has already lost the one thing that says which configuration it
 * describes, and the least this app can do is not lose it in a link as well.
 */

import type { Run, RunKind } from '../../api/types'

/**
 * Seriousness, most serious first.
 *
 * A walk-forward is the only run that has been asked to survive data it never saw, so it
 * outranks an optimization, which outranks a single backtest. The default lands on the run
 * that has earned the most trust rather than the one that finished last.
 *
 * Evolution ranks last, and not because its evidence is weak — it has a holdout the search
 * never touched. It ranks last because its curves are not *this strategy's*: they describe a
 * composition that lives in the run, drawn over the holdout alone. Every other run on this tab
 * charts the strategy in the page header, and defaulting to the one that does not would put a
 * different strategy's equity curve under this strategy's name.
 */
export const SERIOUSNESS: readonly RunKind[] = ['walk_forward', 'optimize', 'backtest', 'evolve']

function launchedAt(run: Run): number {
  return new Date(run.finished_at ?? run.queued_at).getTime()
}

/** Succeeded runs only, most serious kind first and most recent within a kind. */
export function chartableRuns(runs: Run[]): Run[] {
  return runs
    .filter((run) => run.status === 'succeeded')
    .sort((left, right) => {
      const rank = SERIOUSNESS.indexOf(left.kind) - SERIOUSNESS.indexOf(right.kind)
      return rank !== 0 ? rank : launchedAt(right) - launchedAt(left)
    })
}

export function defaultRun(runs: Run[]): Run | null {
  return chartableRuns(runs)[0] ?? null
}

/**
 * Why this run is the one on screen.
 *
 * Said in words rather than implied by a dropdown's position, because "the most recent
 * walk-forward" and "the only run that has finished" are different situations and a user who
 * cannot tell them apart cannot tell how much the charts are worth.
 */
export function whyThisRun(run: Run, all: Run[]): string {
  const chartable = chartableRuns(all)
  if (chartable.length <= 1) return 'the only run that has finished'

  const sameKind = chartable.filter((other) => other.kind === run.kind)
  const moreSerious = chartable.filter(
    (other) => SERIOUSNESS.indexOf(other.kind) < SERIOUSNESS.indexOf(run.kind),
  )
  const newest = sameKind[0]?.id === run.id

  if (moreSerious.length > 0) return 'chosen by you — more serious runs are available'
  if (!newest) return 'chosen by you'
  return sameKind.length > 1
    ? 'the most recent of its kind, and the most serious available'
    : 'the most serious run available'
}

/**
 * The fold a validation run's charts are scoped to.
 *
 * `null` is Combined, and it is the default. Combined is not "all the trades together": each
 * fold re-optimizes, so a pooled trade view would describe a configuration that was never run
 * (spec §12.9). It is the view in which the trade-level groups explain themselves instead.
 */
export type FoldSelection = number | null

/**
 * Series are stored per fold, numbered from one; a single-window run stores everything under
 * fold 0. `FoldResult.index` counts from zero. Getting the two confused draws fold 2's curve
 * under fold 1's parameters, which is invisible and wrong, so the conversion lives here.
 */
export function seriesFold(kind: RunKind, selection: FoldSelection): number | null {
  if (kind !== 'walk_forward') return 0
  return selection === null ? null : selection + 1
}

export function foldLabel(selection: FoldSelection): string {
  return selection === null ? 'Combined (all folds)' : `Fold ${selection + 1}`
}
