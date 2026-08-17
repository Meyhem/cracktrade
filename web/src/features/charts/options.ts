/**
 * The chart rules from §5.7, expressed once as option fragments.
 *
 * Written as shared builders rather than as a style guide, because a rule a chart has to
 * remember to follow is a rule some chart will not follow. Every percentage axis on this tab
 * includes zero because `percentAxis` includes zero; there is no second place to get it wrong.
 *
 * The rules, and why each is not a preference:
 *
 * - **zero on every percentage axis.** A truncated baseline turns a 2% move into a cliff, and
 *   it is the single most effective way to make a chart lie while every number on it is true;
 * - **no dual y-axes.** Two units on one frame invent a correlation between them;
 * - **colour is never the only encoding.** Green and red carry sign here, and roughly one man
 *   in twelve cannot separate them — so sign is always also position relative to a drawn zero
 *   line, or a direct label, or a fill pattern;
 * - **hover gives exact figures, and nothing important lives only on hover.**
 */

import type { MarkLineComponentOption, XAXisComponentOption, YAXisComponentOption } from 'echarts'

/** Sign colours. Always paired with position, shape or a label — never load-bearing alone. */
export const SIGN = {
  positive: '#2f9e44',
  negative: '#e03131',
  neutral: '#868e96',
  strategy: '#1971c2',
  benchmark: '#f08c00',
  train: '#adb5bd',
} as const

export const GRID = { left: 64, right: 24, top: 28, bottom: 40, containLabel: true } as const

/**
 * A percentage axis that always contains zero.
 *
 * ECharts' default `min`/`max` are data-driven, which produces exactly the truncated baseline
 * §5.7 forbids: a series between 12% and 14% would otherwise be drawn as a full-height climb.
 */
export function percentAxis(name?: string): YAXisComponentOption {
  return {
    type: 'value',
    ...(name === undefined ? {} : { name, nameGap: 40, nameLocation: 'middle' as const }),
    min: (value: { min: number; max: number }) => Math.min(0, value.min),
    max: (value: { min: number; max: number }) => Math.max(0, value.max),
    axisLabel: { formatter: percentTick },
    splitLine: { show: true },
  }
}

/**
 * A percentage on an axis, at `digits` decimals at most.
 *
 * The bound comes back from `min`/`max` as whatever float the data produced, and printed raw
 * it reads `239.0172149420057%` — sixteen significant figures of precision that the underlying
 * return does not have and that no reader wants. Every other percentage in this application
 * goes through `percent()` at one decimal; an axis label is not the place to start disagreeing.
 *
 * `digits` exists for the one quantity where one decimal is genuinely too coarse: a mean *per
 * bar* return lives in thousandths of a percent, and at one decimal its axis reads
 * "0% 0% 0% 0.1% 0.1%" — six ticks, three distinct labels, and a zero line the reader cannot
 * place against them. The CLI prints that same figure at three decimals, so this follows it
 * rather than inventing a third convention.
 */
function percentTick(value: number, digits = 1): string {
  return `${Number.isInteger(value) ? value : Number(value.toFixed(digits))}%`
}

/** A currency axis. Not forced through zero: an equity curve starting at $10,000 is not a ratio. */
export function moneyAxis(name?: string): YAXisComponentOption {
  return {
    type: 'value',
    ...(name === undefined ? {} : { name, nameGap: 48, nameLocation: 'middle' as const }),
    scale: true,
    axisLabel: { formatter: (value: number) => `$${Math.round(value).toLocaleString()}` },
  }
}

/**
 * The same zero-inclusive percentage axis, horizontally.
 *
 * Two functions rather than one generic because ECharts types the two axes separately, and a
 * cast between them would defeat the point of having the compiler check that a chart's axes
 * are the ones it thinks they are.
 */
export function percentValueAxis(digits = 1): XAXisComponentOption {
  return {
    type: 'value',
    min: (value: { min: number; max: number }) => Math.min(0, value.min),
    max: (value: { min: number; max: number }) => Math.max(0, value.max),
    axisLabel: { formatter: (value: number) => percentTick(value, digits) },
  }
}

export function timeAxis(): XAXisComponentOption {
  return { type: 'time', axisLine: { show: true }, splitLine: { show: false } }
}

type MarkLineData = NonNullable<MarkLineComponentOption['data']>

/**
 * Reference lines on a chart.
 *
 * The zero line is the reason this exists. Sign has to be readable as *position* — a value
 * above or below a drawn line — and not only as red or green, so every chart carrying a signed
 * quantity draws one. Colour is the second encoding here, never the first.
 */
export function marks(
  data: MarkLineData,
  options: { dashed?: boolean; label?: string } = {},
): MarkLineComponentOption {
  return {
    silent: true,
    symbol: 'none',
    lineStyle: {
      color: SIGN.neutral,
      type: options.dashed === true ? 'dashed' : 'solid',
      width: 1,
    },
    // Inside the plot, not past its right edge. ECharts places a mark-line label at the end of
    // the line by default, which on a narrow column puts it beyond the grid and clips it to
    // "bas" — a label that has to be guessed at is not a label.
    // `rotate: 0` because ECharts lays a mark-line label along its line, which stands the text
    // of a vertical threshold marker on its end — legible in principle, unreadable in a 140px
    // panel where it is also clipped by the top of the grid.
    label:
      options.label === undefined
        ? { show: false }
        : { show: true, formatter: options.label, position: 'insideEndTop', rotate: 0 },
    data,
  }
}

export function zeroLine(axis: 'x' | 'y' = 'y'): MarkLineComponentOption {
  return marks([axis === 'y' ? { yAxis: 0 } : { xAxis: 0 }])
}

/** Exact figures on hover, with a crosshair — never the only place a figure appears. */
export const CROSSHAIR = {
  trigger: 'axis' as const,
  axisPointer: { type: 'cross' as const },
} as const

/**
 * Forward-filled bars, marked on a time-domain chart.
 *
 * A filled bar repeats the previous session wholesale — it is not an observed price, and a
 * line drawn through it says a trade happened at a price nobody quoted (spec §4.3, §8.1).
 * Drawn as thin vertical marks rather than as a gap, so the series stays continuous while the
 * bars that were not measured are visible.
 *
 * Above a few dozen, the marks are dropped and the count is stated in the caption instead:
 * three hundred hairlines is a grey wash that hides the thing it was meant to show, and the
 * honest reading of a history that filled that often is "the chart is not trustworthy", which
 * is a sentence rather than a mark.
 */
export const MARKABLE_FILLED_BARS = 40

export function filledMarkData(dates: string[]): MarkLineData {
  if (dates.length === 0 || dates.length > MARKABLE_FILLED_BARS) return []
  return dates.map((date) => ({ xAxis: date }))
}

export function filledMarks(dates: string[]): MarkLineComponentOption | undefined {
  const data = filledMarkData(dates)
  if (data.length === 0) return undefined
  return {
    silent: true,
    symbol: 'none',
    lineStyle: { color: SIGN.neutral, type: 'dotted', width: 1, opacity: 0.7 },
    label: { show: false },
    data,
  }
}
