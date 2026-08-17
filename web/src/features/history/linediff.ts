/**
 * Which lines of two YAML documents differ.
 *
 * The naive version — compare line *i* of one pane with line *i* of the other — is right only
 * while the two documents have the same shape. Add an indicator and every line after it shifts
 * by four, so a positional comparison marks the whole remainder of the file as changed: the
 * `exit:` block gets highlighted in a diff that never touched it, directly beneath a structured
 * summary correctly reporting seven changes. Over-reporting on the one screen whose job is to
 * say precisely what changed is not a cosmetic problem.
 *
 * So this aligns the two first, by longest common subsequence, and marks only what the
 * alignment could not pair up. Both documents are canonical (the server re-dumps them through
 * the YAML writer), so identical content really does produce identical lines and the alignment
 * has something to work with.
 *
 * O(n·m) in lines, which for configs of a few dozen lines is nothing.
 */

export type Alignment = {
  /** Per line of the left document: true when it has no counterpart on the right. */
  left: boolean[]
  right: boolean[]
}

export function alignLines(left: string[], right: string[]): Alignment {
  const rows = left.length
  const columns = right.length

  // lengths[i][j] = length of the LCS of left[i:] and right[j:]
  const lengths: number[][] = Array.from({ length: rows + 1 }, () =>
    new Array<number>(columns + 1).fill(0),
  )
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = columns - 1; j >= 0; j -= 1) {
      const row = lengths[i]!
      row[j] =
        left[i] === right[j]
          ? lengths[i + 1]![j + 1]! + 1
          : Math.max(lengths[i + 1]![j]!, lengths[i]![j + 1]!)
    }
  }

  const changedLeft = new Array<boolean>(rows).fill(true)
  const changedRight = new Array<boolean>(columns).fill(true)

  let i = 0
  let j = 0
  while (i < rows && j < columns) {
    if (left[i] === right[j]) {
      changedLeft[i] = false
      changedRight[j] = false
      i += 1
      j += 1
    } else if (lengths[i + 1]![j]! >= lengths[i]![j + 1]!) {
      i += 1
    } else {
      j += 1
    }
  }

  return { left: changedLeft, right: changedRight }
}
