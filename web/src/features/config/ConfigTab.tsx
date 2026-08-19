import { useEffect, useState } from 'react'
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  List,
  Loader,
  SegmentedControl,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { IconAlertTriangle, IconInfoCircle, IconWand } from '@tabler/icons-react'
import { useValidatedYaml } from '../../api/config'
import { useStrategyContext } from '../strategy/context'
import { ConfigForm } from './ConfigForm'
import { YamlPane } from './YamlPane'
import { SaveVersionModal } from './SaveVersionModal'
import { readConfig } from './document'
import { anchorId, type SectionProps } from './section'
import { describe, saveBlockedBecause, unattached } from './issues'
import { useDraft } from './draft'
import { GenerateModal } from '../authoring/GenerateModal'
import type { Issue } from '../../api/types'

/** Long enough that a round trip is not fired per keystroke, short enough to feel live. */
const VALIDATE_AFTER_MS = 400

/**
 * The configuration editor.
 *
 * One draft, held as text, projected two ways. The form is a set of controls over the same
 * bytes the YAML pane shows, so switching modes is a change of view rather than a conversion —
 * and an edit made in the form leaves the comments and layout of the file it was made in alone.
 *
 * Validity is the server's answer and only the server's. Nothing here decides whether a
 * configuration is a strategy; it asks, debounced, and renders what comes back against the
 * fields the answer names. The consequence worth stating is that "checking" is a real state
 * with its own presentation: while a round trip is in flight this screen does not know, and
 * says so, rather than showing the previous answer as though it still applied.
 */
export function ConfigTab() {
  const strategy = useStrategyContext()
  const draft = useDraft(strategy.id, strategy.head)
  const [mode, setMode] = useState<'form' | 'yaml'>('form')
  const [saving, setSaving] = useState(false)
  const [asking, setAsking] = useState(false)

  const [settled] = useDebouncedValue(draft.yaml, VALIDATE_AFTER_MS)
  const validation = useValidatedYaml(settled)
  // The answer describes `settled`, not what is on screen. Treating a stale answer as current
  // is how a save gets offered for text nobody has checked.
  const checking = settled !== draft.yaml || validation.isFetching

  useUnloadWarning(draft.dirty)

  const parsed = readConfig(draft.yaml)
  const errors = validation.data?.errors ?? []
  const warnings = validation.data?.warnings ?? []
  const blocked = saveBlockedBecause({
    dirty: draft.dirty,
    headMoved: draft.headMoved,
    checking,
    validation: validation.data,
  })

  // The sections get the last answer even while it is being refreshed, so an error the user is
  // in the middle of fixing does not blink out on every keystroke. `blocked` above is what
  // gates the save, and it does not accept a stale answer — the two uses are different claims.
  const section: SectionProps = {
    yaml: draft.yaml,
    value: parsed.parsed ? parsed.value : {},
    validation: validation.data,
    onChange: draft.edit,
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="sm">
          <SegmentedControl
            data={[
              { label: 'Form', value: 'form' },
              { label: 'YAML', value: 'yaml' },
            ]}
            onChange={(next) => setMode(next === 'yaml' ? 'yaml' : 'form')}
            size="sm"
            value={mode}
          />
          <Text c="dimmed" size="sm">
            based on v{draft.baseVersion}
          </Text>
          {draft.dirty && (
            <Badge color="orange" size="sm" variant="light">
              unsaved
            </Badge>
          )}
          {checking && draft.dirty && <Loader size="xs" />}
        </Group>

        <Group gap="xs">
          {draft.dirty && (
            <Button color="gray" onClick={draft.discard} variant="subtle">
              Discard changes
            </Button>
          )}
          {/*
            A proposal lands in this draft, never in a version. Whatever comes back is text in
            the editor the user is already looking at: they read it, diff it through the save
            dialog, and keep it or discard it with the same two controls as any other edit.
          */}
          <Button
            leftSection={<IconWand size={16} />}
            onClick={() => setAsking(true)}
            variant="light"
          >
            Ask Claude
          </Button>
          <Tooltip disabled={blocked === null} label={blocked ?? ''} withArrow>
            <div>
              <Button disabled={blocked !== null} onClick={() => setSaving(true)}>
                Save as v{draft.baseVersion + 1}
              </Button>
            </div>
          </Tooltip>
        </Group>
      </Group>

      {/*
        Never a dead control. The reason sits beside the button whenever the button is
        disabled — including "nothing has changed yet", which is the state a user meets first
        and the one most likely to read as the app being broken.
      */}
      {blocked !== null && (
        <Text c="dimmed" size="xs" ta="right">
          {blocked}
        </Text>
      )}

      {draft.headMoved && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />} variant="light">
          This draft was started against v{draft.baseVersion}, and the head is now v
          {strategy.head.version}. Saving would be refused rather than silently overwriting the
          newer version, so copy anything you want to keep and discard this draft.
        </Alert>
      )}

      {!parsed.parsed && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />} variant="light">
          <Stack gap={4}>
            <Text size="sm">
              This file does not parse as YAML
              {parsed.line === null ? '' : `, starting at line ${parsed.line}`}: {parsed.message}
            </Text>
            <Text size="sm">
              The form is hidden while that is true — it would have to guess at what the fields are,
              and a form showing fields that are not in your file is worse than no form.
            </Text>
            {mode === 'form' && (
              <Anchor component="button" onClick={() => setMode('yaml')} size="sm" type="button">
                Fix it in the YAML editor
              </Anchor>
            )}
          </Stack>
        </Alert>
      )}

      <IssueSummary
        errors={errors}
        onJump={mode === 'form' ? jumpToField : null}
        warnings={warnings}
      />

      {mode === 'yaml' ? (
        <YamlPane errors={errors} onChange={draft.edit} value={draft.yaml} warnings={warnings} />
      ) : (
        parsed.parsed && <ConfigForm {...section} />
      )}

      <GenerateModal
        adoptLabel="Load into the editor"
        allowInvalid
        baseYaml={draft.yaml}
        onAdopt={(yaml) => {
          draft.edit(yaml)
          setAsking(false)
        }}
        onClose={() => setAsking(false)}
        opened={asking}
        placeholder="Tighten the exit: use an ATR stop instead of the fixed one, and add a volume filter to the entry."
        title="Ask Claude to change this strategy"
      />

      <SaveVersionModal
        baseVersion={draft.baseVersion}
        onClose={() => setSaving(false)}
        onSaved={draft.discard}
        opened={saving}
        yaml={draft.yaml}
      />
    </Stack>
  )
}

