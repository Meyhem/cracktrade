import { Alert } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { Explain } from '../../components/Explain'
import { historyOf, type Json } from '../../lib/result'

/**
 * Says out loud when a run's figures rest on too little history.
 *
 * Styled as a warning rather than a footnote, and placed above the result rather than beneath
 * it, on purpose. This is the one screen element that stands between a user and the project's
 * worst possible output — a number that looks authoritative and is not — and a thin result
 * looks exactly like a sound one until something says otherwise. Engine spec §12.11.
 *
 * The note is the engine's own sentence, rendered verbatim. It already names what is thin, why
 * it matters for this kind of run, and the way out (which at 15m and 30m is *not* "widen the
 * date range", because the provider will not serve one), and paraphrasing it here would put a
 * second, drifting copy of that reasoning in TypeScript.
 *
 * Rendered for every run kind, from one place, because there is no kind for which thin history
 * is acceptable and four copies would eventually disagree about that.
 */
export function HistoryBanner({ result }: { result: Json | null }) {
  const history = historyOf(result)
  if (!history || !history.limited || !history.note) return null

  return (
    <Alert
      color="orange"
      icon={<IconAlertTriangle size={18} />}
      title={
        <>
          Limited history <Explain term="history_limited" />
        </>
      }
      variant="light"
    >
      {history.note}
    </Alert>
  )
}
