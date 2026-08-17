/**
 * The comparison table's Δ column: when two versions may be compared, and what the movement is.
 *
 * This is the riskiest thing on the history screen. Every other column reports a number some
 * run produced; Δ *asserts a relationship* between two runs, and the user reads that assertion
 * as "did my edit help". A Δ between two runs that were not measuring the same question is a
 * confident answer to a question nobody asked, and it is indistinguishable on screen from a
 * real one.
 *
 * So the rules here are all about refusing. Everything below decides either "these two are
 * comparable" or "they are not, and here is the sentence explaining why" — never "close
 * enough".
 *
 * Written as pure functions over plain records, separately from the table, because these rules
 * are the part worth testing and a rule embedded in a cell renderer is a rule tested by looking
 * at it.
 */

export type RunKind = 'backtest' | 'optimize' | 'walk_forward'

/** The five figures the Δ column moves, in the engine's own units. */
export type Figures = {
  /** Out-of-sample total return, in percent. */
  returnPct: number | null
  /** Excess over buy-and-hold, already in percentage points. */
  excessPp: number | null
  /**
   * Maximum drawdown, in percent and **negative** — the engine reports it as a signed move
   * from the peak, and `tests/test_metrics.py` asserts `<= 0`.
   */
  maxDrawdownPct: number | null
  sharpe: number | null
  /** Share of folds that made money, in `[0, 1]`. Walk-forward only. */
  foldWinRate: number | null
}

export type MetricKey = keyof Figures

/**
 * One version's selected run, reduced to what the table needs.
 *
 * `figures` is null when the run is below the trade floor: suppression happens before this
 * module sees anything, so there is no path here that could compute a Δ from a withheld number.
 */
export type VersionRun = {
  version: number
  runId: string
  kind: RunKind
  /** The objective the search maximised. Null for a backtest, which optimises nothing. */
  objective: string | null
  /** Walk-forward only. */
  folds: number | null
  scheme: string | null
  /** The price frame the run actually read (spec §4.4). */
  frameDigest: string | null
  trades: number | null
  figures: Figures | null
}

/** One row of the table: a version, and the run of the selected kind against it, if any. */
export type VersionRow = {
  version: number
  run: VersionRun | null
}

// --------------------------------------------------------------------------- comparability

/**
 * Why these two runs may not be compared, or null if they may.
 *
 * Ordered most-fundamental first, and only the first reason is reported: a walk-forward with a
 * different objective *and* a different fold count is not two problems to fix, it is one
 * sentence saying these runs are unrelated.
 *
 * The kind check looks redundant — the table is filtered to one kind — but it is the rule the
 * brief is most explicit about ("do not fall back to comparing a backtest against a
 * walk-forward"), and a filter is a thing that can be got wrong somewhere else.
 */
export function incomparability(older: VersionRun, newer: VersionRun): string | null {
  if (older.kind !== newer.kind) {
    return `v${older.version} was a ${kindName(older.kind)} and v${newer.version} a ${kindName(newer.kind)}. Different exercises produce different numbers, so the movement between them measures nothing.`
  }
  if (older.objective !== newer.objective) {
    return `v${older.version}'s search maximised ${older.objective ?? 'nothing'} and v${newer.version}'s maximised ${newer.objective ?? 'nothing'}. Each run won at its own game.`
  }
  if (older.folds !== newer.folds) {
    return `v${older.version} was validated over ${countOf(older.folds, 'fold')} and v${newer.version} over ${countOf(newer.folds, 'fold')}. Fold count changes both the test windows and how much history each one gets.`
  }
  if (older.scheme !== newer.scheme) {
    return `v${older.version} used a ${older.scheme ?? 'different'} scheme and v${newer.version} a ${newer.scheme ?? 'different'} one, so the two were tested on differently drawn windows.`
  }
  return null
}

function kindName(kind: RunKind): string {
  return kind === 'walk_forward'
    ? 'walk-forward'
    : kind === 'optimize'
      ? 'optimization'
      : 'backtest'
}

function countOf(value: number | null, noun: string): string {
  if (value === null) return `an unrecorded number of ${noun}s`
  return `${value} ${noun}${value === 1 ? '' : 's'}`
}

// --------------------------------------------------------------------------- the Δ itself

/**
 * Which direction is an improvement, stated rather than inferred.
 *
 * Every one of the five is higher-is-better, **including drawdown**, and that is the trap. A
 * drawdown reads as "smaller is better" in English, and the brief says exactly that; but the
 * engine signs it negative, so −10% is a better outcome than −30% and the arithmetic agrees
 * with the other four. Inferring direction from the metric's name, or flipping drawdown on the
 * strength of the sentence in the brief, would colour a halved drawdown as a regression.
 *
 * Kept as an explicit table so that a metric added later has to answer the question.
 */
