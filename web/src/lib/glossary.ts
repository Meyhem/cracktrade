/**
 * Every word this app says, explained for someone who does not work in finance.
 *
 * One module, because the alternative is what was here before: explanation strings scattered
 * across component files, two of twelve metrics rows carrying a note, one of fifteen columns
 * carrying a tooltip, and nothing anywhere that fails when a new label ships unexplained.
 *
 * The boundary against the server is deliberate and worth stating. Anything whose wording
 * depends on engine behaviour or on a threshold comes from the API and is not repeated here —
 * the per-check sentences (`check.plain`), the residual-bias list (`meta.limits`), and the
 * trade-floor message (`suppressionMessage`). What lives here is *vocabulary*: what the word
 * on the screen means. Vocabulary is copywriting, and copywriting does not belong in an HTTP
 * contract that a schema regeneration has to chase.
 *
 * Two rules the tests enforce, both of which matter more than they look:
 *
 * - **Nothing here advises.** This engine reports; it does not tell anyone what to do with
 *   their money. A glossary is exactly where advice sneaks in wearing the clothes of
 *   helpfulness, so `glossary.test.ts` rejects it by pattern.
 * - **The vocabulary is closed.** An explanation may lean on a piece of jargon only if this
 *   glossary also defines that piece of jargon, so no chain of definitions dead-ends in a word
 *   the reader never got told.
 */

/** Which section of the glossary page a term files under. */
export const TERM_GROUPS = ['results', 'risk', 'validation', 'search', 'data', 'admin'] as const

export type TermGroup = (typeof TERM_GROUPS)[number]

export type Term = {
  /** The label as it appears on screen, so the panel repeats back what was clicked. */
  title: string
  /** One sentence. What it is, in words a non-financial reader already owns. */
  plain: string
  /**
   * How this number misleads. Omitted where there is genuinely no trap — a name is a name.
   * Where it exists it is the more important half, because a figure nobody can misread is not
   * the figure that costs someone money.
   */
  catch?: string
  group: TermGroup
}

