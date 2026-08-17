/**
 * Whether a chart may be drawn, and what occupies its frame when it may not.
 *
 * §5.2 of the brief overrides every chart description, and the four rules in it are *designed
 * states*, not error handling. A chart is more persuasive than a number — sixteen trades drawn
 * as a smooth curve is a lie told in a friendlier medium — so the decision not to draw is made
 * here, once, before any component receives data.
 *
 * Every non-ready state carries its own sentence. A blank panel, a dash, or a chart that
 * silently shrinks to nothing all read as "there is nothing here", which is a different claim
 * from "there is something here and it is not enough to draw a conclusion from".
 */

import { suppressionMessage } from '../../lib/suppression'

export type ChartState =
  | { kind: 'ready' }
  /** Below the trade floor. The evidence for the refusal is the trade count itself. */
  | { kind: 'too-few-trades'; trades: number; floor: number; message: string }
  /** A series the chart needs was never captured, or the run predates it. */
  | { kind: 'missing'; what: string }
  /** The window is shorter than the period the chart aggregates over. */
  | { kind: 'too-short'; what: string }
  /** Combining folds here would describe a configuration that was never run. */
  | { kind: 'needs-a-fold'; what: string }

export const READY: ChartState = { kind: 'ready' }

/**
 * The trade floor.
 *
 * `hasEnough` is the engine's own answer (`Metrics.has_enough_trades_to_judge`), never a
 * comparison recomputed here — the floor is served by `/meta` and applied by the engine, and a
 * second implementation of it would eventually disagree in public about whether a result is
 * publishable.
 *
 * `null` means the engine did not state one either way, which is not a reason to withhold.
 */
export function tradeFloor(
  hasEnough: boolean | null,
  trades: number | null,
  floor: number,
): ChartState {
  if (hasEnough !== false) return READY
  const counted = trades ?? 0
  return {
    kind: 'too-few-trades',
    trades: counted,
    floor,
    message: suppressionMessage(counted, floor),
  }
}

/** A required series that is not there. Never substitute a chart that happens to have data. */
export function requires(present: boolean, what: string): ChartState {
  return present ? READY : { kind: 'missing', what }
}

/**
 * The first state that is not ready, in the order given.
 *
 * Order matters: "too few trades to draw a conclusion from" is a more useful thing to be told
 * than "no rolling twelve-month series", when both are true because the strategy barely traded.
 */
export function firstProblem(...states: ChartState[]): ChartState {
  return states.find((state) => state.kind !== 'ready') ?? READY
}

/** The sentence shown in a chart's frame when it is not drawn. */
export function explain(state: ChartState): string {
  switch (state.kind) {
    case 'ready':
      return ''
    case 'too-few-trades':
      return state.message
    case 'missing':
      return `${state.what} was not recorded for this run, so there is nothing to draw.`
    case 'too-short':
      return `${state.what} — a partial window is not a shorter answer, it is a different one.`
    case 'needs-a-fold':
      return state.what
  }
}
