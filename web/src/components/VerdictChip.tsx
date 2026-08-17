import { Badge, Tooltip } from '@mantine/core'
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconHelpCircle,
} from '@tabler/icons-react'
import { VERDICT_COLOR } from '../theme/theme'
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

const PRESENTATION: Record<
  VerdictState,
  { label: string; icon: typeof IconCircleCheck; explain: string }
> = {
  credible: {
    label: 'Credible',
    icon: IconCircleCheck,
    explain: 'A walk-forward run against the current version passed every robustness check.',
  },
  not_credible: {
    label: 'Not credible',
    icon: IconAlertTriangle,
    explain: 'A walk-forward run ran against the current version and failed at least one check.',
  },
  unvalidated: {
    label: 'Unvalidated',
    icon: IconHelpCircle,
    explain:
      'No walk-forward run has been made against the current version. Nobody has checked this yet — earlier runs describe a configuration that has since changed.',
  },
  never_run: {
    label: 'Never run',
    icon: IconCircleDashed,
    explain: 'This strategy has never been run.',
  },
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
  const Icon = presentation.icon
  const showCount = verdict === 'not_credible' && failureCount !== undefined && failureCount > 0

  return (
    <Tooltip label={presentation.explain} multiline w={300} withArrow>
      <Badge
        color={VERDICT_COLOR[verdict]}
        leftSection={<Icon size={13} />}
        size={size}
        variant="light"
      >
        {presentation.label}
        {showCount && ` · ${failureCount} failed`}
      </Badge>
    </Tooltip>
  )
}
