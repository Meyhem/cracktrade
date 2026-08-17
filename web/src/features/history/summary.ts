/**
 * Rendering a change list as a sentence.
 *
 * The timeline needs "rsi_ind.window 14 → 21, atr_swing.take_profit_pct 18 → 12" — one line,
 * scannable, per version. Kept apart from the components because the awkward cases are all
 * about *values* rather than layout: a signal expression is a long string, a null is an absence
 * rather than a value, and a config with thirty changed leaves must not produce a thirty-clause
 * line that pushes the timestamp off the row.
 */

import type { Change } from '../../api/types'

/** How many changes a one-line summary names before it starts counting instead. */
export const NAMED_IN_SUMMARY = 3

/** The longest a value may be before it is elided; longer than this is a signal expression. */
const VALUE_LIMIT = 28

export function summarise(changes: Change[], limit = NAMED_IN_SUMMARY): string {
  if (changes.length === 0) return 'No changes'

  const named = changes.slice(0, limit).map(clause).join(', ')
  const rest = changes.length - limit
  return rest > 0 ? `${named}, and ${rest} more change${rest === 1 ? '' : 's'}` : named
}

export function clause(change: Change): string {
  return `${shortPath(change.path)} ${valueOf(change.old)} → ${valueOf(change.new)}`
}

/**
 * The path without its section prefix.
 *
 * `indicators.rsi_ind.window` reads better as `rsi_ind.window`, and the section is already the
 * heading in the grouped diff. `exit.stop_loss_pct` keeps its prefix because `stop_loss_pct`
 * alone loses which rule it belongs to.
 */
export function shortPath(path: string): string {
  return path.startsWith('indicators.') ? path.slice('indicators.'.length) : path
}

/**
 * One value, as the summary prints it.
 *
 * `null` renders as "unset" rather than as the word null or an empty gap: a field moving from
 * unset to a number is a real edit, and rendering the before-side as nothing at all makes it
 * read as though the line were describing a new field rather than a changed one.
 */
export function valueOf(value: unknown): string {
  if (value === null || value === undefined) return 'unset'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') {
    const quoted = value.length > VALUE_LIMIT ? `${value.slice(0, VALUE_LIMIT).trimEnd()}…` : value
    return `"${quoted}"`
  }
  // Arrays and objects reach here when a whole section is added or removed at once. The shape
  // is not worth spelling out in a one-line summary, and truncated JSON is worse than a word.
  return Array.isArray(value) ? `${value.length} entries` : 'a group of settings'
}

/**
 * Sections whose change makes earlier runs incomparable, and what that means.
 *
 * Mirrors the server's own `CONSEQUENCES` — the API sends these strings on the diff, and this
 * is only for the timeline, which carries flags rather than full section diffs.
 */
export const FLAG_MEANING: Record<string, string> = {
  universe_changed:
    'The universe changed here: the ticker or the date range moved, so runs either side of this version cover different history and cannot be compared.',
  execution_changed:
    'The cost model changed here. Every run made before this version was measured against different costs, so comparing across it compares experiments rather than strategies.',
}

export function flagLabel(flag: string): string {
  return flag === 'universe_changed' ? 'Universe changed' : 'Costs changed'
}
