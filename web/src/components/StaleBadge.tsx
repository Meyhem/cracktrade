import { Badge, Tooltip } from '@mantine/core'
import { IconHistory } from '@tabler/icons-react'
import { explanationOf } from '../lib/glossary'

/**
 * A run made against a version that is no longer the head.
 *
 * This appears on every run row, every chart run-picker entry and every run header, because
 * without it two numbers from two versions look comparable and are not — they came from
 * different strategies wearing the same name.
 */
export function StaleBadge({ version }: { version: number }) {
  return (
    <Tooltip label={`Run against v${version}. ${explanationOf('stale')}`} multiline w={280}>
      <Badge color="gray" leftSection={<IconHistory size={12} />} size="sm" variant="outline">
        stale
      </Badge>
    </Tooltip>
  )
}
