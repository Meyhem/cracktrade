import type { TermKey } from '../../lib/glossary'
import type { RunKind } from '../../api/types'

/**
 * The list columns each run kind reports, in the order the brief specifies.
 *
 * Glossary keys rather than free text, and the header the reader sees comes from the glossary
 * — so a column cannot be added without an explanation for it existing. `label` is the short
 * form where the header has less room than the definition's own title.
 */
export const HEADLINE_COLUMNS: Record<RunKind, { term: TermKey; label?: string }[]> = {
  backtest: [
    { term: 'total_return', label: 'Return' },
    { term: 'buy_and_hold' },
    { term: 'excess', label: 'Excess' },
    { term: 'max_drawdown' },
    { term: 'trades' },
  ],
  optimize: [
    { term: 'oos_return', label: 'Out-of-sample' },
    { term: 'improvement' },
    { term: 'overfitting_gap' },
    { term: 'trials' },
    { term: 'trades' },
  ],
  walk_forward: [
    { term: 'combined_oos' },
    { term: 'buy_and_hold' },
    { term: 'fold_win_rate', label: 'Folds won' },
    { term: 'trades' },
    { term: 'verdict' },
  ],
}
