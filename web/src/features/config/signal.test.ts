import { describe, expect, it } from 'vitest'
import { numericLiterals, parenthesiseComparisons } from './signal'

describe('the parenthesisation fix', () => {
  it('wraps each comparison in the expression the user actually wrote', () => {
    expect(parenthesiseComparisons('close > sma & rsi < 30')).toBe('(close > sma) & (rsi < 30)')
  })

  it('leaves a comparison that is already parenthesised alone', () => {
    expect(parenthesiseComparisons('(close > sma) & rsi < 30')).toBe('(close > sma) & (rsi < 30)')
  })

  it('keeps the operator that separated each pair', () => {
    expect(parenthesiseComparisons('a > 1 | b < 2 & c > 3')).toBe('(a > 1) | (b < 2) & (c > 3)')
  })

  it('carries arithmetic and negation along inside the parentheses', () => {
    expect(parenthesiseComparisons('close > sma * 1.02 & rsi < 30')).toBe(
      '(close > sma * 1.02) & (rsi < 30)',
    )
    expect(parenthesiseComparisons('~(rsi > 70) & close > sma')).toBe('~(rsi > 70) & (close > sma)')
  })

  it('offers nothing when there is nothing to fix', () => {
    expect(parenthesiseComparisons('(close > sma) & (rsi < 30)')).toBeNull()
    expect(parenthesiseComparisons('close > sma')).toBeNull()
    expect(parenthesiseComparisons('above & below')).toBeNull()
  })

  it('offers nothing while the parentheses are unbalanced', () => {
    // Mid-edit. Any suggestion here would be built on a misreading of what is being written.
    expect(parenthesiseComparisons('(close > sma & rsi < 30')).toBeNull()
    expect(parenthesiseComparisons('close > sma) & (rsi < 30')).toBeNull()
  })
})

describe('the unoptimizable-literal hint', () => {
  it('names the numbers a search cannot reach', () => {
    expect(numericLiterals('(close > sma_long) & (rsi_ind < 35)')).toEqual(['35'])
    expect(numericLiterals('close > sma * 1.02')).toEqual(['1.02'])
  })

  it('does not mistake a digit inside a name for a literal', () => {
    expect(numericLiterals('close > sma_200')).toEqual([])
    expect(numericLiterals('ema12 > ema26')).toEqual([])
  })

  it('says nothing about an expression with no literals in it', () => {
    expect(numericLiterals('close > sma_long')).toEqual([])
  })
})
