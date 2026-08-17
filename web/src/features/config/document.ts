import { parseDocument, type Document } from 'yaml'

/**
 * The editor's draft, as a YAML document.
 *
 * The draft is **text**, not a parsed object, and the form is a projection over it. The other
 * arrangement — an object as the source of truth, serialised into the YAML pane on demand —
 * is simpler right up until someone imports a commented file, changes one window in the form,
 * and finds their comments gone. `save_version` stores the text it is given verbatim
 * (`config_yaml`), so the round trip through this module is what decides whether a user's own
 * file survives being edited.
 *
 * Every write therefore goes through `yaml`'s document API, which edits nodes in place and
 * leaves every byte it did not touch — comments, key order, quoting style, `10000.0` keeping
 * its `.0` — exactly as it was.
 */

export type Path = readonly (string | number)[]

export type ConfigDocument =
  | { parsed: true; value: Record<string, unknown> }
  | { parsed: false; message: string; line: number | null }

/**
 * Read a draft.
 *
 * A document with errors is reported as unparsed rather than as its best-effort recovery: the
 * form renders fields from this, and a half-recovered document would show fields that are not
 * in the file the user is looking at.
 */
export function readConfig(text: string): ConfigDocument {
  const doc = parseDocument(text)
  const failure = doc.errors[0]
  if (failure) {
    return {
      parsed: false,
      message: failure.message,
      line: failure.linePos?.[0]?.line ?? null,
    }
  }

  const value: unknown = doc.toJS()
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { parsed: false, message: 'a strategy file must be a mapping of sections', line: null }
  }
  return { parsed: true, value: value as Record<string, unknown> }
}

/**
 * Write one value into the draft, creating whatever parents it needs.
 *
 * Returns the text unchanged when it does not parse. The form is not rendered in that state,
 * so this is unreachable in the app — but a silently-discarded edit is a bad enough outcome to
 * be worth being explicit that the discard is the deliberate branch, not an oversight.
 */
export function setAt(text: string, path: Path, value: unknown): string {
  return edit(text, (doc) => doc.setIn(path, value))
}

/** Remove a key. Used for every optional field, where absent and "empty" are different. */
export function removeAt(text: string, path: Path): string {
  return edit(text, (doc) => doc.deleteIn(path))
}

/** Set when `value` is given, remove when it is `null`. Optional fields are all this shape. */
export function putAt(text: string, path: Path, value: unknown): string {
  return value === null ? removeAt(text, path) : setAt(text, path, value)
}

function edit(text: string, apply: (doc: Document) => void): string {
  const doc = parseDocument(text)
  if (doc.errors.length > 0) return text
  apply(doc)
  return String(doc)
}

// --------------------------------------------------------------------------- reading

/**
 * Typed reads off the parsed value.
 *
 * Deliberately narrow: a string field reads as `null` when the file holds a number there,
 * rather than rendering `200` in a text input and writing it back as the string `"200"`.
 * Numbers are not coerced from strings anywhere in this engine (spec §3.1), and an editor that
 * quietly coerced them would be the one place they were.
 */
export function at(value: unknown, path: Path): unknown {
  let current = value
  for (const key of path) {
    if (current === null || typeof current !== 'object') return undefined
    current = (current as Record<string | number, unknown>)[key]
  }
  return current
}

export function stringAt(value: unknown, path: Path): string | null {
  const found = at(value, path)
  return typeof found === 'string' ? found : null
}

export function numberAt(value: unknown, path: Path): number | null {
  const found = at(value, path)
  return typeof found === 'number' && Number.isFinite(found) ? found : null
}

export function listAt(value: unknown, path: Path): unknown[] {
  const found = at(value, path)
  return Array.isArray(found) ? found : []
}
