import { useMemo, useState } from 'react'
import {
  Anchor,
  Badge,
  Button,
  Center,
  Chip,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from '@mantine/core'
import { IconArrowsSort, IconSearch, IconSparkles, IconUpload, IconWand } from '@tabler/icons-react'
import { Link, useSearchParams } from 'react-router'
import { useDebouncedValue } from '@mantine/hooks'
import { ProblemAlert } from '../../components/ProblemAlert'
import { Explain, ExplainedLabel } from '../../components/Explain'
import { type TermKey } from '../../lib/glossary'
import { VerdictChip } from '../../components/VerdictChip'
import { LineageMarker } from '../../components/LineageMarker'
import { EmptyState } from '../../components/EmptyState'
import { dateOnly, relative, runCounts, runKindLabel } from '../../lib/format'
import { useStrategies } from './queries'
import { NewStrategyModal } from './NewStrategyModal'
import { ImportStrategyModal } from './ImportStrategyModal'
import { ComposeStrategyModal } from './ComposeStrategyModal'
import { GenerateStrategyModal } from './GenerateStrategyModal'
import type { StrategyRow, VerdictState } from '../../api/types'

/**
 * The landing page.
 *
 * A table rather than cards: these rows are compared column against column — which one has
 * been validated, which was run most recently — and a card grid makes that comparison
 * impossible while implying each strategy is a self-contained thing to browse.
 */

type SortKey = 'name' | 'ticker' | 'verdict' | 'last_run_at' | 'versions'

const VERDICT_FILTERS: { value: VerdictState | 'all'; label: string; totalKey: string }[] = [
  { value: 'all', label: 'All', totalKey: 'all' },
  { value: 'credible', label: 'Credible', totalKey: 'credible' },
  { value: 'not_credible', label: 'Not credible', totalKey: 'not_credible' },
  { value: 'unvalidated', label: 'Unvalidated', totalKey: 'unvalidated' },
  { value: 'never_run', label: 'Never run', totalKey: 'never_run' },
]

/** Sorted so the states that need attention are adjacent, not alphabetical. */
const VERDICT_ORDER: Record<VerdictState, number> = {
  not_credible: 0,
  unvalidated: 1,
  never_run: 2,
  credible: 3,
}

function compare(a: StrategyRow, b: StrategyRow, key: SortKey): number {
  switch (key) {
    case 'name':
      return a.name.localeCompare(b.name)
    case 'ticker':
      return (a.ticker ?? '').localeCompare(b.ticker ?? '')
    case 'verdict':
      return VERDICT_ORDER[a.verdict] - VERDICT_ORDER[b.verdict]
    case 'versions':
      return a.versions - b.versions
    case 'last_run_at':
      // Never-run strategies sort last whichever way the column is pointed: they have no
      // date, and inventing one would order them among strategies that do.
      if (!a.last_run_at && !b.last_run_at) return 0
      if (!a.last_run_at) return 1
      if (!b.last_run_at) return -1
      return a.last_run_at.localeCompare(b.last_run_at)
  }
}

function SortableHeader({
  children,
  column,
  term,
  sort,
  onSort,
}: {
  children: React.ReactNode
  column: SortKey
  term: TermKey
  sort: { key: SortKey; descending: boolean }
  onSort: (key: SortKey) => void
}) {
  const active = sort.key === column
  return (
    <Table.Th>
      <Group gap={4} wrap="nowrap">
        {/* The sort control and the explanation are separate buttons on purpose: nesting the
            info icon inside the sort button would make asking what a column means also
            re-sort the table. */}
        <UnstyledButton onClick={() => onSort(column)}>
          <Group gap={4} wrap="nowrap">
            <Text fw={600} size="sm">
              {children}
            </Text>
            <IconArrowsSort opacity={active ? 1 : 0.3} size={12} />
          </Group>
        </UnstyledButton>
        <Explain term={term} />
      </Group>
    </Table.Th>
  )
}

function LastRunCell({ row }: { row: StrategyRow }) {
  if (!row.last_run_kind || !row.last_run_at) {
    return (
      <Text c="dimmed" size="sm">
        never run
      </Text>
    )
  }

  return (
    <Stack gap={0}>
      <Text size="sm">{runKindLabel(row.last_run_kind)}</Text>
      <Text c="dimmed" size="xs" title={row.last_run_at}>
        {relative(row.last_run_at)}
        {row.last_run_status && row.last_run_status !== 'succeeded' && ` · ${row.last_run_status}`}
      </Text>
    </Stack>
  )
}

export function StrategyListPage() {
  const [params, setParams] = useSearchParams()
  const search = params.get('q') ?? ''
  const verdict = (params.get('verdict') ?? 'all') as VerdictState | 'all'
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({
    key: 'last_run_at',
    descending: true,
  })
  const [newOpen, setNewOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [composeOpen, setComposeOpen] = useState(false)
  const [writeOpen, setWriteOpen] = useState(false)

  // The server does the filtering, so the request is debounced rather than fired per
  // keystroke; the input itself stays uncontrolled-fast.
  const [debouncedSearch] = useDebouncedValue(search, 250)
  const { data, error, isPending } = useStrategies({
    ...(debouncedSearch ? { search: debouncedSearch } : {}),
    ...(verdict === 'all' ? {} : { verdict }),
  })

  const rows = useMemo(() => {
    if (!data) return []
    const sorted = [...data.strategies].sort((a, b) => compare(a, b, sort.key))
    return sort.descending ? sorted.reverse() : sorted
  }, [data, sort])

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const onSort = (key: SortKey) =>
    setSort((current) =>
      current.key === key ? { key, descending: !current.descending } : { key, descending: false },
    )

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Strategies</Title>
        <Group gap="xs">
          <Button
            leftSection={<IconUpload size={16} />}
            onClick={() => setImportOpen(true)}
            variant="default"
          >
            Import
          </Button>
          <Button
            leftSection={<IconWand size={16} />}
            onClick={() => setWriteOpen(true)}
            variant="light"
          >
            Write with Claude
          </Button>
          <Button
            leftSection={<IconSparkles size={16} />}
            onClick={() => setComposeOpen(true)}
            variant="light"
          >
            Compose from scratch
          </Button>
          <Button onClick={() => setNewOpen(true)}>New strategy</Button>
        </Group>
      </Group>

      <Group justify="space-between">
        <TextInput
          leftSection={<IconSearch size={16} />}
          onChange={(event) => setParam('q', event.currentTarget.value)}
          placeholder="Search name or ticker"
          value={search}
          w={280}
        />
        <Chip.Group
          multiple={false}
          onChange={(value) => setParam('verdict', value === 'all' ? '' : String(value))}
          value={verdict}
        >
          <Group gap="xs">
            {VERDICT_FILTERS.map((filter) => (
              <Chip key={filter.value} size="sm" value={filter.value} variant="light">
                {filter.label}
                {data && ` (${data.totals[filter.totalKey] ?? 0})`}
              </Chip>
            ))}
          </Group>
        </Chip.Group>
      </Group>

      {error && <ProblemAlert error={error} />}

      {isPending && (
        <Center py="xl">
          <Loader />
        </Center>
      )}

      {data && rows.length === 0 && (
        <EmptyState
          action={<Button onClick={() => setNewOpen(true)}>New strategy</Button>}
          title={search || verdict !== 'all' ? 'Nothing matches this filter' : 'No strategies yet'}
        >
          {search || verdict !== 'all'
            ? 'Clear the search or the verdict filter to see everything.'
            : 'A strategy is a ticker, a date range, and one entry and exit rule. Create one, back it test, and then walk-forward it before believing anything it prints.'}
        </EmptyState>
      )}

      {data && rows.length > 0 && (
        <Table highlightOnHover striped>
          <Table.Thead>
            <Table.Tr>
              <SortableHeader column="name" onSort={onSort} sort={sort} term="strategy_name">
                Name
              </SortableHeader>
              <SortableHeader column="ticker" onSort={onSort} sort={sort} term="ticker">
                Ticker
              </SortableHeader>
              <Table.Th>
                <ExplainedLabel term="date_range" />
              </Table.Th>
              <SortableHeader column="last_run_at" onSort={onSort} sort={sort} term="last_run">
                Last run
              </SortableHeader>
              <Table.Th>
                <ExplainedLabel term="runs_count" />
              </Table.Th>
              <SortableHeader column="versions" onSort={onSort} sort={sort} term="version">
                Versions
              </SortableHeader>
              <SortableHeader column="verdict" onSort={onSort} sort={sort} term="verdict">
                Verdict
              </SortableHeader>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => (
              <Table.Tr key={row.id}>
                <Table.Td>
                  <Group gap="xs">
                    <Anchor component={Link} fw={500} size="sm" to={`/strategies/${row.id}`}>
                      {row.name}
                    </Anchor>
                    <LineageMarker lineage={row.lineage} />
                  </Group>
                </Table.Td>
                <Table.Td>{row.ticker ?? '—'}</Table.Td>
                <Table.Td>
                  <Text size="sm">
                    {dateOnly(row.start_date)} → {dateOnly(row.end_date)}
                  </Text>
                  {/* Only when it is not daily. A badge on every row would be noise on the
                      overwhelmingly common case and would stop being read before it mattered. */}
                  {row.interval !== '1d' && (
                    <Badge color="grape" size="xs" tt="none" variant="light">
                      {row.interval}
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <LastRunCell row={row} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm">
                    {runCounts({
                      optimize: row.optimize_runs,
                      backtest: row.backtest_runs,
                      walk_forward: row.walk_forward_runs,
                      evolve: row.evolve_runs,
                    })}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">v{row.head_version}</Text>
                </Table.Td>
                <Table.Td>
                  <VerdictChip verdict={row.verdict} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <NewStrategyModal onClose={() => setNewOpen(false)} opened={newOpen} />
      <ComposeStrategyModal onClose={() => setComposeOpen(false)} opened={composeOpen} />
      <GenerateStrategyModal onClose={() => setWriteOpen(false)} opened={writeOpen} />
      <ImportStrategyModal onClose={() => setImportOpen(false)} opened={importOpen} />
    </Stack>
  )
}
