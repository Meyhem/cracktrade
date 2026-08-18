import { ActionIcon, Group, Stack, Text, Tooltip } from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'
import { termOf, type TermKey } from '../lib/glossary'

/**
 * The plain-language explanation of one term, wherever that term appears.
 *
 * Two details here are load-bearing rather than stylistic:
 *
 * - **`events.touch`.** Mantine's tooltip is hover-only by default, and a hover-only
 *   explanation does not exist on a tablet. Every one of the hand-rolled tooltips this
 *   component replaces had that defect.
 * - **`ActionIcon` rather than a glyph in a `<Text>`.** A button is focusable natively, so the
 *   explanation is reachable by keyboard and announced with a name; the `ⓘ` character that was
 *   here before was neither, and read as punctuation to a screen reader.
 *
 * Nothing load-bearing goes *inside* the tooltip. It is opt-in, and a reader scanning a table
 * will not open it — so anything that changes what someone does with a number stays visible on
 * the page, the way the chart captions and the limits panel already do.
 */
export function Explain({ term }: { term: TermKey }) {
  const { title, plain, catch: caveat } = termOf(term)

  return (
    <Tooltip
      events={{ hover: true, focus: true, touch: true }}
      label={
        <Stack gap={4}>
          <Text fw={600} size="xs">
            {title}
          </Text>
          <Text size="xs">{plain}</Text>
          {caveat && (
            <Text c="yellow.4" size="xs">
              Watch out: {caveat}
            </Text>
          )}
        </Stack>
      }
      multiline
      w={300}
      withArrow
    >
      <ActionIcon aria-label={`What does ${title} mean?`} color="gray" size="xs" variant="subtle">
        <IconInfoCircle size={14} />
      </ActionIcon>
    </Tooltip>
  )
}

/**
 * A label with its explanation beside it.
 *
 * `label` overrides the glossary's own title for the places where the screen says something
 * shorter than the dictionary does — a column header reading "Excess" against a term titled
 * "Excess over buy-and-hold". The tooltip still shows the full title, so the two never drift
 * into looking like different things.
 */
export function ExplainedLabel({
  term,
  label,
  size = 'sm',
}: {
  term: TermKey
  label?: string
  size?: 'xs' | 'sm'
}) {
  return (
    <Group gap={4} wrap="nowrap">
      <Text size={size}>{label ?? termOf(term).title}</Text>
      <Explain term={term} />
    </Group>
  )
}
