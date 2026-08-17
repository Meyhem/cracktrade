import type { Issue, ValidateResponse } from '../../api/types'

/**
 * Attaching the engine's complaints to the fields that caused them.
 *
 * The server addresses each issue with a dotted path (spec §3.9). Matching on that path is
 * what puts "min_holding_days (15) must be less than max_holding_days (10)" under the two
 * inputs it is about rather than in a banner at the top of a form the user then has to search.
 *
 * Errors block the save; warnings never do. That distinction is the server's and is carried
 * through unchanged — a client that promoted a warning to an error would refuse to save a
 * strategy the engine considers valid.
 */

/** Issues on exactly this field. */
export function issuesAt(issues: readonly Issue[], path: string): Issue[] {
  return issues.filter((issue) => issue.path === path)
}

/**
 * Issues on this field or anything beneath it.
 *
 * A section header uses this: `exit` collects both its own cross-field errors and the ones on
 * `exit.min_holding_days`, so a collapsed section can still say that something inside it is
 * wrong.
 */
export function issuesUnder(issues: readonly Issue[], prefix: string): Issue[] {
  return issues.filter((issue) => issue.path === prefix || issue.path.startsWith(`${prefix}.`))
}

/** Issues with no field to attach to. They still have to be shown somewhere. */
export function unattached(issues: readonly Issue[]): Issue[] {
  return issues.filter((issue) => issue.path === '')
}

/** How an issue reads in a summary list, where the field is not implied by position. */
export function describe(issue: Issue): string {
  return issue.path ? `${issue.path}: ${issue.message}` : issue.message
}

/**
 * Why save is disabled, in words.
 *
 * The brief is explicit that a disabled control always states its reason — a dead button with
 * no explanation is the failure mode. `null` means the save is available.
 *
 * "Checking" is its own answer and not an optimistic pass: while a round trip is in flight the
 * client does not know whether the text is valid, and a save offered on a stale answer would
 * be a save that fails at the server for reasons the screen was still claiming were fine.
 */
export function saveBlockedBecause(state: {
  dirty: boolean
  headMoved: boolean
  checking: boolean
  validation: ValidateResponse | undefined
}): string | null {
  if (state.headMoved) {
    return 'This edit was based on a version that is no longer the head. Reload before saving.'
  }
  if (!state.dirty) return 'Nothing has changed yet.'
  if (state.checking || !state.validation) return 'Still checking this configuration.'
  if (!state.validation.valid) {
    const count = state.validation.errors.length
    return `${count} error${count === 1 ? '' : 's'} to fix first.`
  }
  return null
}