/**
 * Every issue in one place, in the engine's own words.
 *
 * The brief asks for both: attached to the field *and* collected here. The collection is what
 * makes "four errors" countable and what a user scans before scrolling; the attachment is what
 * makes each one actionable. Neither replaces the other.
 */
function IssueSummary({
  errors,
  onJump,
  warnings,
}: {
  errors: Issue[]
  onJump: ((path: string) => void) | null
  warnings: Issue[]
}) {
  if (errors.length === 0 && warnings.length === 0) return null

  return (
    <Stack gap="xs">
      {errors.length > 0 && (
        <Card padding="sm" style={{ borderColor: 'var(--mantine-color-red-6)' }} withBorder>
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Please fix the following issues in your strategy file:
            </Text>
            <List size="sm" spacing={2}>
              {errors.map((issue) => (
                <List.Item key={`${issue.path}:${issue.message}`}>
                  {onJump && issue.path ? (
                    <Anchor component="button" onClick={() => onJump(issue.path)} type="button">
                      {describe(issue)}
                    </Anchor>
                  ) : (
                    describe(issue)
                  )}
                </List.Item>
              ))}
            </List>
            {unattached(errors).length > 0 && (
              <Text c="dimmed" size="xs">
                Issues with no field named are about the configuration as a whole.
              </Text>
            )}
          </Stack>
        </Card>
      )}

      {warnings.length > 0 && (
        <Alert color="orange" icon={<IconInfoCircle size={16} />} variant="light">
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Warnings — these never block a save or a run:
            </Text>
            {warnings.map((issue) => (
              <Text key={`${issue.path}:${issue.message}`} size="sm">
                {describe(issue)}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}
    </Stack>
  )
}

function jumpToField(path: string): void {
  const element = document.getElementById(anchorId(path))
  element?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  element?.querySelector('input, textarea')?.dispatchEvent(new Event('focus'))
}

/**
 * The browser's own warning, which is the only one that can stop a tab closing.
 *
 * Registered only while there is something to lose. A page that always warns is a page whose
 * warning is dismissed without reading, and this one has to be read the once it matters.
 */
function useUnloadWarning(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
}
