import { describe, expect, it } from 'vitest'
import { NAMED_IN_SUMMARY, clause, shortPath, summarise, valueOf } from './summary'
import type { Change } from '../../api/types'

const change = (path: string, old: unknown, next: unknown): Change =>
  ({ path, old, new: next }) as Change

describe('summarise', () => {
  it('names each change as a from-to clause', () => {
    expect(summarise([change('indicators.rsi_ind.window', 14, 21)])).toBe('rsi_ind.window 14 → 21')
  })

  it('counts the rest rather than running off the row', () => {
    const changes = Array.from({ length: 7 }, (_, index) =>
      change(`exit.field_${index}`, index, index + 1),
    )
    expect(summarise(changes)).toContain(`and ${7 - NAMED_IN_SUMMARY} more changes`)
  })

  it('says so when a version changed nothing rather than rendering an empty line', () => {
    // v1 has no predecessor, so its summary is empty; a blank cell would read as a rendering bug.
    expect(summarise([])).toBe('No changes')
  })
})

describe('valueOf', () => {
  it('renders an absent value as unset, so an added field still reads as a change', () => {
    expect(clause(change('execution.risk_free_rate', null, 0.04))).toBe(
      'execution.risk_free_rate unset → 0.04',
    )
  })

  it('elides a signal expression rather than letting it fill the row', () => {
    const long = '(close > sma_long) & (rsi_ind < 35) & (close > sma_short)'
    expect(valueOf(long)).toMatch(/^".{1,29}…"$/)
  })

  it('leaves a short string quoted and whole', () => {
    expect(valueOf('close > sma_long')).toBe('"close > sma_long"')
  })

  it('reads booleans as on and off rather than true and false', () => {
    expect(valueOf(true)).toBe('on')
    expect(valueOf(false)).toBe('off')
  })

  it('summarises a whole group rather than printing truncated JSON', () => {
    expect(valueOf([1, 2, 3])).toBe('3 entries')
    expect(valueOf({ min: 5, max: 20 })).toBe('a group of settings')
  })
})

describe('shortPath', () => {
  it('drops the indicators prefix, which the name already implies', () => {
    expect(shortPath('indicators.sma_long.window')).toBe('sma_long.window')
  })

  it('keeps a prefix that carries which rule the field belongs to', () => {
    expect(shortPath('exit.stop_loss_pct')).toBe('exit.stop_loss_pct')
  })
})
