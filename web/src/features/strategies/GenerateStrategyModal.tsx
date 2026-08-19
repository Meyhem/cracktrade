import { useNavigate } from 'react-router'
import { notifications } from '@mantine/notifications'
import { GenerateModal } from '../authoring/GenerateModal'
import { useImportStrategy } from './queries'

/**
 * Write a strategy from a description, then keep it — or not.
 *
 * Adoption goes through `POST /strategies/import`, the same route an uploaded file takes, and
 * for the same reason: what arrives is YAML the user has read and accepted, and there is no
 * argument for a second creation path that treats a machine-written file differently. The
 * strategy that results is v1 of an ordinary strategy, with no marker saying where it came
 * from, because it has been through exactly the checks a hand-written one has.
 *
 * The draft can only be adopted if the engine accepted it. Import would refuse an invalid file
 * anyway, and offering a button that is certain to fail is worse than saying why it is off.
 */
export function GenerateStrategyModal({
  opened,
  onClose,
}: {
  opened: boolean
  onClose: () => void
}) {
  const navigate = useNavigate()
  const importStrategy = useImportStrategy()

  const close = () => {
    importStrategy.reset()
    onClose()
  }

  const adopt = (yaml: string) => {
    importStrategy.mutate(
      { yaml },
      {
        onSuccess: (created) => {
          if (created.warnings.length > 0) {
            notifications.show({
              color: 'orange',
              message: created.warnings.map((warning) => warning.message).join(' · '),
              title: 'Created, with warnings',
            })
          }
          close()
          void navigate(`/strategies/${created.strategy.id}`)
        },
      },
    )
  }

  return (
    <GenerateModal
      adoptError={importStrategy.error}
      adoptLabel="Create strategy"
      adopting={importStrategy.isPending}
      allowInvalid={false}
      onAdopt={adopt}
      onClose={close}
      opened={opened}
      placeholder="Buy NVDA when it pulls back in an uptrend, and get out on a trailing stop. Ten years of daily bars."
      title="Write a strategy"
    />
  )
}
