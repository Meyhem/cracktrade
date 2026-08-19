import { useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Code,
  Group,
  List,
  Loader,
  Modal,
  Stack,
  Text,
  Textarea,
  Tooltip,
} from '@mantine/core'
import { IconAlertTriangle, IconCheck, IconInfoCircle } from '@tabler/icons-react'
import { ProblemAlert } from '../../components/ProblemAlert'
import { describe } from '../config/issues'
import type { GenerateConfigResponse } from '../../api/types'
import { useGenerateConfig } from './queries'

/**
 * Describe a strategy; read what came back; decide whether to keep it.
 *
 * Three properties of this dialog are load-bearing rather than stylistic.
 *
 * **Nothing is created until the user says so.** The server writes a file and stores nothing;
 * this shows it and stores nothing either. Adoption is a separate press, and it goes through
 * the same import or save-version path a hand-written file takes — so a generated strategy is
 * a strategy, with the same validation, the same version history, and no special status.
 *
 * **The verdict shown is the engine's.** The draft has already been through the validator the
 * editor uses, up to four times, and what is rendered here is that answer verbatim. A dialog
 * that said "looks good" on its own authority would be the exact failure mode this codebase is
 * built against.
 *
 * **An invalid draft is still shown.** It is the near-miss the user can read, correct and
 * keep; hiding it behind an error would throw away the only useful thing the request produced.
 * Whether it can be *adopted* is the caller's call — the editor can take a broken file into a
 * draft, an import cannot.
 */
export function GenerateModal({
  adoptLabel,
  adopting = false,
  adoptError = null,
  allowInvalid,
  baseYaml,
  onAdopt,
  onClose,
  opened,
  placeholder,
  title,
}: {
  adoptLabel: string
  adopting?: boolean
  adoptError?: unknown
  /** Whether a draft the engine rejected may still be handed to `onAdopt`. */
  allowInvalid: boolean
  /** The file being revised, when this is an edit rather than a first draft. */
  baseYaml?: string
  onAdopt: (yaml: string) => void
  onClose: () => void
  opened: boolean
  placeholder: string
  title: string
}) {
  const generate = useGenerateConfig()
  const [instruction, setInstruction] = useState('')
  const [proposal, setProposal] = useState<GenerateConfigResponse | null>(null)
  const [note, setNote] = useState('')

  const close = () => {
    setInstruction('')
    setNote('')
    setProposal(null)
    generate.reset()
    onClose()
  }

  const ask = (text: string, base: string | undefined) => {
    generate.mutate(
      { instruction: text, ...(base ? { baseYaml: base } : {}) },
      {
        onSuccess: (result) => {
          setProposal(result)
          setNote('')
        },
      },
    )
  }

  const valid = proposal?.review.valid ?? false
  const blocked = proposal === null || (!valid && !allowInvalid)

  return (
    <Modal onClose={close} opened={opened} size="xl" title={title}>
      <Stack gap="md">
        {generate.error && <ProblemAlert error={generate.error} />}
        {adoptError ? <ProblemAlert error={adoptError} /> : null}

        <Textarea
          autosize
          data-autofocus
          description={
            baseYaml
              ? 'The current file is sent along, so ask for the change rather than restating the whole strategy.'
              : 'A ticker, what should make it buy, and what should get it out. Anything you leave out is chosen for you and named in the notes.'
          }
          disabled={generate.isPending}
          label={baseYaml ? 'What should change?' : 'What should the strategy do?'}
          maxRows={8}
          minRows={3}
          onChange={(event) => setInstruction(event.currentTarget.value)}
          placeholder={placeholder}
          value={instruction}
        />

        {generate.isPending && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text c="dimmed" size="sm">
              Looking up whatever it needs, writing the file, then checking it against the engine
              and fixing what it rejects. This takes a few minutes.
            </Text>
          </Group>
        )}

        {proposal && <Proposal proposal={proposal} />}

        {/*
          Refinement re-runs generation with the draft as its starting point, so the second
          answer is a change to the first rather than an unrelated strategy that happens to
          satisfy both sentences. There is no session on the server: everything the next
          attempt knows is in this request.
        */}
        {proposal && (
          <Textarea
            autosize
            description="Sent with the draft above, not with your original request — say what to change about what you can see."
            disabled={generate.isPending}
            label="Not quite right?"
            maxRows={5}
            minRows={2}
            onChange={(event) => setNote(event.currentTarget.value)}
            placeholder="Use an ATR stop instead, and let it hold longer."
            value={note}
          />
        )}

        <Group justify="space-between">
          <Text c="dimmed" size="xs">
            Nothing has been created. This is a file, not a result — backtest it before believing
            anything about it.
          </Text>
          <Group gap="xs">
            <Button onClick={close} variant="default">
              Cancel
            </Button>
            {proposal ? (
              <Button
                disabled={note.trim().length === 0}
                loading={generate.isPending}
                onClick={() => ask(note.trim(), proposal.yaml)}
                variant="light"
              >
                Regenerate
              </Button>
            ) : (
              <Button
                disabled={instruction.trim().length === 0}
                loading={generate.isPending}
                onClick={() => ask(instruction.trim(), baseYaml)}
              >
                Write it
              </Button>
            )}
            <Tooltip
              disabled={!blocked}
              label={
                proposal === null
                  ? 'Write a draft first.'
                  : 'The engine rejected this file, so there is nothing here that could be stored.'
              }
              withArrow
            >
              <div>
                <Button
                  disabled={blocked}
                  loading={adopting}
                  onClick={() => proposal && onAdopt(proposal.yaml)}
                >
                  {adoptLabel}
                </Button>
              </div>
            </Tooltip>
          </Group>
        </Group>
      </Stack>
    </Modal>
  )
}

