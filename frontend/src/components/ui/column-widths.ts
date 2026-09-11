export const DEFAULT_MIN_COLUMN_WIDTH = 72

export function preserveColumnWidths(
  measured: readonly number[],
  previous: readonly number[] | null,
  minWidths: readonly number[] = [],
): number[] {
  return measured.map((width, index) => {
    if (Number.isFinite(width) && width > 0) return Math.max(1, Math.round(width))
    const remembered = previous?.[index]
    if (remembered !== undefined && Number.isFinite(remembered) && remembered > 0) {
      return Math.max(1, Math.round(remembered))
    }
    const minimum = minWidths[index]
    return minimum !== undefined && Number.isFinite(minimum) && minimum > 0
      ? Math.max(1, Math.round(minimum))
      : DEFAULT_MIN_COLUMN_WIDTH
  })
}