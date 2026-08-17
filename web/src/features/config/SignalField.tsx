import { useRef, useState } from 'react'
import { Alert, Badge, Button, Code, Group, Stack, Text, Textarea } from '@mantine/core'
import { IconBulb, IconWand } from '@tabler/icons-react'
import { putAt, stringAt, type Path } from './document'
import { issuesAt } from './issues'
import { numericLiterals, parenthesiseComparisons, referencedNames } from './signal'
import { anchorId, type SectionProps } from './section'

/**
 * The signal expression editor.
 *
 * A signal is the one field where the engine's central guarantee is visible to the user, so it
 * is presented as a feature rather than as a list of things that fail. Calls, attribute access
 * and subscripting are not rejected expressions — they are not expressible, which is why
 * `close.shift(-1)` cannot be written here and therefore why a number this app prints cannot
 * have been computed from a bar that had not happened yet (spec §2).
 *
 * Three affordances hang off that, all of them addressing mistakes users make constantly:
 * completion over the names actually in scope, the parenthesisation fix applied to the user's
 * own expression, and the reminder that a number written inside an expression is invisible to
 * the optimizer.
 */
export function SignalField({
  label,
  path,
  placeholder,
  section,
}: {
  label: string
  path: Path
  placeholder?: string
  section: SectionProps
}) {
  const dotted = path.join('.')
  const expression = stringAt(section.value, path) ?? ''
  const namespace = section.validation?.namespace ?? []
  const errors = issuesAt(section.validation?.errors ?? [], dotted)
  const [partial, setPartial] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)

  const write = (next: string) => {
    section.onChange(putAt(section.yaml, path, next === '' ? null : next))
  }

  const suggestions =
    partial.length > 0
      ? namespace.filter((name) => name.startsWith(partial) && name !== partial).slice(0, 8)
      : []

  const complete = (name: string) => {
    const caret = input.current?.selectionStart ?? expression.length
    const before = expression.slice(0, caret).replace(/[A-Za-z_]\w*$/, '')
    write(`${before}${name}${expression.slice(caret)}`)
    setPartial('')
  }

  const fixed = parenthesiseComparisons(expression)
  const literals = numericLiterals(expression)
  const used = new Set(referencedNames(expression))

  return (
    <Stack data-field={dotted} gap="xs" id={anchorId(dotted)}>
      <Text fw={500} size="sm">
        {label}
      </Text>

      <Textarea
        aria-label={label}
        error={errors.length > 0}
        onChange={(event) => {
          const next = event.currentTarget.value
          write(next)
          setPartial(wordBefore(next, event.currentTarget.selectionStart))
        }}
        placeholder={placeholder}
        ref={input}
        rows={2}
        styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
        value={expression}
      />

      {suggestions.length > 0 && (
        <Group gap={4}>
          <Text c="dimmed" size="xs">
            complete:
          </Text>
          {suggestions.map((name) => (
            <Button key={name} onClick={() => complete(name)} size="compact-xs" variant="light">
              {name}
            </Button>
          ))}
        </Group>
      )}

      {errors.map((issue) => (
        <Text c="red" key={issue.message} size="xs">
          {issue.message}
        </Text>
      ))}

      {/*
        Offered only alongside an error. The transform is a text edit, not a verdict — the
        engine decides whether the result parses, exactly as it decided about the original.
      */}
      {fixed !== null && errors.length > 0 && (
        <Alert color="blue" icon={<IconWand size={16} />} variant="light">
          <Stack gap={6}>
            <Text size="sm">
              <Code>&amp;</Code> and <Code>|</Code> bind more tightly than comparisons, so each
              comparison needs its own parentheses.
            </Text>
            <Code block>{fixed}</Code>
            <Button onClick={() => write(fixed)} size="compact-sm" variant="light" w="fit-content">
              Apply this
            </Button>
          </Stack>
        </Alert>
      )}

      {literals.length > 0 && (
        <Group align="flex-start" gap={6} wrap="nowrap">
          <IconBulb size={14} />
          <Text c="dimmed" size="xs">
            {literals.join(', ')} {literals.length === 1 ? 'is' : 'are'} written into this
            expression, so an optimization cannot reach {literals.length === 1 ? 'it' : 'them'}. To
            tune a threshold, move it into an indicator and compare against that.
          </Text>
        </Group>
      )}

      {namespace.length > 0 && (
        <Group gap={4}>
          <Text c="dimmed" size="xs">
            in scope:
          </Text>
          {namespace.map((name) => (
            <Badge
              key={name}
              size="xs"
              style={{ cursor: 'pointer', textTransform: 'none' }}
              variant={used.has(name) ? 'filled' : 'default'}
            >
              {name}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  )
}

/** The identifier being typed at the caret, which is what completion is about. */
function wordBefore(text: string, caret: number | null): string {
  const found = /[A-Za-z_]\w*$/.exec(text.slice(0, caret ?? text.length))
  return found?.[0] ?? ''
}
