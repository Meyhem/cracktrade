import type { RunKind } from '../../api/types'

/** The list columns each run kind reports, in the order the brief specifies. */
export const HEADLINE_COLUMNS: Record<RunKind, string[]> = {
  backtest: ['Return', 'Buy & hold', 'Excess', 'Max drawdown', 'Trades'],
  optimize: ['Out-of-sample', 'Improvement', 'Overfitting gap', 'Trials', 'Trades'],
  walk_forward: ['Combined OOS', 'Buy & hold', 'Folds won', 'Trades', 'Verdict'],
}
