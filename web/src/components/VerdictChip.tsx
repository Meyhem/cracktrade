import { Badge, Tooltip } from '@mantine/core'
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconHelpCircle,
} from '@tabler/icons-react'
import { VERDICT_COLOR } from '../theme/theme'
import { termOf, type TermKey } from '../lib/glossary'
import type { VerdictState } from '../api/types'

/**
 * The load-bearing element of the strategy list.
 *
 * Four states, each with an icon as well as a colour, because colour alone fails for a
 * meaningful share of traders and this is the chip they scan the table for.
 *
 * `unvalidated` is styled as a warning rather than a neutral grey. It is the default state
 * of every strategy that has never been walk-forwarded, and it means "nobody has checked
 * this yet" — a grey chip reads as "fine", which is the opposite (spec section 14.4).
 */

const PRESENTATION: Record<VerdictState, { icon: typeof IconCircleCheck; term: TermKey }> = {
  credible: { icon: IconCircleCheck, term: 'credible' },
  not_credible: { icon: IconAlertTriangle, term: 'not_credible' },
  unvalidated: { icon: IconHelpCircle, term: 'unvalidated' },
  never_run: { icon: IconCircleDashed, term: 'never_run' },
}

export function VerdictChip({
  verdict,
  failureCount,
  size = 'sm',
}: {
  verdict: VerdictState
  /** Shown on `not_credible` — a red badge with no count teaches people to ignore it. */
  failureCount?: number | undefined
  size?: 'sm' | 'md' | 'lg'
}) {
  const presentation = PRESENTATION[verdict]
  const { title, plain, catch: caveat } = termOf(presentation.term)
  const Icon = presentation.icon
  const showCount = verdict === 'not_credible' && failureCount !== undefined && failureCount > 0

  return (
    <Tooltip label={caveat ? `${plain} ${caveat}` : plain} multiline w={300} withArrow>
      <Badge
        color={VERDICT_COLOR[verdict]}
        leftSection={<Icon size={13} />}
        size={size}
        variant="light"
      >
        {title}
        {showCount && ` · ${failureCount} failed`}
      </Badge>
    </Tooltip>
  )
}
