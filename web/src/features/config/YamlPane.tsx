import { useEffect, useRef } from 'react'
import { useComputedColorScheme } from '@mantine/core'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { Compartment, EditorState, type Extension } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { bracketMatching } from '@codemirror/language'
import { yaml } from '@codemirror/lang-yaml'
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { editorTheme } from './editorTheme'
import type { Issue } from '../../api/types'

/**
 * The raw editor.
 *
 * Diagnostics come from the server and are placed by the line it reported (spec §3.9). They are
 * pushed in rather than produced by a `linter` extension, because a linter is a function from
 * document to problems and the only honest such function here is a round trip — running one
 * locally would mean two answers about the same text, differing while a request is in flight.
 *
 * Colours live in `editorTheme`, swapped through a compartment when the scheme changes. This is
 * the one surface in the app where a rebuild is not an option: the editor holds the user's
 * unsaved text, cursor and undo history, and toggling the scheme is not a reason to lose any of
 * them.
 */
export function YamlPane({
  errors,
  onChange,
  value,
  warnings,
}: {
  errors: Issue[]
  onChange: (next: string) => void
  value: string
  warnings: Issue[]
}) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const latest = useRef(onChange)
  latest.current = onChange
  const scheme = useComputedColorScheme('light')
  const theme = useRef(new Compartment())

  // The scheme at creation time, readable without making it a dependency of the setup effect.
  const schemeAtSetup = useRef(scheme)
  schemeAtSetup.current = scheme

  useEffect(() => {
    const element = host.current
    if (!element) return

    const editor = new EditorView({
      parent: element,
      state: EditorState.create({
        doc: value,
        extensions: extensions(latest, theme.current, schemeAtSetup.current),
      }),
    })
    view.current = editor
    return () => {
      editor.destroy()
      view.current = null
    }
    // Created once. `value` is synchronised by the effect below, because recreating the editor
    // on every keystroke would discard the cursor along with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const editor = view.current
    if (!editor) return
    editor.dispatch({ effects: theme.current.reconfigure(editorTheme(scheme)) })
  }, [scheme])

  useEffect(() => {
    const editor = view.current
    if (!editor || editor.state.doc.toString() === value) return
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: value },
    })
  }, [value])

  useEffect(() => {
    const editor = view.current
    if (!editor) return
    editor.dispatch(setDiagnostics(editor.state, diagnostics(editor, errors, warnings)))
  }, [errors, warnings])

  return <div data-testid="yaml-editor" ref={host} style={{ minHeight: 400 }} />
}

function extensions(
  latest: { current: (next: string) => void },
  theme: Compartment,
  scheme: 'light' | 'dark',
): Extension[] {
  return [
    lineNumbers(),
    history(),
    bracketMatching(),
    lintGutter(),
    theme.of(editorTheme(scheme)),
    keymap.of([...defaultKeymap, ...historyKeymap]),
    yaml(),
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged) latest.current(update.state.doc.toString())
    }),
  ]
}

/**
 * Turn issues into gutter markers.
 *
 * An issue with no line has nowhere to go. It is deliberately dropped here rather than pinned
 * to line 1: a marker on a line that has nothing to do with the problem is worse than no
 * marker, and the summary panel above the editor shows every issue regardless.
 */
function diagnostics(view: EditorView, errors: Issue[], warnings: Issue[]): Diagnostic[] {
  const found: Diagnostic[] = []

  for (const [severity, issues] of [
    ['error', errors],
    ['warning', warnings],
  ] as const) {
    for (const issue of issues) {
      if (issue.line === null || issue.line === undefined) continue
      if (issue.line < 1 || issue.line > view.state.doc.lines) continue
      const line = view.state.doc.line(issue.line)
      found.push({
        from: line.from,
        to: line.to,
        severity,
        message: issue.path ? `${issue.path}: ${issue.message}` : issue.message,
      })
    }
  }

  return found
}
