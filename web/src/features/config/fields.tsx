import type { ReactNode } from 'react'
import { Group, NumberInput, Stack, Text, TextInput } from '@mantine/core'
import { Explain } from '../../components/Explain'
import type { TermKey } from '../../lib/glossary'
import { numberAt, putAt, stringAt, type Path } from './document'
import { anchorId, matching, NO_ISSUES, NO_PATHS, type SectionProps } from './section'
import type { Issue } from '../../api/types'

/**
 * The form's field primitives.
 *
 * Every one of them writes through `putAt`, so a control that is cleared removes its key
 * rather than writing an empty value. Absent and blank are different to this schema: `exit`
 * with no mechanism at all is refused with an explanation, while `stop_loss_pct: null` is a
 * type error about a field the user thought they had cleared.
 */

export function Field({
  children,
  control,
  errors = NO_ISSUES,
  hint,
  label,
  path,
  term,
  warnings = NO_ISSUES,
}: {
  children: ReactNode
  /** The optimizer control, when this field is a number a search could move. */
  control?: ReactNode
  errors?: Issue[]
  /** The resolved value, where the unit is ambiguous enough to need one. */
  hint?: ReactNode
  label: string
  /** The dotted path, used as the scroll anchor the issue summary jumps to. */
  path: string
  /** The glossary entry explaining this field. Absent only where the label is a free name. */
  term?: TermKey
  warnings?: Issue[]
}) {
  return (
    <Stack data-field={path} gap={4} id={anchorId(path)}>
      <Group align="flex-end" gap="sm" justify="space-between" wrap="nowrap">
        <Stack gap={2} style={{ flex: 1 }}>
          <Group gap={4} wrap="nowrap">
            <Text fw={500} size="sm">
              {label}
            </Text>
            {term && <Explain term={term} />}
          </Group>
          {children}
        </Stack>
        {control && <div style={{ paddingBottom: 4 }}>{control}</div>}
      </Group>
      {hint && (
        <Text c="dimmed" size="xs">
          {hint}
        </Text>
      )}
      {errors.map((issue) => (
        <Text c="red" key={issue.message} size="xs">
          {issue.message}
        </Text>
      ))}
      {warnings.map((issue) => (
        <Text c="orange" key={issue.message} size="xs">
          {issue.message}
        </Text>
      ))}
    </Stack>
  )
}

export function NumberField({
  aliases = NO_PATHS,
  control,
  hint,
  label,
  path,
  section,
  step,
  suffix,
  term,
}: {
  /** Other dotted paths the server may address this same field by. */
  aliases?: string[]
  control?: ReactNode
  hint?: ReactNode
  label: string
  /** Where the value lives in the draft. */
  path: Path
  section: SectionProps
  step?: number
  /** Rendered inside the input, because a percentage that looks like a fraction is a footgun. */
  suffix?: string
  term?: TermKey
}) {
  const dotted = path.join('.')
  const addressed = [dotted, ...aliases]
  const current = numberAt(section.value, path)
  const raw = stringAt(section.value, path)

  return (
    <Field
      control={control}
      errors={matching(section.validation?.errors ?? [], addressed)}
      hint={hint}
      label={label}
      path={dotted}
      warnings={matching(section.validation?.warnings ?? [], addressed)}
      {...(term === undefined ? {} : { term })}
    >
      <NumberInput
        aria-label={label}
        onChange={(next) =>
          section.onChange(putAt(section.yaml, path, next === '' ? null : Number(next)))
        }
        rightSection={
          suffix ? (
            <Text c="dimmed" pr={6} size="xs">
              {suffix}
            </Text>
          ) : null
        }
        rightSectionWidth={suffix ? suffix.length * 8 + 12 : undefined}
        size="sm"
        {...(step === undefined ? {} : { step })}
        // A value the file holds as a string is shown as the string it is. The schema does not
        // coerce `"200"` into `200` (spec §3.1), and an input that silently did would present a
        // valid-looking form over a config the engine refuses.
        value={current ?? raw ?? ''}
      />
    </Field>
  )
}

export function TextField({
  aliases = NO_PATHS,
  description,
  label,
  path,
  placeholder,
  section,
  term,
}: {
  aliases?: string[]
  description?: ReactNode
  label: string
  path: Path
  placeholder?: string
  section: SectionProps
  term?: TermKey
}) {
  const dotted = path.join('.')
  const addressed = [dotted, ...aliases]

  return (
    <Field
      errors={matching(section.validation?.errors ?? [], addressed)}
      hint={description}
      label={label}
      path={dotted}
      warnings={matching(section.validation?.warnings ?? [], addressed)}
      {...(term === undefined ? {} : { term })}
    >
      <TextInput
        aria-label={label}
        onChange={(event) => {
          const next = event.currentTarget.value
          section.onChange(putAt(section.yaml, path, next === '' ? null : next))
        }}
        placeholder={placeholder}
        size="sm"
        value={stringAt(section.value, path) ?? ''}
      />
    </Field>
  )
}
