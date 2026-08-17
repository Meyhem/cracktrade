import { describe, expect, it } from 'vitest'
import { alignLines } from './linediff'

function marked(text: string, other: string) {
  const left = text.split('\n')
  const right = other.split('\n')
  const alignment = alignLines(left, right)
  return {
    left: left.filter((_line, index) => alignment.left[index]),
    right: right.filter((_line, index) => alignment.right[index]),
  }
}

describe('alignLines', () => {
  it('marks nothing when the documents are identical', () => {
    expect(marked('a\nb\nc', 'a\nb\nc')).toEqual({ left: [], right: [] })
  })

  it('marks one changed line on each side', () => {
    expect(marked('a\nb\nc', 'a\nB\nc')).toEqual({ left: ['b'], right: ['B'] })
  })

  it('does not cascade after an insertion', () => {
    // The whole point. Adding an indicator shifts every following line, and a positional
    // comparison would mark all of them — putting a highlight on an `exit:` block that nobody
    // touched, under a summary that correctly reports one added indicator.
    const before = [
      'indicators:',
      '- name: sma_long',
      '  window: 20',
      'exit:',
      '  stop_loss_pct: 5.0',
    ]
    const after = [
      'indicators:',
      '- name: sma_long',
      '  window: 20',
      '- name: sma_short',
      '  window: 5',
      'exit:',
      '  stop_loss_pct: 5.0',
    ]
    const alignment = alignLines(before, after)

    expect(alignment.left.filter(Boolean)).toHaveLength(0)
    expect(after.filter((_line, index) => alignment.right[index])).toEqual([
      '- name: sma_short',
      '  window: 5',
    ])
  })

  it('marks a removal on the side it was removed from', () => {
    const alignment = alignLines(['a', 'b', 'c'], ['a', 'c'])
    expect(alignment.left).toEqual([false, true, false])
    expect(alignment.right).toEqual([false, false])
  })

  it('handles an empty document on either side', () => {
    expect(alignLines([], ['a'])).toEqual({ left: [], right: [true] })
    expect(alignLines(['a'], [])).toEqual({ left: [true], right: [] })
  })

  it('does not pair identical lines across an unrelated rewrite', () => {
    // Repeated lines are the norm in YAML — `  type: sma` appears once per indicator — so the
    // alignment must not claim a match just because the text is equal somewhere.
    const alignment = alignLines(
      ['- name: a', '  type: sma', '- name: b', '  type: sma'],
      ['- name: a', '  type: sma'],
    )
    expect(alignment.left).toEqual([false, false, true, true])
    expect(alignment.right).toEqual([false, false])
  })
})
