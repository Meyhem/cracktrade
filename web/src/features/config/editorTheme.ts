import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import { tags } from '@lezer/highlight'

/**
 * The editor's colours, per scheme.
 *
 * CodeMirror ships `defaultHighlightStyle`, which is a light-mode style and the only one this
 * editor had. In dark mode it left YAML keys at `#0000cc` on the `#242424` page — a contrast
 * ratio of 1.06:1, which is not "hard to read" but genuinely invisible — quoted strings at
 * 2.05:1, and a white `#f5f5f5` line-number gutter stapled to the side of a dark editor. None of
 * that is theme-dependent in CodeMirror: the base theme's own dark rules are gated behind the
 * `darkTheme` facet, which nothing here was setting.
 *
 * Only four things are coloured, because only four are things the YAML grammar actually knows.
 * `@codemirror/lang-yaml` tags every unquoted scalar as `content` — `10000`, `true`, `null` and
 * a bare word are one token to it, and rules for `number`, `bool` or `null` are silently inert.
 * Unquoted values therefore keep the body colour rather than being painted as though the editor
 * had recognised a type it did not; `editorTheme.test.ts` pins that, so a grammar upgrade that
 * starts distinguishing them fails the test rather than quietly changing what the colours claim.
 *
 * Shades are Mantine palette steps rather than hand-mixed hexes, so the editor drifts with the
 * rest of the app if the palette moves. Hues are constant across schemes — keys are blue in
 * both, only the step changes — because a token that changes hue with the scheme is a token the
 * reader has to re-learn. Measured ratios against the scheme's page background, dark / light:
 *
 *   keys              blue-3 / blue-9    7.90 / 6.09
 *   quoted strings    teal-3 / teal-9   10.05 / 5.00
 *   comments, `:`     gray-5 / gray-7    7.38 / 8.20
 *   everything else   body text          9.37 / 21.0
 */
const PALETTE = {
  dark: {
    key: 'var(--mantine-color-blue-3)',
    string: 'var(--mantine-color-teal-3)',
    muted: 'var(--mantine-color-gray-5)',
  },
  light: {
    key: 'var(--mantine-color-blue-9)',
    string: 'var(--mantine-color-teal-9)',
    muted: 'var(--mantine-color-gray-7)',
  },
} as const

export function highlightFor(scheme: 'light' | 'dark') {
  const colour = PALETTE[scheme]
  return HighlightStyle.define(
    [
      { tag: [tags.propertyName, tags.definition(tags.propertyName)], color: colour.key },
      { tag: [tags.string, tags.special(tags.string)], color: colour.string },
      { tag: [tags.comment, tags.lineComment, tags.blockComment], color: colour.muted },
      { tag: [tags.separator, tags.punctuation], color: colour.muted },
    ],
    { themeType: scheme },
  )
}

/**
 * The chrome — surfaces, gutter and caret.
 *
 * The `dark` flag is the load-bearing part. It sets CodeMirror's `darkTheme` facet, which is
 * what switches the caret, the selection and the lint tooltips onto their dark variants; those
 * live in base themes this module never sees, so overriding colours by hand here would have
 * fixed the text and left the caret black on black.
 */
export function editorTheme(scheme: 'light' | 'dark'): Extension {
  return [
    EditorView.theme(
      {
        '&': {
          backgroundColor: 'var(--mantine-color-body)',
          color: 'var(--mantine-color-text)',
          border: '1px solid var(--mantine-color-default-border)',
          borderRadius: 'var(--mantine-radius-sm)',
          fontSize: '13px',
        },
        '.cm-content': {
          fontFamily: 'var(--mantine-font-family-monospace)',
          caretColor: 'var(--mantine-color-text)',
        },
        '.cm-gutters': {
          backgroundColor: 'var(--mantine-color-default-hover)',
          color: PALETTE[scheme].muted,
          border: 'none',
          borderRight: '1px solid var(--mantine-color-default-border)',
        },
        '.cm-activeLineGutter': { backgroundColor: 'transparent' },
      },
      { dark: scheme === 'dark' },
    ),
    syntaxHighlighting(highlightFor(scheme)),
  ]
}
