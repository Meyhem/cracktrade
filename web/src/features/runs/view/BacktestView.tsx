import { Alert, Card, Code, Divider, Grid, Group, Stack, Text, Title } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { Explain } from '../../../components/Explain'
import { explanationOf } from '../../../lib/glossary'
import { Figure, MetricsTable, TooFewTrades } from './Figures'
import { TradeList } from './TradeList'
import { backtestResult, figuresOf, isIntraday, type Json } from '../../../lib/result'
import { dateOnly, integer, percent, points } from '../../../lib/format'

/**
 * What the configuration as written would have done.
 *
 * The brief is explicit that this screen leads with the benchmark rather than with the return
 * (§2.4): a strategy returning 13% where the ticker returned 40% is a failure, and a layout
 * that opens with a large green 13% reads as a success. So the headline is the *excess*, and
 * the two returns sit beside it at equal weight.
 */
export function BacktestView({ result }: { result: Json | null }) {
  const backtest = backtestResult(result)
  const figures = figuresOf(backtest.metrics)
  const benchmark = backtest.benchmark

  return (
    <Stack gap="lg">
      <Card padding="md" withBorder>
        <Stack gap="sm">
          <Text c="dimmed" size="sm">
            Simulated exactly as written, against buying and holding{' '}
            {backtest.ticker ?? 'the ticker'} over the same window. No search — this is one
            configuration and one set of numbers.
          </Text>

          {figures.shown ? (
            <Group gap="xl">
              <Figure term="excess" value={points(benchmark?.excessReturnPct ?? null)} />
              <Divider orientation="vertical" />
              <Figure
                label="Strategy"
                term="total_return"
                value={percent(figures.metrics.totalReturnPct, { signed: true })}
              />
              <Figure
                term="buy_and_hold"
                value={percent(benchmark?.metrics?.totalReturnPct ?? null, { signed: true })}
              />
              <Divider orientation="vertical" />
              <Figure
                size="md"
                term="max_drawdown"
                value={percent(figures.metrics.maxDrawdownPct)}
              />
              <Figure size="md" term="trades" value={integer(figures.metrics.totalTrades)} />
            </Group>
          ) : (
            <TooFewTrades trades={figures.trades} />
          )}

          {figures.shown && benchmark?.beatsBuyAndHold === false && (
            <Text size="sm">
              This strategy underperformed simply owning the ticker. Every figure below describes a
              result you could have beaten by doing nothing.
            </Text>
          )}

          {/* Only when non-zero. Zero is the expected state on every daily run and every healthy
              intraday one, and a permanent "0 overnight carries" would be read as decoration
              within a day and then stop being read at all. */}
          {(backtest.overnightCarries ?? 0) > 0 && (
            <Alert
              color="orange"
              icon={<IconAlertTriangle size={18} />}
              title={
                <>
                  {integer(backtest.overnightCarries)} position(s) carried overnight{' '}
                  <Explain term="overnight_carry" />
                </>
              }
              variant="light"
            >
              An intraday position is meant to be closed on its session&apos;s last bar. These were
              not, so their returns include an overnight gap the strategy never chose to hold —
              check the trade list below for which ones.
            </Alert>
          )}
        </Stack>
      </Card>

      {figures.shown && (
        <Stack gap="xs">
          <Group gap={6}>
            <Title order={4}>Metrics</Title>
            <Explain term="metrics" />
          </Group>
          <MetricsTable benchmark={benchmark?.metrics ?? null} metrics={figures.metrics} />
        </Stack>
      )}

      <Grid>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Stack gap="xs">
            <Group gap={6}>
              <Title order={4}>Definedness</Title>
              <Explain term="definedness" />
            </Group>
            <Card padding="md" withBorder>
              <Stack gap="xs">
                <Group gap="xl">
                  <Figure
                    size="md"
                    term="entry_defined"
                    value={percent(backtest.entryDefinedPct)}
                  />
                  <Figure
                    size="md"
                    term="exit_defined"
                    value={
                      backtest.exitDefinedPct === null
                        ? 'no signal exit'
                        : percent(backtest.exitDefinedPct)
                    }
                  />
                  <Figure size="md" term="warmup_bars" value={integer(backtest.warmupBars)} />
                </Group>
                <Text c="dimmed" size="sm">
                  The share of bars on which the condition could actually be evaluated. Several
                  indicators are undefined on most of their bars by design, so a signal defined on
                  8% of bars is a fact you need before reading the returns above.
                </Text>
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 6 }}>
          <Stack gap="xs">
            <Group gap={6}>
              <Title order={4}>Data vintage</Title>
              <Explain term="data_vintage" />
            </Group>
            <Card padding="md" withBorder>
              <Stack gap="xs">
                <Text size="sm">
                  {backtest.vintage?.ticker ?? '—'} · {dateOnly(backtest.vintage?.firstBar ?? null)}{' '}
                  → {dateOnly(backtest.vintage?.lastBar ?? null)} ·{' '}
                  {integer(backtest.vintage?.bars ?? null)} bars
                </Text>
                <Group gap={4} wrap="nowrap">
                  <Text size="sm">
                    {integer(backtest.vintage?.filledBars ?? null)} forward-filled (
                    {percent(backtest.vintage?.filledPct ?? null)}) · fetched{' '}
                    {dateOnly(backtest.vintage?.fetchedOn ?? null)}
                  </Text>
                  <Explain term="forward_filled" />
                </Group>
                <Group gap="xs">
                  <Text c="dimmed" size="sm">
                    Price frame digest
                  </Text>
                  <Explain term="frame_digest" />
                  <Code>{backtest.vintage?.frameDigest ?? '—'}</Code>
                </Group>
                <Text c="dimmed" size="sm">
                  Prices are retroactively adjusted for splits and dividends, so the same run months
                  apart uses different data. Two runs disagreeing is explainable if you can compare
                  digests, and merely alarming if you cannot.
                </Text>
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>

      {backtest.shadowedStops.length > 0 && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
          <Stack gap={4}>
            <Group gap={4} wrap="nowrap">
              <Text fw={600} size="sm">
                Configured stops that did nothing: {backtest.shadowedStops.join(', ')}
              </Text>
              <Explain term="shadowed_stop" />
            </Group>
            <Text size="sm">
              {explanationOf('stop')} Stops follow a priority chain — ATR, then trailing, then fixed
              — and only the highest one set is active. {backtest.activeStop ?? 'None'} was the stop
              in force; the others were configured and had no effect on any trade above.
            </Text>
          </Stack>
        </Alert>
      )}

      <Stack gap="xs">
        <Group gap={6}>
          <Title order={4}>Trades</Title>
          <Explain term="trades" />
        </Group>
        <TradeList intraday={isIntraday(backtest.history)} trades={backtest.trades} />
      </Stack>
    </Stack>
  )
}