export const HIGHER_IS_BETTER: Record<MetricKey, true> = {
  returnPct: true,
  excessPp: true,
  maxDrawdownPct: true,
  sharpe: true,
  foldWinRate: true,
}

export type Movement = {
  metric: MetricKey
  /** `newer − older`, in the metric's own units. */
  value: number
  improved: boolean
}

export type Delta =
  | { shown: false; reason: string }
  | {
      shown: true
      /** The version this row is measured against — not necessarily the one before it. */
      against: number
      movements: Movement[]
      /** Things true of this comparison that stop it being a clean read. */
      caveats: string[]
    }

/**
 * The Δ for `rows[index]`, against the nearest earlier version with a comparable run.
 *
 * "Nearest earlier *comparable*" is the brief's wording and it means walking back past versions
 * that ran something else, rather than stopping at the first one that happens to have a run.
 * Where that happens the skipped versions are named in a caveat: a Δ silently measured against
 * v2 while sitting in v5's row, with v3 and v4 visibly carrying runs, is a number the reader
 * would reasonably misattribute.
 */
export function deltaFor(rows: VersionRow[], index: number): Delta {
  const row = rows[index]
  if (!row) return { shown: false, reason: 'No such version.' }

  const current = row.run
  if (!current) {
    return { shown: false, reason: `No run of this kind was made against v${row.version}.` }
  }
  if (!current.figures) {
    return {
      shown: false,
      reason: `v${row.version}'s run is below the trade floor, so it has no figures to move.`,
    }
  }

  const skipped: string[] = []
  for (let earlier = index - 1; earlier >= 0; earlier -= 1) {
    const candidate = rows[earlier]?.run
    if (!candidate) continue

    if (!candidate.figures) {
      skipped.push(`v${candidate.version} (below the trade floor)`)
      continue
    }
    const reason = incomparability(candidate, current)
    if (reason !== null) {
      skipped.push(`v${candidate.version} (${firstClause(reason)})`)
      continue
    }
    return {
      shown: true,
      against: candidate.version,
      movements: movementsBetween(candidate.figures, current.figures),
      caveats: caveats(candidate, current, skipped),
    }
  }

  return {
    shown: false,
    reason:
      skipped.length > 0
        ? `No earlier version has a comparable run — ${skipped.join(', ')}.`
        : `v${row.version} is the earliest version with a run of this kind.`,
  }
}

function firstClause(reason: string): string {
  // The reasons above are written as full sentences for the hover; inside a list of skipped
  // versions only the distinguishing half is wanted.
  const [first = reason] = reason.split('. ')
  return first.toLowerCase()
}

function movementsBetween(older: Figures, newer: Figures): Movement[] {
  const keys: MetricKey[] = ['returnPct', 'excessPp', 'maxDrawdownPct', 'sharpe', 'foldWinRate']
  const movements: Movement[] = []
  for (const metric of keys) {
    const before = older[metric]
    const after = newer[metric]
    // A missing figure on either side is not a zero move. Both runs have to have reported the
    // number for the difference between them to mean anything.
    if (before === null || after === null) continue
    const value = after - before
    movements.push({ metric, value, improved: HIGHER_IS_BETTER[metric] ? value > 0 : value < 0 })
  }
  return movements
}

/**
 * What stops this comparison being a clean read, without stopping it being shown.
 *
 * The data-vintage one is the substantive case. Two runs made weeks apart read price frames
 * that have been retroactively adjusted since — splits, dividends, vendor corrections — so a
 * version can appear to have improved because the history moved underneath it. The engine
 * records the frame digest for exactly this reason (spec §4.4); when they differ, the Δ is
 * still the honest arithmetic on two real runs, but it is not evidence that the *edit* did
 * anything, and it must not be presented as though it were.
 */
function caveats(older: VersionRun, newer: VersionRun, skipped: string[]): string[] {
  const notes: string[] = []
  if (
    older.frameDigest !== null &&
    newer.frameDigest !== null &&
    older.frameDigest !== newer.frameDigest
  ) {
    notes.push(
      `These two runs read different price data — the frames they loaded do not match, so some of this movement may be the history being revised rather than the strategy changing.`,
    )
  } else if (older.frameDigest === null || newer.frameDigest === null) {
    notes.push(
      `One of these runs did not record which price frame it read, so whether they measured the same history cannot be checked.`,
    )
  }
  if (skipped.length > 0) {
    notes.push(`Measured against v${older.version}, skipping ${skipped.join(', ')}.`)
  }
  return notes
}