export const GLOSSARY = {
  // ── results ──────────────────────────────────────────────────────────────────────────────
  total_return: {
    title: 'Total return',
    plain:
      'How much the account grew or shrank over the whole test, start to finish, as a ' +
      'percentage.',
    catch:
      'It says nothing about the ride. Two strategies can both end up +40% while one of them ' +
      'was down by half in the middle.',
    group: 'results',
  },
  cagr: {
    title: 'CAGR',
    plain:
      'The same growth expressed as a steady yearly rate — the constant per-year percentage ' +
      'that would have got you from the start value to the end value.',
    catch:
      'It smooths a bumpy result into a flat-sounding one. A good CAGR can hide a year that ' +
      'lost a third of the account.',
    group: 'results',
  },
  buy_and_hold: {
    title: 'Buy and hold',
    plain:
      'What you would have ended up with by buying the stock on day one, doing nothing at all, ' +
      'and selling on the last day.',
    catch:
      'This is the number that matters most. All the trading is only worth doing if it beats ' +
      'sitting still, and most of the time it does not.',
    group: 'results',
  },
  excess: {
    title: 'Excess over buy-and-hold',
    plain:
      'How far the strategy beat simply owning the stock. Below zero means every trade it ' +
      'made left you worse off than doing nothing.',
    catch:
      'A large positive strategy return can still be a failure here. Making 13% in a year the ' +
      'stock itself made 40% is losing.',
    group: 'results',
  },
  improvement: {
    title: 'Improvement',
    plain:
      'How much better the tuned settings did than the settings you originally wrote, on ' +
      'years neither of them had seen.',
    catch:
      'This compares the search against your own starting guess, not against owning the ' +
      'stock. Both can be beaten by buy and hold.',
    group: 'results',
  },
  oos_return: {
    title: 'Out-of-sample return',
    plain:
      'The return earned only on the years the tuning never looked at — the closest thing ' +
      'here to a result that was not fitted to the past.',
    group: 'results',
  },
  combined_oos: {
    title: 'Combined out of sample',
    plain:
      'All the untouched test stretches stitched together into one result, so the whole ' +
      'walk-forward reads as a single return.',
    catch:
      'Each stretch was traded with different settings, so this is the record of a process ' +
      'that re-tunes itself, not of one configuration you could run tomorrow.',
    group: 'results',
  },
  win_rate: {
    title: 'Win rate',
    plain: 'The share of trades that made money rather than losing it.',
    catch:
      'A high win rate is not the same as a profitable strategy. Winning nine small times and ' +
      'losing once enormously is a 90% win rate and a loss.',
    group: 'results',
  },
  profit_factor: {
    title: 'Profit factor',
    plain:
      'Total money made on winning trades divided by total money lost on losing ones. Above ' +
      '1 means the wins outweighed the losses.',
    catch:
      'Shown as "no losing trades" when there is nothing to divide by. That is a real result ' +
      'off very few trades, not a missing number.',
    group: 'results',
  },
  avg_holding_days: {
    title: 'Avg holding days',
    plain: 'How long the strategy stayed in a position on average, in days, before selling.',
    group: 'results',
  },
  trades: {
    title: 'Trades',
    plain: 'How many completed round trips — a buy followed by its sell — the strategy made.',
    catch:
      'This is the evidence behind every other figure. A handful of trades cannot support a ' +
      'return quoted to two decimal places, which is why the engine withholds figures below a ' +
      'floor and still shows you this count.',
    group: 'results',
  },
  final_equity: {
    title: 'Final equity',
    plain: 'What the account was worth on the last day of the test, in money.',
    group: 'results',
  },

  // ── risk ─────────────────────────────────────────────────────────────────────────────────
  max_drawdown: {
    title: 'Max drawdown',
    plain:
      'The worst fall from a high point to the low point that followed it. If you had put ' +
      'money in at the unluckiest moment, this is how far down it went.',
    catch:
      'It does not say how long you sat underwater waiting to get back. A 30% fall that took ' +
      'four years to recover is far harder to live through than one that took a month.',
    group: 'risk',
  },
  drawdown: {
    title: 'Drawdown',
    plain: 'How far below its own previous high point the account is sitting right now.',
    group: 'risk',
  },
  sharpe: {
    title: 'Sharpe',
    plain:
      'Reward per unit of bumpiness. It divides the return by how much the account value ' +
      'jumped around, so a steady 10% scores better than a wild 10%.',
    catch:
      'It punishes big up-days exactly as hard as big down-days, and it charges the ' +
      'risk-free rate across the whole test even on the days the strategy held nothing.',
    group: 'risk',
  },
  sortino: {
    title: 'Sortino',
    plain:
      'Sharpe with the unfairness removed: it counts only the downward jumps, so a strategy ' +
      'is not penalised for the days it surged upward.',
    group: 'risk',
  },
  calmar: {
    title: 'Calmar',
    plain:
      'Yearly return divided by the worst fall it took to earn it. It answers "how much pain ' +
      'per unit of gain", which is usually the question that actually matters.',
    group: 'risk',
  },
  exposure: {
    title: 'Exposure',
    plain: 'The share of days the strategy actually held something rather than sitting in cash.',
    catch:
      'Low exposure drags Sharpe down, because idle cash earns nothing here while the ' +
      'risk-free rate is charged across the whole period regardless.',
    group: 'risk',
  },
  risk_free_rate: {
    title: 'Risk-free rate',
    plain:
      'What money would have earned sitting safely in government debt instead. It is the bar ' +
      'a strategy has to clear before it has achieved anything.',
    group: 'risk',
  },
  commission: {
    title: 'Commission',
    plain: 'The fee your broker charges per trade, taken out of every result on this screen.',
    group: 'risk',
  },
  slippage: {
    title: 'Slippage',
    plain:
      'The gap between the price you expected and the price you actually got. Real orders ' +
      'rarely fill exactly where the chart says.',
    catch:
      'Strategies that trade often are far more sensitive to this. A result that only works ' +
      'with zero slippage does not work.',
    group: 'risk',
  },
  cost_ladder: {
    title: 'Cost ladder',
    plain:
      'The same strategy re-run with steadily worse fees and slippage, to show at what point ' +
      'the profit disappears.',
    catch:
      'If the profit vanishes at costs only slightly above the ones assumed, the result was ' +
      'never really there.',
    group: 'risk',
  },
  initial_capital: {
    title: 'Initial capital',
    plain: 'The pretend starting balance the simulation begins with.',
    group: 'risk',
  },
  position_sizing: {
    title: 'Position sizing',
    plain: 'How much money goes into each trade — a fixed amount, or a share of the account.',
    group: 'risk',
  },
  take_profit: {
    title: 'Take profit',
    plain: 'An automatic sell once a position has gained a set percentage.',
    group: 'risk',
  },
  stop: {
    title: 'Stop',
    plain: 'An automatic sell once a position has fallen by a set amount, to cap the loss.',
    catch:
      'Only one stop is ever in force. They follow a fixed priority order, so a stop you ' +
      'configured can sit there doing nothing because a higher-priority one outranks it.',
    group: 'risk',
  },
  shadowed_stop: {
    title: 'Shadowed stop',
    plain:
      'A stop you configured that never fired once, because another stop outranked it and ' +
      'always got there first.',
    group: 'risk',
  },
  min_holding_days: {
    title: 'Minimum holding days',
    plain: 'The shortest time a position must be held before the strategy is allowed to sell.',
    group: 'risk',
  },
  max_holding_days: {
    title: 'Maximum holding days',
    plain: 'The longest a position may be held before it is sold regardless of the signals.',
    group: 'risk',
  },

  // ── validation ───────────────────────────────────────────────────────────────────────────
  verdict: {
    title: 'Verdict',
    plain:
      'Whether this strategy passed every robustness check, or failed at least one. It is a ' +
      'plain conjunction — one failure is a failure, nothing is averaged away.',
    catch:
      'Passing means nobody has caught it cheating yet. It is not a forecast, and it is not a ' +
      'promise about next year.',
    group: 'validation',
  },
  credible: {
    title: 'Credible',
    plain: 'A walk-forward run against the current version passed every robustness check.',
    group: 'validation',
  },
  not_credible: {
    title: 'Not credible',
    plain: 'A walk-forward run against the current version failed at least one check.',
    group: 'validation',
  },
  unvalidated: {
    title: 'Unvalidated',
    plain:
      'Nobody has checked this version yet. Any earlier run describes a configuration that ' +
      'has since been edited.',
    group: 'validation',
  },
  never_run: {
    title: 'Never run',
    plain: 'This strategy has never been run at all.',
    group: 'validation',
  },
  walk_forward: {
    title: 'Walk-forward',
    plain:
      'The honest test. The history is cut into stretches; the settings are tuned on each ' +
      'stretch and then judged only on the stretch that follows, which the tuning never saw.',
    group: 'validation',
  },
  fold: {
    title: 'Fold',
    plain:
      'One tune-then-test pair. The strategy learns its settings on the earlier window and is ' +
      'scored on the later one.',
    catch:
      'Every fold re-tunes from scratch, so two folds are usually running different settings. ' +
      'Pooling their trades would chart something that was never one strategy.',
    group: 'validation',
  },
  in_sample: {
    title: 'In sample',
    plain: 'The stretch of history the settings were tuned on. The strategy has seen it.',
    catch:
      'Results here mean almost nothing. Any set of settings can be made to look good on the ' +
      'years it was fitted to.',
    group: 'validation',
  },
  out_of_sample: {
    title: 'Out of sample',
    plain:
      'The stretch of history the tuning never saw. This is where a result starts being worth ' +
      'something.',
    group: 'validation',
  },
  overfitting_gap: {
    title: 'Overfitting gap',
    plain:
      'How much better the strategy did on the years it was tuned on than on the years it had ' +
      'never seen. Small is good.',
    catch:
      'A big positive gap means the settings memorised the past instead of learning anything. ' +
      'This is the single most common way a backtest fools someone.',
    group: 'validation',
  },
  fold_win_rate: {
    title: 'Fold win rate',
    plain: 'How many of the tune-then-test stretches ended in profit, out of all of them.',
    catch:
      'Winning three folds out of six is a coin flip dressed up as a result, no matter how ' +
      'large the total return is.',
    group: 'validation',
  },
  fold_spread: {
    title: 'Fold spread',
    plain:
      'The distance between the best stretch and the worst one. It shows how much the result ' +
      'depends on which years you happened to get.',
    catch:
      'A wide spread means the headline number is mostly luck about timing, not a repeatable ' +
      'edge.',
    group: 'validation',
  },
  median_fold: {
    title: 'Median fold',
    plain:
      'The middle stretch when they are lined up worst to best — the typical outcome, rather ' +
      'than the average, which one freak stretch can drag around.',
    group: 'validation',
  },
  deflated_sharpe: {
    title: 'Deflated Sharpe',
    plain:
      'Sharpe, marked down for how many settings were tried. Test enough combinations and one ' +
      'of them looks brilliant purely by chance; this discounts that.',
    catch:
      'If the deflated figure collapses toward zero, the original Sharpe was mostly the ' +
      'reward for searching hard, not for finding anything.',
    group: 'validation',
  },
  parameter_drift: {
    title: 'Parameter drift',
    plain: 'How much the settings the search picked jumped around from one stretch to the next.',
    catch:
      'Settings that change wildly between stretches mean there is no stable answer to find, ' +
      'so tomorrow the search would pick something else again.',
    group: 'validation',
  },
  confidence_interval: {
    title: 'Confidence interval',
    plain:
      'The range the true result plausibly sits in, given how few trades there are. A range ' +
      'that crosses zero means the profit could as easily have been a loss.',
    group: 'validation',
  },
  cost_sensitivity: {
    title: 'Cost sensitivity',
    plain: 'How quickly the profit disappears as trading fees and slippage are raised.',
    group: 'validation',
  },
  robustness_checks: {
    title: 'Robustness checks',
    plain:
      'The list of ways this result was attacked to see whether it survives. Each one is ' +
      'passed or failed on its own; nothing is averaged.',
    group: 'validation',
  },
  training_window: {
    title: 'Training window',
    plain:
      'Whether the tuning window grows from a fixed start each time, or stays a fixed length ' +
      'that slides forward and forgets the oldest years.',
    group: 'validation',
  },
  monthly_returns: {
    title: 'Monthly returns',
    plain: 'What each individual month made or lost, rather than the total across all of them.',
    catch:
      'Look for whether the profit is spread across many months or comes from one or two. ' +
      'One good month is not a strategy.',
    group: 'validation',
  },
  rolling_12m: {
    title: 'Rolling twelve-month return',
    plain:
      'What the strategy made over each possible one-year stretch, so you can see the bad ' +
      'years as well as the good ones.',
    group: 'validation',
  },

  // ── search ───────────────────────────────────────────────────────────────────────────────
  objective: {
    title: 'Objective',
    plain:
      'What the search is trying to make as large as possible. Change this and it will pick ' +
      'entirely different settings.',
    group: 'search',
  },
  objective_calmar: {
    title: 'Objective: calmar',
    plain: 'Aim for return against the worst fall. The default, and the most cautious of the four.',
    group: 'search',
  },
  objective_sortino: {
    title: 'Objective: sortino',
    plain: 'Aim for return against downward jumps only; upward surges are not penalised.',
    group: 'search',
  },
  objective_sharpe: {
    title: 'Objective: sharpe',
    plain: 'Aim for return against all jumping around, upward and downward alike.',
    group: 'search',
  },
  objective_legacy_pnl: {
    title: 'Objective: legacy_pnl',
    plain: 'Aim for raw profit and nothing else. Kept only for comparing against older runs.',
    catch:
      'It ignores how much pain the profit cost, so it reliably picks settings that made money ' +
      'by taking risks you would not have accepted.',
    group: 'search',
  },
  epochs: {
    title: 'Epochs',
    plain: 'How many rounds the search gets. More rounds means a longer, more thorough hunt.',
    catch:
      'More searching is not more truth. Trying more combinations makes it likelier that one ' +
      'of them looks good purely by chance.',
    group: 'search',
  },
  folds: {
    title: 'Folds',
    plain:
      'How many tune-then-test stretches the history is cut into. Each is tuned and scored ' +
      'independently.',
    group: 'search',
  },
  trials: {
    title: 'Trials',
    plain: 'How many different combinations of settings the search actually tried.',
    group: 'search',
  },
  evaluations: {
    title: 'Evaluations',
    plain: 'How many times a full simulation was run while searching.',
    group: 'search',
  },
  budget: {
    title: 'Budget',
    plain: 'The cap on how many combinations the search was allowed to try.',
    group: 'search',
  },
  failures: {
    title: 'Failures',
    plain: 'Combinations that crashed or could not be simulated at all.',
    group: 'search',
  },
  infeasible: {
    title: 'Infeasible',
    plain:
      'Combinations that ran but broke one of your own rules, such as holding for fewer days ' +
      'than the minimum you set.',
    group: 'search',
  },
  seed: {
    title: 'Seed',
    plain:
      'The starting number for the search randomness. The same seed and the same settings ' +
      'reproduce the same run exactly.',
    group: 'search',
  },
  search_range: {
    title: 'Search range',
    plain: 'The lowest and highest value the search was allowed to consider for this setting.',
    catch:
      'If the chosen value sits right at the edge of the range, the best answer is probably ' +
      'outside it and was never tried.',
    group: 'search',
  },
  pinned_parameter: {
    title: 'Pinned',
    plain:
      'A setting written as one fixed value rather than a range, so the search cannot move it.',
    group: 'search',
  },
  unoptimized_baseline: {
    title: 'Unoptimized baseline',
    plain: 'Your original settings, run on the same untouched years, for comparison.',
    group: 'search',
  },
  optimized: {
    title: 'Optimized',
    plain: 'The settings the search chose, run on the years it was not allowed to see.',
    group: 'search',
  },
  cache_prices: {
    title: 'Cache downloaded price history',
    plain:
      'Reuse the price data already on disk instead of downloading it again. Faster, and ' +
      'keeps repeated runs on identical data.',
    group: 'search',
  },
  parameters_moved: {
    title: 'Parameters the search moved',
    plain: 'Which settings changed from what you wrote, and what they changed to.',
    group: 'search',
  },

  // ── data ─────────────────────────────────────────────────────────────────────────────────
  ticker: {
    title: 'Ticker',
    plain: 'The stock symbol being traded, such as SPY or AAPL.',
    catch:
      'One symbol only — this engine does not do portfolios. You also chose it knowing how ' +
      'its history turned out, and no amount of validation corrects for that.',
    group: 'data',
  },
  date_range: {
    title: 'Date range',
    plain: 'The stretch of history the simulation covers, first day to last.',
    catch:
      'The window chosen changes the answer. A test that starts after a crash flatters every ' +
      'strategy in it.',
    group: 'data',
  },
  start_date: {
    title: 'Start date',
    plain: 'The first day of price history the simulation uses.',
    group: 'data',
  },
  end_date: {
    title: 'End date',
    plain: 'The last day of price history the simulation uses.',
    group: 'data',
  },
  warmup_bars: {
    title: 'Warm-up bars',
    plain:
      'The days at the start that are skipped because the indicators do not have enough ' +
      'history behind them yet to produce a value.',
    group: 'data',
  },
  entry_defined: {
    title: 'Entry defined',
    plain: 'The share of days on which the buy condition could actually be worked out at all.',
    catch:
      'Some indicators are undefined on most days by design. A condition defined on 8% of ' +
      'days is a fact you need before reading any return above it.',
    group: 'data',
  },
  exit_defined: {
    title: 'Exit defined',
    plain: 'The share of days on which the sell condition could actually be worked out at all.',
    group: 'data',
  },
  forward_filled: {
    title: 'Forward-filled bars',
    plain:
      'Days with no real price, filled in by repeating the previous day. Holidays and gaps in ' +
      'the source data.',
    catch: 'A lot of these means the strategy is partly trading invented prices.',
    group: 'data',
  },
  data_vintage: {
    title: 'Data vintage',
    plain: 'Exactly which prices this run used, and when they were fetched.',
    catch:
      'Prices get rewritten backwards over time to account for splits and dividends, so the ' +
      'same run repeated months later uses different numbers.',
    group: 'data',
  },
  frame_digest: {
    title: 'Price frame digest',
    plain:
      'A short fingerprint of the exact price data used. Two runs with the same fingerprint ' +
      'saw the same prices.',
    catch:
      'Two runs disagreeing is explainable when you can compare fingerprints, and merely ' +
      'alarming when you cannot.',
    group: 'data',
  },
  universe: {
    title: 'Strategy and universe',
    plain: 'What is being traded, and over which stretch of history it is being tested.',
    group: 'data',
  },
  execution: {
    title: 'Execution',
    plain:
      'The assumptions about the real world — starting money, broker fees, and the gap between ' +
      'the price you wanted and the one you got.',
    catch:
      'These are guesses about your own broker. Optimistic ones turn a losing strategy into a ' +
      'winning-looking one on this screen and nowhere else.',
    group: 'risk',
  },
  entry_signal: {
    title: 'Entry signal',
    plain: 'The condition that has to be true for the strategy to buy.',
    group: 'data',
  },
  exit_signal: {
    title: 'Exit signal',
    plain: 'The condition that has to be true for the strategy to sell.',
    group: 'data',
  },
  indicators: {
    title: 'Indicators',
    plain:
      'Numbers worked out from past prices — averages, ranges, momentum — that the buy and ' +
      'sell conditions are written in terms of.',
    group: 'data',
  },
  price_with_markers: {
    title: 'Price with trade markers',
    plain: 'The stock price over time, with a mark at every point the strategy bought or sold.',
    group: 'data',
  },
  equity_curve: {
    title: 'Equity',
    plain: 'What the account was worth on each day, drawn as a line.',
    group: 'data',
  },

  metrics: {
    title: 'Metrics',
    plain: 'The standard set of scorecard numbers, with buy and hold in the column beside them.',
    group: 'results',
  },
  definedness: {
    title: 'Definedness',
    plain:
      'How often the buy and sell conditions could be worked out at all, rather than sitting ' +
      'undefined because an indicator had nothing to work with yet.',
    group: 'data',
  },
  three_comparisons: {
    title: 'Three comparisons',
    plain:
      'The tuned settings measured against three different things: your original settings, ' +
      'owning the stock, and the years the tuning was allowed to see.',
    group: 'search',
  },
  winning_configuration: {
    title: 'The winning configuration',
    plain: 'The exact settings the search ended on, written out so you can copy or promote them.',
    catch:
      'These won on past data. Nothing here says they are the settings that will win next ' +
      'year, and the robustness checks exist because usually they are not.',
    group: 'search',
  },
  search_effort: {
    title: 'Search effort',
    plain: 'How much work the search actually did, and how much of it went nowhere.',
    group: 'search',
  },
  parameter: {
    title: 'Parameter',
    plain: 'One adjustable setting in the strategy — a lookback length, a threshold, a percentage.',
    group: 'search',
  },
  value_before: {
    title: 'Was',
    plain: 'The value you originally wrote for this setting.',
    group: 'search',
  },
  value_after: {
    title: 'Now',
    plain: 'The value the search settled on instead.',
    group: 'search',
  },
  changed_fields: {
    title: 'Changed',
    plain: 'Which parts of the configuration differ between these two saved versions.',
    group: 'admin',
  },
  delta: {
    title: 'Δ',
    plain:
      'The difference against the version above, so improvement and regression read at a glance.',
    catch:
      'Two versions are only comparable when they were run the same way over the same years. ' +
      'Where they were not, the difference is left blank rather than guessed at.',
    group: 'admin',
  },
  trade_entry: {
    title: 'Entry',
    plain: 'The day the position was opened, and the price it was opened at.',
    group: 'results',
  },
  trade_exit: {
    title: 'Exit',
    plain: 'The day the position was closed, the price, and what closed it.',
    group: 'results',
  },
  trade_size: {
    title: 'Size',
    plain: 'How many shares the position held.',
    group: 'results',
  },
  trade_fees: {
    title: 'Fees',
    plain: 'What this trade cost in commission and slippage, already deducted from its result.',
    group: 'results',
  },
  trade_pnl: {
    title: 'PnL',
    plain: 'What this one trade made or lost in money, after costs.',
    group: 'results',
  },
  trade_return: {
    title: 'Trade return',
    plain: 'What this one trade made or lost as a percentage of what it put at stake.',
    group: 'results',
  },
  trade_duration: {
    title: 'Duration',
    plain: 'How long this position was held, in days.',
    group: 'results',
  },
  trade_distribution: {
    title: 'Trade return distribution',
    plain: 'How the individual trade results are spread out, from the worst to the best.',
    catch:
      'A long tail on one side means the whole result rests on very few trades, and removing ' +
      'them would remove the profit.',
    group: 'results',
  },
  cumulative_pnl: {
    title: 'Cumulative P&L by trade',
    plain: 'The running total of profit, trade by trade, in the order they happened.',
    catch:
      'Look for one vertical jump carrying the whole line. That is one lucky trade, not a system.',
    group: 'results',
  },
  won_vs_lost: {
    title: 'Won against lost',
    plain: 'The winning trades and the losing ones side by side, in count and in size.',
    group: 'results',
  },
  yearly_returns: {
    title: 'Yearly returns',
    plain: 'What each calendar year made or lost, with buy and hold beside it.',
    group: 'validation',
  },
  fold_returns: {
    title: 'Fold returns',
    plain:
      'What each tune-then-test stretch made, so a single strong stretch cannot hide the rest.',
    group: 'validation',
  },
  stability_plateau: {
    title: 'Parameter stability plateau',
    plain:
      'A map of how the result changes as a setting is nudged around its chosen value. A wide ' +
      'flat region is good; a lone spike is a coincidence.',
    group: 'validation',
  },

  // ── admin ────────────────────────────────────────────────────────────────────────────────
  strategy_name: {
    title: 'Name',
    plain: 'What you call this strategy. It has no effect on any result.',
    group: 'admin',
  },
  run: {
    title: 'Run',
    plain:
      'One simulation, numbered in the order it was started. Nothing about a run is ever ' +
      'edited afterwards, so an old number always means the same thing.',
    group: 'admin',
  },
  status: {
    title: 'Status',
    plain: 'Where a run has got to: waiting, working, finished, failed, or cancelled.',
    group: 'admin',
  },
  version: {
    title: 'Version',
    plain:
      'Which saved edit of the configuration this is. Every save makes a new one and nothing ' +
      'is ever overwritten.',
    group: 'admin',
  },
  stale: {
    title: 'Stale',
    plain:
      'This run describes an older version of the configuration. The strategy has been edited ' +
      'since, so the numbers no longer describe what would run today.',
    group: 'admin',
  },
  last_run: {
    title: 'Last run',
    plain: 'When this strategy was most recently simulated.',
    group: 'admin',
  },
  elapsed: {
    title: 'Elapsed',
    plain: 'How long the run took to finish.',
    group: 'admin',
  },
  runs_count: {
    title: 'Runs',
    plain: 'How many times this strategy has been simulated.',
    group: 'admin',
  },
  promote: {
    title: 'Promote',
    plain:
      'Save the settings this run found as a strategy of its own, so it can be edited and ' +
      'run further.',
    group: 'admin',
  },
  fork: {
    title: 'Fork',
    plain: 'Make a copy of this strategy to change freely, leaving the original exactly as it is.',
    group: 'admin',
  },
  configuration: {
    title: 'Configuration',
    plain: 'The strategy itself, written out — what it trades, when it buys, and when it sells.',
    group: 'admin',
  },
  log_scale: {
    title: 'Logarithmic scale',
    plain:
      'Draw the chart so equal percentage moves take equal vertical space. A doubling looks ' +
      'the same whether it happened early or late.',
    group: 'admin',
  },
} as const satisfies Record<string, Term>

