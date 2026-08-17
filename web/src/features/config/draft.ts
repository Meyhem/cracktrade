import { useCallback, useSyncExternalStore } from 'react'
import type { VersionOut } from '../../api/types'

/**
 * Unsaved edits, held outside the component tree.
 *
 * The brief requires an edit to survive navigation within the app, and the editor to keep
 * saying which version it is based on, so ten minutes of typing does not evaporate because
 * someone clicked through to a run to check a number. Component state cannot do that: the tab
 * unmounts. A module-level store can, unconditionally — it survives a remount of the whole
 * shell, not only of this tab.
 *
 * Deliberately not persisted to storage. A draft outliving a reload would mean the editor
 * opening on text the user has no memory of writing, against a head that may have moved twice
 * since; the `beforeunload` warning is the honest guard for that, because it asks.
 */

export type Draft = {
  /** The version this edit was made against. Sent with the save; a moved head is a conflict. */
  baseVersion: number
  yaml: string
}

const drafts = new Map<string, Draft>()
const listeners = new Set<() => void>()

function announce(): void {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function putDraft(strategyId: string, draft: Draft): void {
  drafts.set(strategyId, draft)
  announce()
}

export function clearDraft(strategyId: string): void {
  if (drafts.delete(strategyId)) announce()
}

/** Test-only: the store is module state, and one test's draft is not another's fixture. */
export function resetDrafts(): void {
  drafts.clear()
  announce()
}

export type DraftState = {
  yaml: string
  /** The version the draft was based on — which is not always the current head. */
  baseVersion: number
  dirty: boolean
  /**
   * The head moved while this draft was open, so the save will be refused (spec §15.2).
   * Surfaced before the user writes a note and presses save, rather than as a 409 after.
   */
  headMoved: boolean
  edit: (yaml: string) => void
  discard: () => void
}

export function useDraft(strategyId: string, head: VersionOut): DraftState {
  const stored = useSyncExternalStore(
    subscribe,
    // Returns the stored object itself, never a fallback constructed here: a fresh object on
    // every call is a new snapshot every time, which React reads as an endless stream of
    // changes.
    () => drafts.get(strategyId),
  )

  const edit = useCallback(
    (yaml: string) => {
      putDraft(strategyId, { baseVersion: stored?.baseVersion ?? head.version, yaml })
    },
    [strategyId, stored?.baseVersion, head.version],
  )

  const discard = useCallback(() => {
    clearDraft(strategyId)
  }, [strategyId])

  if (!stored) {
    return {
      yaml: head.yaml,
      baseVersion: head.version,
      dirty: false,
      headMoved: false,
      edit,
      discard,
    }
  }

  return {
    yaml: stored.yaml,
    baseVersion: stored.baseVersion,
    // Trailing whitespace is not an edit. Reporting it as one would put a dirty marker on a
    // strategy nobody touched, and a marker that cries wolf is a marker nobody reads.
    dirty: stored.yaml.trim() !== head.yaml.trim() || stored.baseVersion !== head.version,
    headMoved: stored.baseVersion !== head.version,
    edit,
    discard,
  }
}
