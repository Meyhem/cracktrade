import { describe, expect, it } from 'vitest'
import { headlineFigure, reported, suppressionOf } from './suppression'
import type { BacktestHeadline, OptimizeHeadline, WalkForwardHeadline } from '../api/types'

describe('suppressionOf', () => {
  it('reports the floor and the count the server sent, not a hard-coded 20', () => {
    const headline: BacktestHeadline = {
      trades: 6,
      entry_defined_pct: 41.2,
      suppressed: true,
      trade_floor: 20,
    }

    const result = suppressionOf(headline)

    expect(result).toEqual({
      suppressed: true,
      trades: 6,
      floor: 20,
      message: 'too few trades to draw a conclusion from (6 of 20 needed)',
    })
  })

  it('passes an unsuppressed headline through', () => {
    const headline: BacktestHeadline = {
      trades: 48,
      entry_defined_pct: 88.0,
      suppressed: false,
      trade_floor: 20,
      return_pct: 31.4,
    }

    expect(suppressionOf(headline).suppressed).toBe(false)
  })

  it('does not suppress a walk-forward headline, which carries no flag', () => {
    const headline: WalkForwardHeadline = {
      folds: 4,
      scheme: 'anchored',
      combined_oos_pct: 6.6,
      benchmark_pct: 149.0,
      profitable_folds: '2/4',
      oos_trades: 16,
      is_credible: false,
      failed_checks: 3,
    }

    expect(suppressionOf(headline).suppressed).toBe(false)
  })
})

describe('headlineFigure', () => {
  it('withholds every figure while the run is suppressed', () => {
    // The server omits these keys, but a client that only checked for absence would start
    // rendering them the moment a future server sent them with the flag still set.
    const headline = {
      trades: 6,
      entry_defined_pct: 41.2,
      suppressed: true,
      trade_floor: 20,
      return_pct: 812.5,
    } as BacktestHeadline

    expect(headlineFigure(headline, 'return_pct')).toEqual({ state: 'absent' })
  })

  it('returns a present figure when the run cleared the floor', () => {
    const headline: OptimizeHeadline = {
      trials: 4800,
      trades: 31,
      suppressed: false,
      trade_floor: 20,
      oos_return_pct: 12.5,
    }

    expect(headlineFigure(headline, 'oos_return_pct')).toEqual({ state: 'present', value: 12.5 })
  })

  it('distinguishes a key the server omitted from one it sent as null', () => {
    const headline: OptimizeHeadline = {
      trials: 4800,
      trades: 31,
      suppressed: false,
      trade_floor: 20,
      overfitting_gap_pct: null,
    }

    // Absent: the server never reports this key for this kind.
    expect(headlineFigure(headline, 'improvement_pct')).toEqual({ state: 'absent' })
    // Null: the engine computed a non-finite value, which is itself a result.
    expect(headlineFigure(headline, 'overfitting_gap_pct')).toEqual({ state: 'null' })
  })

  it('treats a missing headline as absent rather than throwing', () => {
    expect(headlineFigure(null, 'return_pct')).toEqual({ state: 'absent' })
  })
})

describe('reported', () => {
  it('keeps zero, which is a real return, out of the absent branch', () => {
    expect(reported(0)).toEqual({ state: 'present', value: 0 })
  })
})
