import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Select,
  Stack,
  Text,
} from '@mantine/core'
import { IconInfoCircle, IconPlus, IconTrash } from '@tabler/icons-react'
import { listAt, putAt, removeAt, setAt, stringAt } from './document'
import { issuesUnder } from './issues'
import { NumberField, TextField } from './fields'
import { searchIndex, type SectionProps } from './section'
import { OptimizeControl, SectionOptimizeControl } from './OptimizeControl'
import { useMeta } from '../../api/metaContext'

const SOURCES = ['open', 'high', 'low', 'close', 'volume']

/**
 * The indicators list.
 *
 * Each card says which names it contributes to the signal namespace, taken from the engine's
 * registry rather than assembled here: a single-output indicator contributes its own name and
 * a multi-output one contributes `<name>_<output>` and *not* the bare name, which is the kind
 * of rule a user discovers by having their signal rejected.
 *
 * Indicators are also where a strategy's tunable numbers live. A user who wants to search a
 * threshold has to move it out of the signal expression and into one of these, so the two
 * facts are presented next to each other.
 */
export function IndicatorsSection(section: SectionProps) {
  const meta = useMeta()
  const indicators = listAt(section.value, ['indicators'])
  const search = searchIndex(section.validation)

  const add = () => {
    const type = meta.meta.indicators[0]?.type ?? 'sma'
    const parameters = Object.fromEntries(
      (meta.indicator(type)?.parameters ?? []).map((parameter) => [
        parameter.name,
        parameter.default,
      ]),
    )
    section.onChange(
      setAt(section.yaml, ['indicators', indicators.length], {
        name: uniqueName(indicators),
        type,
        ...parameters,
      }),
    )
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Text fw={600}>Indicators</Text>
        <Button
          leftSection={<IconPlus size={14} />}
          onClick={add}
          size="compact-sm"
          variant="light"
        >
          Add indicator
        </Button>
      </Group>

      {indicators.length === 0 && (
        <Alert color="gray" icon={<IconInfoCircle size={16} />} variant="light">
          No indicators. A signal can still be written over the raw price series —{' '}
          <Code>close</Code>, <Code>open</Code> and the rest — but with no indicators there is
          nothing for an optimization to move, because numbers written inside an expression are not
          reachable by the search.
        </Alert>
      )}

      {indicators.map((entry, index) => (
        <IndicatorCard
          entry={entry}
          index={index}
          key={indicatorKey(entry, index)}
          search={search}
          section={section}
        />
      ))}
    </Stack>
  )
}

