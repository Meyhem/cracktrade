import { Text, Tooltip } from '@mantine/core'
import { IconGitBranch, IconArrowBigUpLine } from '@tabler/icons-react'
import { Link } from 'react-router'
import { explanationOf } from '../lib/glossary'
import type { Lineage } from '../api/types'

/**
 * Where a strategy came from.
 *
 * A promoted strategy holds parameters a search chose, and a forked one holds a copy of
 * somebody's earlier thinking; in both cases the parent is the context needed to read the
 * numbers, so it is a link rather than a note.
 */
export function LineageMarker({ lineage }: { lineage: Lineage }) {
  if (lineage.origin === 'authored' || !lineage.parent_strategy_id) return null

  const promoted = lineage.origin === 'promoted'
  const Icon = promoted ? IconArrowBigUpLine : IconGitBranch
  const label = promoted
    ? `${explanationOf('promote')} Promoted from a run of its parent${lineage.parent_version ? ` (v${lineage.parent_version})` : ''}.`
    : `${explanationOf('fork')} Forked from its parent${lineage.parent_version ? ` at v${lineage.parent_version}` : ''}.`

  return (
    <Tooltip label={label}>
      <Text
        c="dimmed"
        component={Link}
        size="xs"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}
        to={`/strategies/${lineage.parent_strategy_id}`}
      >
        <Icon size={12} />
        {promoted ? 'promoted' : 'forked'}
      </Text>
    </Tooltip>
  )
}
