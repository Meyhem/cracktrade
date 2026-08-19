import dayjs from 'dayjs'
import { describe, expect, it } from 'vitest'
import type { IntervalOption } from '../api/types'
import { defaultRange, intervalHint, rangeTooWide } from './intervals'

const daily: IntervalOption = {
  value: '1d',
  intraday: false,
  max_lookback_days: null,
  evolvable: true,
}
const halfHourly: IntervalOption = {
  value: '30m',
  intraday: true,
  max_lookback_days: 55,
  evolvable: false,
}
const hourly: IntervalOption = {
  value: '1h',
  intraday: true,
  max_lookback_days: 700,
  evolvable: true,
}

describe('defaultRange', () => {
  it('offers a range the interval can actually serve', () => {
    const { start_date, end_date } = defaultRange(halfHourly)
    const span = dayjs(end_date).diff(dayjs(start_date), 'day')

    expect(span).toBeLessThanOrEqual(55)
    expect(rangeTooWide(halfHourly, start_date, end_date)).toBeNull()
  })

  it('ends an intraday range yesterday, because today has not closed', () => {
    // Today's last bar is still forming. Offering it means the range the user reads and the
    // range the run used differ, for a reason they had no way to predict.
    expect(defaultRange(hourly).end_date).toBe(dayjs().subtract(1, 'day').format('YYYY-MM-DD'))
  })

  it('leaves the daily default where it was', () => {
    const { start_date, end_date } = defaultRange(daily)

    expect(end_date).toBe(dayjs().format('YYYY-MM-DD'))
    expect(dayjs(end_date).diff(dayjs(start_date), 'year')).toBe(3)
  })
})

describe('rangeTooWide', () => {
  it('refuses a range wider than the interval reaches, and names the earliest date', () => {
    const message = rangeTooWide(halfHourly, '2020-01-01', '2026-08-18')

    expect(message).not.toBeNull()
    expect(message).toContain('2026-06-24')
  })

  it('accepts a range exactly at the limit', () => {
    // Never stricter than the server. A client check that refused what the engine accepts would
    // make a valid strategy unsaveable, which is worse than the round trip it saves.
    expect(rangeTooWide(halfHourly, '2026-06-24', '2026-08-18')).toBeNull()
  })

  it('never refuses a daily range, however wide', () => {
    expect(rangeTooWide(daily, '1990-01-01', '2026-08-18')).toBeNull()
  })
})

describe('intervalHint', () => {
  it('leads with the session close on every intraday choice', () => {
    expect(intervalHint(hourly)).toContain('closed at each session')
    expect(intervalHint(halfHourly)).toContain('closed at each session')
  })

  it('names the reach where there is one', () => {
    expect(intervalHint(halfHourly)).toContain('55 days')
    expect(intervalHint(daily)).not.toContain('days and no further')
  })
})