function IndicatorCard({
  entry,
  index,
  search,
  section,
}: {
  entry: unknown
  index: number
  search: ReturnType<typeof searchIndex>
  section: SectionProps
}) {
  const meta = useMeta()
  const name = stringAt(entry, ['name']) ?? ''
  const type = stringAt(entry, ['type']) ?? ''
  const description = meta.indicator(type)
  const outputs = type && name ? meta.indicatorOutputs(type, name) : []
  // Registry errors are addressed by indicator name, structural ones by list position.
  const errors = [
    ...issuesUnder(section.validation?.errors ?? [], `indicators.${name}`),
    ...issuesUnder(section.validation?.errors ?? [], `indicators.${index}`),
  ]

  const entryPath = ['indicators', index] as const
  const attached = new Set(
    ['name', 'type', ...(description?.parameters ?? []).map((parameter) => parameter.name)].flatMap(
      (key) => [
        `indicators.${index}.${key}`,
        `indicators.${name}.${key}`,
        `indicators.${index}.params.${key}`,
      ],
    ),
  )

  const changeType = (next: string) => {
    // The old type's parameters are meaningless to the new one, and leaving them behind means
    // an "extra inputs are not permitted" error about a field the user never typed.
    let draft = section.yaml
    for (const parameter of description?.parameters ?? []) {
      draft = removeAt(draft, [...entryPath, parameter.name])
    }
    draft = putAt(draft, [...entryPath, 'type'], next)
    for (const parameter of meta.indicator(next)?.parameters ?? []) {
      draft = putAt(draft, [...entryPath, parameter.name], parameter.default)
    }
    section.onChange(draft)
  }

  return (
    <Card padding="sm" withBorder>
      <Stack gap="sm">
        <Group align="flex-start" gap="sm" wrap="nowrap">
          <TextField
            aliases={[`indicators.${name}.name`]}
            label="Name"
            path={[...entryPath, 'name']}
            placeholder="sma_long"
            section={section}
          />
          <Stack gap={2} style={{ minWidth: 160 }}>
            <Text fw={500} size="sm">
              Type
            </Text>
            <Select
              aria-label="Type"
              data={meta.meta.indicators.map((item) => ({
                value: item.type,
                label: `${item.type} — ${item.description}`,
              }))}
              onChange={(next) => next && changeType(next)}
              searchable
              size="sm"
              value={type || null}
            />
          </Stack>
          <ActionIcon
            aria-label={`Remove ${name || 'indicator'}`}
            color="gray"
            mt={24}
            onClick={() => section.onChange(removeAt(section.yaml, [...entryPath]))}
            variant="subtle"
          >
            <IconTrash size={16} />
          </ActionIcon>
        </Group>

        <Group gap="lg" align="flex-start">
          {(description?.parameters ?? []).map((parameter) => (
            <NumberField
              aliases={[
                `indicators.${name}.${parameter.name}`,
                `indicators.${index}.params.${parameter.name}`,
              ]}
              control={
                <OptimizeControl
                  entryPath={[...entryPath]}
                  onChange={section.onChange}
                  parameterKey={parameter.name}
                  searchable={search.get(`indicators.${name}.${parameter.name}`)}
                  sectionPinned={pinned(entry)}
                  value={section.value}
                  yaml={section.yaml}
                />
              }
              key={parameter.name}
              label={parameter.name}
              path={[...entryPath, parameter.name]}
              section={section}
            />
          ))}
          {description?.uses_source && (
            <Stack gap={2} style={{ minWidth: 140 }}>
              <Text fw={500} size="sm">
                source
              </Text>
              <Select
                aria-label={`Source for ${name || 'indicator'}`}
                data={SOURCES}
                onChange={(next) =>
                  section.onChange(putAt(section.yaml, [...entryPath, 'source'], next))
                }
                size="sm"
                value={stringAt(entry, ['source']) ?? 'close'}
              />
            </Stack>
          )}
        </Group>

        <Group gap="xs" justify="space-between">
          <Group gap={4}>
            <Text c="dimmed" size="xs">
              contributes:
            </Text>
            {outputs.map((output) => (
              <Badge key={output} size="xs" style={{ textTransform: 'none' }} variant="default">
                {output}
              </Badge>
            ))}
          </Group>
          <SectionOptimizeControl
            entryPath={[...entryPath]}
            label={name || 'this indicator'}
            onChange={section.onChange}
            value={section.value}
            yaml={section.yaml}
          />
        </Group>

        {/*
          Whatever did not land on one of the inputs above — a typo'd parameter name, which
          belongs to no field because no field is named that. Shown on the card rather than
          left to the summary panel, where it would be the only issue with no visible home.
        */}
        {errors
          .filter((issue) => !attached.has(issue.path))
          .map((issue) => (
            <Text c="red" key={`${issue.path}:${issue.message}`} size="xs">
              {issue.path}: {issue.message}
            </Text>
          ))}
      </Stack>
    </Card>
  )
}

function pinned(entry: unknown): boolean {
  return (entry as { optimize?: unknown } | null)?.optimize === false
}

/** A stable-enough key: the name if there is one, since reordering by index remounts inputs. */
function indicatorKey(entry: unknown, index: number): string {
  return stringAt(entry, ['name']) ?? `indicator-${index}`
}

function uniqueName(indicators: readonly unknown[]): string {
  const taken = new Set(indicators.map((entry) => stringAt(entry, ['name'])))
  for (let suffix = indicators.length + 1; ; suffix += 1) {
    const candidate = `indicator_${suffix}`
    if (!taken.has(candidate)) return candidate
  }
}
