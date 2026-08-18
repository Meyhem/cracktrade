import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GLOSSARY, TERM_GROUPS, termsIn, type Term, type TermKey } from './glossary'

const ENTRIES = Object.entries(GLOSSARY) as [TermKey, Term][]

const prose = (term: Term): string => `${term.plain} ${term.catch ?? ''}`

describe('glossary', () => {
  it('never tells anyone what to do with their money', () => {
    // This engine reports; it does not advise. A glossary is exactly where advice sneaks in
    // wearing the clothes of helpfulness, so it is rejected by pattern rather than by review.
    const advice =
      /\b(you should|we recommend|recommended that you|a good (buy|investment)|safe bet|worth buying|will (rise|grow|go up)|guaranteed)\b/i

    for (const [key, term] of ENTRIES) {
      expect(prose(term), `${key} gives advice`).not.toMatch(advice)
    }
  })

  it('closes the vocabulary — no explanation leans on a word the app never defines', () => {
    // The failure this prevents is a chain of definitions that dead-ends: "Sortino" explained
    // in terms of downside deviation, which nothing anywhere explains. A jargon word may
    // appear in an explanation only if some entry's title also introduces it.
    const jargon =
      /\b(volatilit\w*|annualis\w*|annualiz\w*|risk-adjusted|stochastic\w*|heteroske\w*|kurtosis|autocorrelat\w*|ex-ante|percentile|quantile|Monte Carlo|bootstrap\w*|degrees of freedom)\b/i

    const defined = ENTRIES.map(([, term]) => term.title.toLowerCase())

    for (const [key, term] of ENTRIES) {
      const offender = jargon.exec(prose(term))
      if (!offender) continue
      const word = offender[0].toLowerCase()
      expect(
        defined.some((title) => title.includes(word)),
        `${key} uses "${offender[0]}", which no entry defines`,
      ).toBe(true)
    }
  })

  it('keeps sentences short enough to still be explaining', () => {
    // Long sentences are how an explanation stops explaining. The cap is generous; anything
    // over it is a sentence that wanted to be two.
    for (const [key, term] of ENTRIES) {
      for (const sentence of prose(term).split(/(?<=[.?!])\s+/)) {
        const words = sentence.trim().split(/\s+/).filter(Boolean)
        expect(words.length, `${key}: "${sentence.trim()}"`).toBeLessThanOrEqual(34)
      }
    }
  })

  it('gives every term a title, a definition, and a group', () => {
    for (const [key, term] of ENTRIES) {
      expect(term.title.length, key).toBeGreaterThan(0)
      expect(term.plain.length, key).toBeGreaterThan(20)
      expect(TERM_GROUPS, key).toContain(term.group)
      // A definition that just restates the label has explained nothing.
      expect(term.plain.toLowerCase().trim(), key).not.toBe(term.title.toLowerCase().trim())
    }
  })

  it('files every term into exactly one group, and leaves no group empty', () => {
    const filed = TERM_GROUPS.flatMap((group) => termsIn(group))
    expect(filed).toHaveLength(ENTRIES.length)
    for (const group of TERM_GROUPS) {
      expect(termsIn(group).length, `${group} is empty`).toBeGreaterThan(0)
    }
  })

  it('has no term defined but never attached to anything on screen', () => {
    // The failure this catches is copy that was written for a label, then orphaned when the
    // label was renamed or removed — it keeps passing every other test in this file while
    // explaining something the user can no longer see.
    const sources = walk('src')
      .filter((file) => /\.tsx?$/.test(file) && !file.endsWith('glossary.ts'))
      .map((file) => readFileSync(file, 'utf8'))
      .join('\n')

    const orphans = Object.keys(GLOSSARY).filter(
      (key) => !sources.includes(`'${key}'`) && !sources.includes(`"${key}"`),
    )
    expect(orphans, 'defined but never referenced').toEqual([])
  })

  it('has no two terms sharing a title', () => {
    const titles = ENTRIES.map(([, term]) => term.title)
    expect(new Set(titles).size).toBe(titles.length)
  })
})

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? walk(path) : [path]
  })
}