/** The draft, its verdict, and what the writer says it did. */
function Proposal({ proposal }: { proposal: GenerateConfigResponse }) {
  const { errors, valid, warnings } = proposal.review

  return (
    <Stack gap="xs">
      <Group gap="xs">
        {valid ? (
          <Badge color="green" leftSection={<IconCheck size={12} />} variant="light">
            the engine accepts this file
          </Badge>
        ) : (
          <Badge color="red" leftSection={<IconAlertTriangle size={12} />} variant="light">
            the engine rejects this file
          </Badge>
        )}
        {/* The cost of the request, in the only unit that matters to the person paying it.
            A draft that took every attempt and still does not validate is one to read more
            carefully, not less. */}
        <Text c="dimmed" size="xs">
          {proposal.attempts === 1
            ? 'written first time'
            : `rewritten ${proposal.attempts - 1} time${proposal.attempts > 2 ? 's' : ''} against the validator`}
        </Text>
      </Group>

      {proposal.notes.trim().length > 0 && (
        <Alert color="blue" icon={<IconInfoCircle size={16} />} variant="light">
          <Text size="sm">{proposal.notes}</Text>
        </Alert>
      )}

      {errors.length > 0 && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} variant="light">
          <Stack gap={4}>
            <Text fw={600} size="sm">
              What the engine could not accept:
            </Text>
            <List size="sm" spacing={2}>
              {errors.map((issue) => (
                <List.Item key={`${issue.path}:${issue.message}`}>{describe(issue)}</List.Item>
              ))}
            </List>
          </Stack>
        </Alert>
      )}

      {warnings.length > 0 && (
        <Alert color="orange" icon={<IconInfoCircle size={16} />} variant="light">
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Warnings — these never block anything:
            </Text>
            {warnings.map((issue) => (
              <Text key={`${issue.path}:${issue.message}`} size="sm">
                {describe(issue)}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      <Code block style={{ fontSize: 13, maxHeight: 340, overflow: 'auto' }}>
        {proposal.yaml}
      </Code>
    </Stack>
  )
}
