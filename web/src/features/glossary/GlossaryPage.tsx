import { useEffect, useState } from 'react'
import { Card, Stack, Text, TextInput, Title } from '@mantine/core'
import { IconSearch } from '@tabler/icons-react'
import { EmptyState } from '../../components/EmptyState'
import { TERM_GROUPS, termsIn, type Term, type TermGroup, type TermKey } from '../../lib/glossary'

/**
 * Every word the app uses, in one place.
 *
 * A second view of `lib/glossary.ts` and nothing more — no copy lives here, so the definition
 * on this page and the one in the tooltip cannot disagree. It exists because a hover icon
 * answers a question the reader already knew to ask, and someone who has never read a backtest
 * needs somewhere to start before they know which word is the one confusing them.
 *
 * Each entry carries an `id`, so a definition can be linked to directly — `/glossary#sharpe`.
 */

const HEADINGS: Record<TermGroup, { title: string; blurb: string }> = {
  results: {
    title: 'What it made',
    blurb: 'The numbers describing how the strategy performed.',
  },
  risk: {
    title: 'What it cost to make it',
    blurb: 'How much the account moved around, and what trading it took out.',
  },
  validation: {
    title: 'Whether to believe it',
    blurb: 'The checks that separate a real edge from a pattern fitted to the past.',
  },
  search: {
    title: 'How the settings were chosen',
    blurb: 'What the optimizer did, and what it was aiming at.',
  },
  data: {
    title: 'The data underneath',
    blurb: 'Which prices were used, and how complete they were.',
  },
  admin: { title: 'Getting around', blurb: 'Names, versions and the rest of the furniture.' },
}

export function GlossaryPage() {
  const [query, setQuery] = useState('')

  // The browser's own hash scrolling fires before this page has rendered, so `/glossary#sharpe`
  // lands at the top and looks like a broken link. Scroll once the entries actually exist.
  useEffect(() => {
    const id = window.location.hash.slice(1)
    if (id === '') return
    document.getElementById(id)?.scrollIntoView({ block: 'center' })
  }, [])

  const needle = query.trim().toLowerCase()
  const matches = ({ term }: { term: Term }) =>
    needle === '' ||
    term.title.toLowerCase().includes(needle) ||
    term.plain.toLowerCase().includes(needle) ||
    (term.catch ?? '').toLowerCase().includes(needle)

  const sections = TERM_GROUPS.map((group) => ({
    group,
    entries: termsIn(group).filter(matches),
  })).filter((section) => section.entries.length > 0)

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <Title order={3}>Glossary</Title>
        <Text c="dimmed" size="sm">
          Every term this app uses, in plain language. Nothing here is advice about what to buy — it
          explains what each number measures, and where each one can mislead you.
        </Text>
      </Stack>

      <TextInput
        leftSection={<IconSearch size={16} />}
        onChange={(event) => setQuery(event.currentTarget.value)}
        placeholder="Search terms"
        value={query}
      />

      {sections.length === 0 ? (
        <EmptyState title={`Nothing matches “${query.trim()}”`}>
          Try a word from the screen you were looking at, such as “drawdown” or “fold”.
        </EmptyState>
      ) : (
        sections.map((section) => (
          <Stack gap="xs" key={section.group}>
            <Title order={4}>{HEADINGS[section.group].title}</Title>
            <Text c="dimmed" size="sm">
              {HEADINGS[section.group].blurb}
            </Text>
            <Stack gap="xs">
              {section.entries.map((entry) => (
                <Entry key={entry.key} term={entry.term} termKey={entry.key} />
              ))}
            </Stack>
          </Stack>
        ))
      )}
    </Stack>
  )
}

function Entry({ term, termKey }: { term: Term; termKey: TermKey }) {
  return (
    <Card id={termKey} padding="md" withBorder>
      <Stack gap={4}>
        <Text fw={600} size="sm">
          {term.title}
        </Text>
        <Text size="sm">{term.plain}</Text>
        {term.catch && (
          <Text c="orange" size="sm">
            Watch out: {term.catch}
          </Text>
        )}
      </Stack>
    </Card>
  )
}
