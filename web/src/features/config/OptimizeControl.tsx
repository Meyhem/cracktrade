import { Badge, Group, NumberInput, Popover, Switch, Text, UnstyledButton } from '@mantine/core'
import { IconLock, IconSearch } from '@tabler/icons-react'
import { putAt, removeAt, at, type Path } from './document'
import { ratio } from '../../lib/format'
import type { SearchableParameter } from '../../api/types'

/**
 * What an optimization is allowed to do to one number.
 *
 * This range is the search: it is what the optimizer may actually try, and until now it was
 * invisible until a run finished and reported which parameters ended up on a bound. Showing
 * `14 → search 7–21` beside the field turns "optimize: true" from a flag into a statement
 * about the experiment being run.
 *
 * The range comes from the server's `searchable_parameters`, never from multiplying by 0.5
 * here. The default spread is the engine's rule (spec §9.1) and it has exceptions — a baseline
 * of zero has no proportional range and gets an absolute one — so a client that reimplemented
 * it would be right about most fields and quietly wrong about the rest.
 */
export function OptimizeControl({
  entryPath,
  parameterKey,
  searchable,
  sectionPinned,
  value,
  yaml,
  onChange,
}: {
  /** The config path of the entry that owns the `optimize` block: an indicator, or `exit`. */
  entryPath: Path
  /** The field's key within that entry, which is how `optimize` addresses it. */
  parameterKey: string
  /** The engine's own answer for this parameter, absent when it is not being searched. */
  searchable: SearchableParameter | undefined
  sectionPinned: boolean
  /** The parsed draft, for reading the current `optimize` override. */
  value: unknown
  yaml: string
  onChange: (yaml: string) => void
}) {
  const override = at(value, [...entryPath, 'optimize', parameterKey])
  const pinned = sectionPinned || override === false
  const bounds = readBounds(override)
  const configured = at(value, [...entryPath, parameterKey]) !== undefined

  const setPinned = (next: boolean) => {
    const path = [...entryPath, 'optimize', parameterKey]
    onChange(next ? putAt(yaml, path, false) : removeAt(yaml, path))
  }

  const setBounds = (next: { min: number; max: number } | null) => {
    const path = [...entryPath, 'optimize', parameterKey]
    if (next === null) {
      onChange(removeAt(yaml, path))
      return
    }
    onChange(putAt(putAt(yaml, [...path, 'min'], next.min), [...path, 'max'], next.max))
  }

  // An optional field that is not set has nothing to search, and offering a "searched" toggle
  // over it says the opposite. `exit.trailing_stop_pct` with no value is not a stop being
  // tuned between bounds — it is a stop that does not exist.
  if (!configured) return null

  if (sectionPinned) {
    return (
      <Badge color="gray" leftSection={<IconLock size={11} />} size="sm" variant="light">
        section pinned
      </Badge>
    )
  }

  return (
    <Group gap="xs" wrap="nowrap">
      <Switch
        aria-label={`Search ${parameterKey}`}
        checked={!pinned}
        onChange={(event) => setPinned(!event.currentTarget.checked)}
        size="xs"
      />
      {pinned ? (
        <Text c="dimmed" size="xs">
          pinned — the search will not move this
        </Text>
      ) : (
        <Popover position="bottom-end" shadow="md" width={260} withArrow>
          <Popover.Target>
            <UnstyledButton>
              <Group gap={4} wrap="nowrap">
                <IconSearch size={12} />
                <Text size="xs">
                  {searchable
                    ? `search ${ratio(searchable.low)}–${ratio(searchable.high)}`
                    : 'search range unknown'}
                </Text>
                {bounds && (
                  <Badge size="xs" variant="light">
                    explicit
                  </Badge>
                )}
              </Group>
            </UnstyledButton>
          </Popover.Target>
          <Popover.Dropdown>
            <Text mb="xs" size="xs">
              {bounds
                ? 'Explicit bounds. Clear them to fall back to the engine default.'
                : 'The engine default is ±50% of the configured value. Set bounds to override it.'}
            </Text>
            <Group gap="xs" wrap="nowrap">
              <NumberInput
                aria-label={`Minimum for ${parameterKey}`}
                onChange={(next) =>
                  setBounds({
                    min: Number(next),
                    max: bounds?.max ?? searchable?.high ?? Number(next),
                  })
                }
                placeholder={searchable ? String(searchable.low) : 'min'}
                size="xs"
                value={bounds?.min ?? ''}
              />
              <NumberInput
                aria-label={`Maximum for ${parameterKey}`}
                onChange={(next) =>
                  setBounds({
                    min: bounds?.min ?? searchable?.low ?? Number(next),
                    max: Number(next),
                  })
                }
                placeholder={searchable ? String(searchable.high) : 'max'}
                size="xs"
                value={bounds?.max ?? ''}
              />
            </Group>
            {bounds && (
              <UnstyledButton mt="xs" onClick={() => setBounds(null)}>
                <Text size="xs" td="underline">
                  Use the default range
                </Text>
              </UnstyledButton>
            )}
          </Popover.Dropdown>
        </Popover>
      )}
    </Group>
  )
}

/** A whole entry pinned at once, which is what `optimize: false` on the entry means. */
export function SectionOptimizeControl({
  entryPath,
  label,
  value,
  yaml,
  onChange,
}: {
  entryPath: Path
  label: string
  value: unknown
  yaml: string
  onChange: (yaml: string) => void
}) {
  const pinned = at(value, [...entryPath, 'optimize']) === false

  return (
    <Switch
      checked={pinned}
      label={`Pin all of ${label}`}
      onChange={(event) => {
        const path = [...entryPath, 'optimize']
        onChange(event.currentTarget.checked ? putAt(yaml, path, false) : removeAt(yaml, path))
      }}
      size="xs"
    />
  )
}

function readBounds(override: unknown): { min: number; max: number } | null {
  if (override === null || typeof override !== 'object') return null
  const record = override as Record<string, unknown>
  const min = record.min
  const max = record.max
  if (typeof min !== 'number' || typeof max !== 'number') return null
  return { min, max }
}
