import { Box, Group, Text, Tooltip } from '@mantine/core'
import { ratio } from '../../../lib/format'

/**
 * A parameter's search range, with where it started and where it ended.
 *
 * The shape of the move is the information, not the digits — a value that slid from the middle
 * to the middle is a different fact from one that ran to the edge and stopped, and two columns
 * of numbers present them identically.
 */
export function RangeTrack({
  low,
  high,
  oldValue,
  newValue,
  atBound,
}: {
  low: number | null
  high: number | null
  oldValue: number | null
  newValue: number | null
  atBound: boolean
}) {
  if (low === null || high === null || high <= low) {
    return (
      <Text c="dimmed" size="xs">
        no range recorded
      </Text>
    )
  }

  const position = (value: number | null) =>
    value === null ? null : Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))

  const from = position(oldValue)
  const to = position(newValue)

  return (
    <Group gap="xs" wrap="nowrap">
      <Text c="dimmed" size="xs">
        {ratio(low, 1)}
      </Text>
      <Box
        pos="relative"
        style={{
          flex: 1,
          minWidth: 120,
          height: 8,
          borderRadius: 4,
          background: 'var(--mantine-color-default-border)',
        }}
      >
        {from !== null && (
          <Tooltip label={`was ${ratio(oldValue, 2)}`}>
            <Box
              pos="absolute"
              style={{
                left: `calc(${from}% - 4px)`,
                top: -2,
                width: 8,
                height: 12,
                borderRadius: 2,
                background: 'var(--mantine-color-gray-5)',
              }}
            />
          </Tooltip>
        )}
        {to !== null && (
          <Tooltip label={`now ${ratio(newValue, 2)}${atBound ? ' — on the bound' : ''}`}>
            <Box
              pos="absolute"
              style={{
                left: `calc(${to}% - 5px)`,
                top: -4,
                width: 10,
                height: 16,
                borderRadius: 2,
                background: atBound
                  ? 'var(--mantine-color-orange-6)'
                  : 'var(--mantine-color-blue-6)',
              }}
            />
          </Tooltip>
        )}
      </Box>
      <Text c="dimmed" size="xs">
        {ratio(high, 1)}
      </Text>
    </Group>
  )
}
