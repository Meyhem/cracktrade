import { createTheme, type MantineColorsTuple } from '@mantine/core'

/**
 * The palette.
 *
 * Verdict states get named colours here rather than at each call site, because they are the
 * load-bearing signal in the app and must mean the same thing on every screen. Colour is
 * never their only encoding — every verdict surface pairs it with an icon and a word (spec
 * section 12, UI brief section 5.7) — but where colour is used it has to be consistent.
 */

const slate: MantineColorsTuple = [
  '#f5f6f8',
  '#e7e9ee',
  '#ced2db',
  '#b2b9c7',
  '#9aa3b5',
  '#8b95aa',
  '#828da5',
  '#6f7991',
  '#626c83',
  '#535d75',
]

export const theme = createTheme({
  primaryColor: 'indigo',
  colors: { slate },
  fontFamily:
    'Inter, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif',
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  headings: { fontWeight: '600' },
  defaultRadius: 'sm',
  cursorType: 'pointer',
})

/**
 * Verdict colours. `unvalidated` is deliberately not grey: nobody has checked this strategy
 * yet, and a neutral chip reads as "fine" (spec section 14.4 — "unvalidated is not a neutral
 * state and must never render as one").
 */
export const VERDICT_COLOR = {
  credible: 'teal',
  not_credible: 'red',
  unvalidated: 'orange',
  never_run: 'gray',
} as const

export const RUN_STATUS_COLOR = {
  queued: 'gray',
  running: 'blue',
  succeeded: 'teal',
  failed: 'red',
  cancelled: 'gray',
} as const
