import { useState } from 'react'
import { Button, FileButton, Group, Modal, Stack, Text, Textarea } from '@mantine/core'
import { IconFileUpload } from '@tabler/icons-react'
import { useNavigate } from 'react-router'
import { notifications } from '@mantine/notifications'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useImportStrategy } from './queries'

/**
 * Import an existing config file.
 *
 * The YAML is stored as written rather than re-dumped, so a file that arrives with comments
 * and a particular field order keeps both. Validation happens server-side on submit: an
 * invalid file is rejected with the engine's own messages rather than a generic "invalid
 * file" that leaves the user diffing against the schema by hand.
 */
export function ImportStrategyModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const importStrategy = useImportStrategy()
  const [yaml, setYaml] = useState('')
  const [filename, setFilename] = useState<string | null>(null)

  const close = () => {
    setYaml('')
    setFilename(null)
    importStrategy.reset()
    onClose()
  }

  const readFile = async (file: File | null) => {
    if (!file) return
    setFilename(file.name)
    setYaml(await file.text())
  }

  const submit = () => {
    importStrategy.mutate(
      { yaml, ...(filename ? { filename } : {}) },
      {
        onSuccess: (created) => {
          if (created.warnings.length > 0) {
            // Warnings do not block an import — a shadowed stop is a real configuration
            // that does something, just not what its author probably meant.
            notifications.show({
              color: 'orange',
              message: created.warnings.map((warning) => warning.message).join(' · '),
              title: 'Imported, with warnings',
            })
          }
          close()
          void navigate(`/strategies/${created.strategy.id}`)
        },
      },
    )
  }

  return (
    <Modal onClose={close} opened={opened} size="lg" title="Import a strategy">
      <Stack gap="md">
        {importStrategy.error && <ProblemAlert error={importStrategy.error} />}

        <Group>
          <FileButton accept=".yaml,.yml,text/yaml" onChange={(file) => void readFile(file)}>
            {(props) => (
              <Button {...props} leftSection={<IconFileUpload size={16} />} variant="default">
                Choose a file
              </Button>
            )}
          </FileButton>
          {filename && (
            <Text c="dimmed" size="sm">
              {filename}
            </Text>
          )}
        </Group>

        <Textarea
          autosize
          label="Configuration"
          maxRows={20}
          minRows={10}
          onChange={(event) => setYaml(event.currentTarget.value)}
          placeholder={'strategy:\n  name: rsi_pullback\nuniverse:\n  ticker: NVDA'}
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 13 } }}
          value={yaml}
        />

        <Text c="dimmed" size="xs">
          The file is stored exactly as written, comments and all. It is validated on import and
          rejected with the engine's own messages if it does not describe a strategy.
        </Text>

        <Group justify="flex-end">
          <Button onClick={close} variant="default">
            Cancel
          </Button>
          <Button
            disabled={yaml.trim().length === 0}
            loading={importStrategy.isPending}
            onClick={submit}
          >
            Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
