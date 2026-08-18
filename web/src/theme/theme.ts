import { createTheme, Tooltip, type MantineColorsTuple } from '@mantine/core'

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
  components: {
    /**
     * Tooltips are pinned to one surface in both colour schemes.
     *
     * Mantine's default inverts the tooltip against the page — near-black in light mode, but
     * `gray-2` with black text in dark mode. That flip breaks any tooltip whose body carries
     * colour: the `Watch out:` caveat in `Explain` is a light yellow chosen to read on a dark
     * surface, and on `gray-2` it fell to roughly 1.3:1 — invisible on exactly the sentence
     * that says a number is not what it looks like.
     *
     * `dark.9` (#141414) is the same value in both schemes, so a colour picked inside a
     * tooltip contrasts the same way everywhere. The shadow does the separating work the
     * inversion used to do, since #141414 against the dark body (#242424) is a quiet edge.
     */
    Tooltip: Tooltip.extend({
      defaultProps: { color: 'dark.9' },
      styles: { tooltip: { boxShadow: 'var(--mantine-shadow-md)' } },
    }),
  },
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
