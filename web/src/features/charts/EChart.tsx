import { useEffect, useRef } from 'react'
import { useComputedColorScheme } from '@mantine/core'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'

/**
 * The charting surface. **D-18: Apache ECharts.**
 *
 * Chosen against the constraints in §5.7 rather than on general merit. It renders to canvas,
 * so a decade of daily bars is one draw rather than three thousand DOM nodes; `connect` gives
 * charts 1–3 a genuinely shared crosshair and one brush across all three, which is the
 * difference between three charts and one instrument; `getDataURL` is the PNG export; and it
 * has first-class heatmaps, `markLine`, `markArea` and `visualMap`, which charts 3, 7, 12 and
 * 13 all need and which a general-purpose plotting library makes into custom work.
 *
 * It was already a dependency of this project and unused until now.
 *
 * The whole library is imported rather than the tree-shakeable `echarts/core` build. The
 * shaken build requires every chart type and component to be registered by hand, and a missed
 * registration fails at runtime by *not drawing part of a chart* — an axis, a mark, a tooltip —
 * which is the one failure mode this application cannot tolerate quietly. The bundle is
 * bigger; the charts are either correct or absent.
 */
export function EChart({
  option,
  height,
  group,
  onReady,
  ariaLabel,
}: {
  option: EChartsOption
  height: number
  /** Charts sharing a group share a crosshair and a zoom range (§5.3, charts 1–3). */
  group?: string
  onReady?: (instance: echarts.ECharts) => void
  ariaLabel: string
}) {
  const holder = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const scheme = useComputedColorScheme('light')

  // The latest option, readable from the setup effect without making it a dependency of it.
  // Rebuilding the chart on every render would throw away the user's zoom on each keystroke
  // elsewhere on the page.
  const latest = useRef(option)
  latest.current = option

  // A chart's theme is fixed when it is created, so a scheme change means a new instance.
  //
  // The new instance must be given its option here, in the same effect that made it. Leaving
  // that to the effect below — whose dependency is the option, which did not change — produces
  // a live, correctly sized, entirely blank chart: the DOM element has an ECharts instance, no
  // error is raised, and nothing is drawn. Charts that happened to re-render afterwards healed
  // themselves and charts that did not stayed empty, so the same page showed four working
  // charts and five blank frames with no way to tell from the code which would be which.
  useEffect(() => {
    const element = holder.current
    if (!element) return

    const instance = echarts.init(element, scheme === 'dark' ? 'dark' : undefined, {
      renderer: 'canvas',
    })
    chart.current = instance
    if (group !== undefined) {
      instance.group = group
      echarts.connect(group)
    }
    instance.setOption(latest.current, true)
    onReady?.(instance)

    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(element)

    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
    // `onReady` is deliberately not a dependency: it hands the instance to the export button,
    // and re-running for a new closure identity would rebuild the chart on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, scheme])

  useEffect(() => {
    // `true` replaces the option rather than merging it. Merging leaves the previous run's
    // series in place when the new one has fewer, which draws one fold's data under another
    // fold's label.
    chart.current?.setOption(option, true)
  }, [option])

  // A figure, not a div with role="img": the chart is a canvas with no accessible content of
  // its own, and every number it draws is also printed in the caption or the footer beside it.
  return <figure aria-label={ariaLabel} ref={holder} style={{ height, margin: 0, width: '100%' }} />
}
