/**
 * Whether a figure may be shown, and what to say when it may not.
 *
 * The server enforces the trade floor by omission: below it, the withheld keys are simply
 * not in the payload, so a client cannot render a number it was never given (spec section
 * 15.2). This module is the client's half of that contract — it turns an absent value into
 * the sentence the brief specifies, rather than into a blank cell, a dash, or a zero, each
 * of which reads as information.
 *
 * The floor itself is never written here. It arrives on every headline as `trade_floor` and
 * is served by `/meta`, so this side cannot drift from the engine that did the suppressing.
 */

import type { Headline } from '../api/types'

export type Suppression =
  { suppressed: false } | { suppressed: true; trades: number; floor: number; message: string }

/** The brief's wording, and the engine's. Used wherever a figure is withheld. */
export function suppressionMessage(trades: number, floor: number): string {
  return `too few trades to draw a conclusion from (${trades} of ${floor} needed)`
}

function readNumber(source: Record<string, unknown>, key: string): number | null {
  const value = source[key]
  return typeof value === 'number' ? value : null
}

/**
 * Read the suppression state off a headline.
 *
 * Walk-forward headlines carry no `suppressed` flag: their credibility verdict already
 * accounts for the trade count through the `trade_count` check, and the fold-level figures
 * are reported either way. Backtest, optimize and evolution headlines all suppress, and this
 * reads the flag rather than the kind — so a kind added later is covered by having said so on
 * the wire, not by being listed here.
 */
export function suppressionOf(headline: Headline | null | undefined): Suppression {
  if (!headline) return { suppressed: false }

  const record = headline as unknown as Record<string, unknown>
  if (record.suppressed !== true) return { suppressed: false }

  const trades = readNumber(record, 'trades') ?? 0
  const floor = readNumber(record, 'trade_floor') ?? 0
  return { suppressed: true, trades, floor, message: suppressionMessage(trades, floor) }
}

/**
 * A figure the API may have withheld.
 *
 * `absent` and `null` are kept distinct on purpose. Absent means the server declined to
 * report the figure; null means the engine computed one that is not finite — an infinite
 * profit factor is a real result, and `serialize` writes it as null rather than dropping it.
 * A component that showed the same placeholder for both would be conflating "we will not
 * tell you" with "the answer has no finite value".
 */
export type Reported<T> = { state: 'present'; value: T } | { state: 'absent' } | { state: 'null' }

export function reported<T>(value: T | null | undefined): Reported<T> {
  if (value === undefined) return { state: 'absent' }
  if (value === null) return { state: 'null' }
  return { state: 'present', value }
}

/**
 * Pull one figure out of a headline, honouring suppression.
 *
 * Returns `absent` whenever the run is suppressed, even if the key happened to be present,
 * so that a future server change that starts sending a flagged figure cannot quietly start
 * rendering it here.
 */
export function headlineFigure(
  headline: Headline | null | undefined,
  key: string,
): Reported<number> {
  if (!headline) return { state: 'absent' }
  if (suppressionOf(headline).suppressed) return { state: 'absent' }
  return reported(
    (headline as unknown as Record<string, unknown>)[key] as number | null | undefined,
  )
}
