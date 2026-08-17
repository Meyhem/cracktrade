import { describe, expect, it } from 'vitest'
import {
  HIGHER_IS_BETTER,
  deltaFor,
  incomparability,
  type Figures,
  type VersionRow,
  type VersionRun,
} from './delta'

const FIGURES: Figures = {
  returnPct: 20,
  excessPp: 5,
  maxDrawdownPct: -15,
  sharpe: 1.2,
  foldWinRate: 0.5,
}

function run(version: number, overrides: Partial<VersionRun> = {}): VersionRun {
  return {
    version,
    runId: `run-${version}`,
    kind: 'walk_forward',
    objective: 'sharpe_ratio',
    folds: 6,
    scheme: 'rolling',
    frameDigest: 'abc123',
    trades: 40,
    figures: FIGURES,
    ...overrides,
  }
}

function rows(...entries: (VersionRun | null)[]): VersionRow[] {
  return entries.map((entry, index) => ({ version: index + 1, run: entry }))
}

describe('incomparability', () => {
  it('accepts two runs of the same kind, objective, fold count and scheme', () => {
    expect(incomparability(run(1), run(2))).toBeNull()
  })

  it('refuses to compare across run kinds', () => {
    const reason = incomparability(run(1, { kind: 'backtest' }), run(2))
    expect(reason).toContain('backtest')
    expect(reason).toContain('walk-forward')
  })

  it('refuses when the two searches maximised different things', () => {
    const reason = incomparability(run(1, { objective: 'total_return' }), run(2))
    expect(reason).toContain('total_return')
    expect(reason).toContain('sharpe_ratio')
  })

  it('refuses across a change in fold count', () => {
    expect(incomparability(run(1, { folds: 4 }), run(2))).toContain('4 folds')
  })

  it('refuses across a change in scheme', () => {
    expect(incomparability(run(1, { scheme: 'anchored' }), run(2))).toContain('anchored')
  })

  it('reports only the first reason, because unrelated runs are one problem and not four', () => {
    const reason = incomparability(run(1, { kind: 'backtest', objective: 'total_return' }), run(2))
    expect(reason).toContain('backtest')
    expect(reason).not.toContain('total_return')
  })
})

describe('deltaFor', () => {
  it('measures against the immediately previous version when it is comparable', () => {
    const better = { ...FIGURES, returnPct: 26, sharpe: 1.5 }
    const delta = deltaFor(rows(run(1), run(2, { figures: better })), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.against).toBe(1)
    expect(delta.movements).toContainEqual({ metric: 'returnPct', value: 6, improved: true })
    expect(delta.caveats).toEqual([])
  })

  it('shows nothing for the earliest version, since there is nothing behind it', () => {
    const delta = deltaFor(rows(run(1), run(2)), 0)
    expect(delta.shown).toBe(false)
    if (delta.shown) return
    expect(delta.reason).toContain('earliest')
  })

  it('shows nothing for a version with no run of the selected kind', () => {
    const delta = deltaFor(rows(run(1), null), 1)
    expect(delta.shown).toBe(false)
    if (delta.shown) return
    expect(delta.reason).toContain('No run of this kind')
  })

  it('never falls back to an incomparable run — it reports why instead', () => {
    const delta = deltaFor(rows(run(1, { kind: 'backtest' }), run(2)), 1)

    expect(delta.shown).toBe(false)
    if (delta.shown) return
    expect(delta.reason).toContain('No earlier version has a comparable run')
    expect(delta.reason).toContain('v1')
  })

  it('walks back past an incomparable version to the nearest comparable one, and says so', () => {
    const delta = deltaFor(rows(run(1), run(2, { folds: 4 }), run(3)), 2)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.against).toBe(1)
    expect(delta.caveats.join(' ')).toContain('skipping v2')
  })

  it('skips a version whose run is below the trade floor rather than treating it as zero', () => {
    const delta = deltaFor(rows(run(1), run(2, { figures: null, trades: 6 }), run(3)), 2)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.against).toBe(1)
    expect(delta.caveats.join(' ')).toContain('below the trade floor')
  })

  it('shows no Δ at all for a row that is itself below the floor', () => {
    const delta = deltaFor(rows(run(1), run(2, { figures: null, trades: 6 })), 1)

    expect(delta.shown).toBe(false)
    if (delta.shown) return
    expect(delta.reason).toContain('below the trade floor')
  })

  it('treats a missing figure on either side as no movement, not as a move from zero', () => {
    const partial = { ...FIGURES, sharpe: null }
    const delta = deltaFor(rows(run(1, { figures: partial }), run(2)), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.movements.map((movement) => movement.metric)).not.toContain('sharpe')
    expect(delta.movements.map((movement) => movement.metric)).toContain('returnPct')
  })

  it('caveats a comparison between runs that read different price frames', () => {
    const delta = deltaFor(rows(run(1, { frameDigest: 'older' }), run(2)), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.caveats.join(' ')).toContain('different price data')
  })

  it('caveats a comparison where a frame digest was never recorded', () => {
    const delta = deltaFor(rows(run(1, { frameDigest: null }), run(2)), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.caveats.join(' ')).toContain('did not record')
  })
})

describe('direction of improvement', () => {
  it('reads a shallower drawdown as an improvement', () => {
    // The engine signs drawdown negative (`tests/test_metrics.py`), so −30% → −15% is a gain of
    // 15 points. Treating "smaller drawdown is better" literally would colour this a regression.
    const worse = { ...FIGURES, maxDrawdownPct: -30 }
    const delta = deltaFor(rows(run(1, { figures: worse }), run(2)), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.movements).toContainEqual({
      metric: 'maxDrawdownPct',
      value: 15,
      improved: true,
    })
  })

  it('reads a deepening drawdown as a regression', () => {
    const deeper = { ...FIGURES, maxDrawdownPct: -40 }
    const delta = deltaFor(rows(run(1), run(2, { figures: deeper })), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.movements).toContainEqual({
      metric: 'maxDrawdownPct',
      value: -25,
      improved: false,
    })
  })

  it('does not call an unchanged figure an improvement', () => {
    const delta = deltaFor(rows(run(1), run(2)), 1)

    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    expect(delta.movements.every((movement) => !movement.improved)).toBe(true)
    expect(delta.movements.every((movement) => movement.value === 0)).toBe(true)
  })

  it('states a direction for every metric it moves', () => {
    const delta = deltaFor(rows(run(1), run(2)), 1)
    expect(delta.shown).toBe(true)
    if (!delta.shown) return
    for (const movement of delta.movements) {
      expect(HIGHER_IS_BETTER[movement.metric]).toBe(true)
    }
  })
})
