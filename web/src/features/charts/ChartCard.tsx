import { useRef, type ReactNode } from 'react'
import { ActionIcon, Card, Center, Group, Stack, Text, Tooltip } from '@mantine/core'
import { IconDownload, IconPhoto } from '@tabler/icons-react'
import type * as echarts from 'echarts'
import { EChart } from './EChart'
import { explain, type ChartState } from './state'
import { Explain } from '../../components/Explain'
import type { TermKey } from '../../lib/glossary'
import type { EChartsOption } from 'echarts'

/**
 * One chart, in its frame.
 *
 * Three things every chart on this tab has, and none of them are decoration:
 *
 * - **a caption in plain language**, saying what the chart answers and what a bad picture
 *   looks like. The user is a trader, not a statistician, and a chart nobody can read wrong
 *   is worth more than one that is technically richer;
 * - **PNG and CSV export**, because a chart that cannot leave the app gets screenshotted and
 *   loses its labels;
 * - **the full footprint when it is not drawn**. A suppressed chart keeps its height so the
 *   page does not silently shrink — a missing chart that takes no space is indistinguishable
 *   from one that was never specified.
 */
export function ChartCard({
  id,
  title,
  term,
  question,
  badPicture,
  state,
  option,
  height = 320,
  group,
  csv,
  footer,
  children,
}: {
  id: string
  title: string
  /** The glossary entry naming what this chart plots. */
  term?: TermKey
  /** What this chart answers. */
  question: string
  /** What a bad picture looks like. */
  badPicture?: string
  state: ChartState
  option?: EChartsOption
  height?: number
  group?: string
  /** Either a link to the server's own CSV, or rows to build one from. */
  csv?: { href: string } | { filename: string; rows: (string | number | null)[][] }
  /** Annotations that belong under the chart rather than on it. */
  footer?: ReactNode
  /** Replaces the chart entirely — used where a "chart" is really a table or a panel. */
  children?: ReactNode
}) {
  const instance = useRef<echarts.ECharts | null>(null)

  return (
    <Card id={id} padding="md" withBorder>
      <Stack gap="xs">
        <Group align="flex-start" justify="space-between" wrap="nowrap">
          <Stack gap={2}>
            <Group gap={4} wrap="nowrap">
              <Text fw={600}>{title}</Text>
              {term && <Explain term={term} />}
            </Group>
            <Text c="dimmed" size="xs">
              {question}
              {badPicture && ` A bad picture: ${badPicture}`}
            </Text>
          </Stack>
          {state.kind === 'ready' && (
            <Group gap={4} wrap="nowrap">
              {option && (
                <Tooltip label="Download as PNG">
                  <ActionIcon
                    aria-label={`Download ${title} as PNG`}
                    onClick={() => savePng(instance.current, title)}
                    variant="subtle"
                  >
                    <IconPhoto size={16} />
                  </ActionIcon>
                </Tooltip>
              )}
              {csv && (
                <Tooltip label="Download the underlying series as CSV">
                  <ActionIcon
                    aria-label={`Download ${title} as CSV`}
                    onClick={() => saveCsv(csv, title)}
                    variant="subtle"
                  >
                    <IconDownload size={16} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Group>
          )}
        </Group>

        {state.kind !== 'ready' ? (
          <Center h={height} style={{ borderRadius: 4 }}>
            <Text c="dimmed" maw={420} size="sm" ta="center">
              {explain(state)}
            </Text>
          </Center>
        ) : (
          (children ??
          (option && (
            <EChart
              ariaLabel={title}
              height={height}
              {...(group === undefined ? {} : { group })}
              onReady={(chart) => {
                instance.current = chart
              }}
              option={option}
            />
          )))
        )}

        {state.kind === 'ready' && footer}
      </Stack>
    </Card>
  )
}

function savePng(chart: echarts.ECharts | null, title: string): void {
  if (!chart) return
  // `backgroundColor` is set explicitly: the chart's own background is transparent, and a
  // transparent PNG pasted into a document renders as dark text on dark, or vanishes.
  download(
    chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' }),
    `${slug(title)}.png`,
  )
}

function saveCsv(
  csv: { href: string } | { filename: string; rows: (string | number | null)[][] },
  title: string,
): void {
  if ('href' in csv) {
    // The server streams this from the same stored points the chart was drawn from, so the
    // file and the picture are the same bytes. An export that recomputed anything could
    // disagree with the chart it claims to be.
    download(csv.href, `${slug(title)}.csv`)
    return
  }
  const body = csv.rows
    .map((row) => row.map((cell) => (cell === null ? '' : String(cell))).join(','))
    .join('\n')
  download(`data:text/csv;charset=utf-8,${encodeURIComponent(body)}`, csv.filename)
}

function download(href: string, filename: string): void {
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  anchor.click()
}

function slug(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '')
}