export type TermKey = keyof typeof GLOSSARY

/**
 * The same table, widened.
 *
 * `as const satisfies` above is what makes `TermKey` a literal union, so a misspelled term is
 * a compile error rather than a silently missing tooltip. The cost is that the inferred entry
 * type is a union of ~100 exact shapes, and the ones without a `catch` genuinely do not have
 * the property — so reading `.catch` off the union does not type-check. Everything that
 * *renders* a term goes through here instead; only the key checking needs the narrow type.
 */
const TERMS: Record<TermKey, Term> = GLOSSARY

export function termOf(key: TermKey): Term {
  return TERMS[key]
}

/** The `plain` sentence alone, for places that compose their own sentence around it. */
export function explanationOf(term: TermKey): string {
  return TERMS[term].plain
}

/**
 * Definition and caveat as one visible paragraph, for a form field's description slot.
 *
 * Form labels do not get an info icon, and the reason is structural rather than stylistic: a
 * focusable control nested inside a `<label>` is a labelable element inside a label, which is
 * invalid HTML and breaks the label's association with its own input. The description slot is
 * the correct place, and being always visible it is the better one anyway — a novice filling
 * in a field has not learned yet that there is something to hover over.
 */
export function descriptionOf(term: TermKey): string {
  const { plain, catch: caveat } = TERMS[term]
  return caveat ? `${plain} ${caveat}` : plain
}

/** Every term in one group, in declaration order, for the glossary page. */
export function termsIn(group: TermGroup): { key: TermKey; term: Term }[] {
  return (Object.entries(TERMS) as [TermKey, Term][])
    .filter(([, term]) => term.group === group)
    .map(([key, term]) => ({ key, term }))
}
