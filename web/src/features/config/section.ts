import type { Issue, SearchableParameter, ValidateResponse } from '../../api/types'

/** The non-component half of the form's plumbing, kept apart so `fields.tsx` exports only components. */

/** What every section needs to render itself against the draft. */
export type SectionProps = {
  yaml: string
  value: Record<string, unknown>
  /**
   * The server's answer, or `undefined` while one is in flight.
   *
   * Undefined is not "no problems". Sections render it as "not checked yet" rather than as a
   * pass, because an editor that showed a clean form over unvalidated text would be at its most
   * reassuring exactly when it knew least.
   */
  validation: ValidateResponse | undefined
  onChange: (yaml: string) => void
}

/** Shared empty lists, so a defaulted prop is not a new array on every render. */
export const NO_ISSUES: Issue[] = []
export const NO_PATHS: string[] = []

/** Optimizer answers indexed by the dotted path they belong to. */
export function searchIndex(
  validation: ValidateResponse | undefined,
): Map<string, SearchableParameter> {
  return new Map((validation?.searchable_parameters ?? []).map((entry) => [entry.path, entry]))
}

/**
 * Every path an issue about one field could arrive under.
 *
 * The server addresses a field two ways, and both are correct for what produced them. A
 * structural error comes from pydantic, which knows the list position: `indicators.0.window`. A
 * registry error comes from the semantic pass, which knows the indicator: `indicators.sma.window`.
 * A field matching only one of them would show half the problems with it and leave the rest in
 * the summary panel, unattached and apparently about something else.
 */
export function matching(issues: readonly Issue[], paths: readonly string[]): Issue[] {
  return issues.filter((issue) => paths.includes(issue.path))
}

/** Where the issue summary scrolls to. Dots are legal in an id but awkward in a selector. */
export function anchorId(path: string): string {
  return `field-${path.replace(/\./g, '-')}`
}
