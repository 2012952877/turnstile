import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import type { PointerEvent as ReactPointerEvent, RefObject } from "react"
import type { PinnedReportLayout } from "../assistant/types"

/**
 * Per-card width and per-row height for a pinned report.
 *
 * Width is measured in columns of the 12-column base grid, not pixels. A free pixel width
 * would desync a card from the neighbours sharing its row, and the row is what keeps the
 * cards aligned. Twelve columns make the step small enough (~110px at a 1304px grid) that
 * the snapping is not the coarse three-position jump `auto-fit` produced.
 *
 * Height belongs to the ROW, not the card, and that is the whole design. A row is only
 * level if every card in it is the same height, so a height drag has to set all of them.
 * Storing the result per card meant keeping several copies of one number in sync, and
 * reordering broke it immediately: two rows sized 346 and 418 were re-partitioned into
 * rows holding one card of each. Keyed by row, the number survives a reorder because it
 * never belonged to the cards in the first place.
 *
 * It is a definite `height` rather than a `min-height` because a minimum can only raise a
 * card: a table stored at 351px still rendered 450px, since its own rows set the height.
 * With a definite height the row owns its size and the content scrolls inside it.
 *
 * Sizes come from the report's server-owned layout. Pointer movement stays local and only
 * pointerup replaces the canonical layout, so a drag never turns into a stream of writes.
 * A single-column viewport ignores these desktop dimensions entirely.
 */

/** Below this a chart stops being readable; above it a card no longer fits a laptop
 *  viewport and the report becomes one long scroll of a single card. */
export const CARD_MIN_HEIGHT = 200
export const CARD_MAX_HEIGHT = 1400

/** The floor a width drag may not cross. Expressed in pixels because readability is a
 *  pixel fact: one twelfth of a narrow grid is ~29px, which no chart survives. */
export const CARD_MIN_WIDTH = 260
const RESIZABLE_GRID_MIN_WIDTH = 800

type CardSizes = {
  /** Card id -> column span. */
  spans: Record<string, number>
  /** Row index -> pixel height. */
  rowHeights: Record<number, number>
}

export type ResizeAxis = "width" | "height" | "both"

const CURSORS: Record<ResizeAxis, string> = {
  width: "col-resize",
  height: "row-resize",
  both: "nwse-resize",
}

function clamp(value: number, min: number, max: number) {
  return Math.round(Math.max(min, Math.min(max, value)))
}

function sizesFromLayout(layout: PinnedReportLayout | undefined): CardSizes {
  const rowHeights: Record<number, number> = {}
  for (const [row, height] of Object.entries(layout?.row_heights ?? {})) {
    rowHeights[Number(row)] = height
  }
  return { spans: { ...(layout?.spans ?? {}) }, rowHeights }
}

function layoutFromSizes(sizes: CardSizes): PinnedReportLayout {
  const row_heights: Record<string, number> = {}
  for (const [row, height] of Object.entries(sizes.rowHeights)) {
    row_heights[row] = height
  }
  return { version: 1, spans: { ...sizes.spans }, row_heights }
}

/**
 * The grid's column count and the narrowest span a card may take on it.
 *
 * The count is read from the resolved track list, which is safe on a fixed twelve-column
 * base: every span is clamped to it, so a wide card cannot inflate the track list and
 * validate its own oversized span the way it could under `auto-fit`. The single-column
 * list layout reports 1 from the same read.
 */
function readGeometry(grid: HTMLElement) {
  const style = getComputedStyle(grid)
  const tracks = style.gridTemplateColumns
  const columns = !tracks || tracks === "none"
    ? 1
    : Math.max(1, tracks.split(" ").filter(Boolean).length)

  const gap = Number.parseFloat(style.columnGap) || 0
  const width = grid.getBoundingClientRect().width
  const track = (width - gap * (columns - 1)) / columns
  // A span covers its own tracks plus the gaps closed up between them.
  const minSpan = Math.min(columns, Math.max(1, Math.ceil((CARD_MIN_WIDTH + gap) / (track + gap))))

  return {
    columns,
    gap,
    track,
    minSpan,
    canResize: width >= RESIZABLE_GRID_MIN_WIDTH,
  }
}

/**
 * Which row each card sits in.
 *
 * Derived by replaying the grid's own row-filling rule over the rendered spans, not read
 * back from each card's `y`. Vertical position depends on height, and height is what this
 * is about to set, so measuring it would make the answer depend on its own effect.
 */
function assignRows(grid: HTMLElement, columns: number) {
  const rows: Record<string, number> = {}
  let used = 0
  let row = 0
  for (const item of grid.querySelectorAll<HTMLElement>(".pinned-report-item")) {
    // The shorthand `grid-column: span 4` resolves to `grid-column-start: span 4` with an
    // `auto` end. Reading the end returns "auto" for every card, which silently made all
    // of them span 1 and land in row 0 -- one height drag then resized the whole report.
    const style = getComputedStyle(item)
    const declared = style.gridColumnStart.match(/span\s+(\d+)/)
      ?? style.gridColumnEnd.match(/span\s+(\d+)/)
    const span = Math.min(columns, declared ? Number(declared[1]) : 1)
    if (used > 0 && used + span > columns) {
      row += 1
      used = 0
    }
    if (item.dataset.cardId) rows[item.dataset.cardId] = row
    used += span
    if (used >= columns) {
      row += 1
      used = 0
    }
  }
  return rows
}

function sameRows(left: Record<string, number>, right: Record<string, number>) {
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length
    && keys.every((key) => left[key] === right[key])
}

export function usePinnedCardResize(
  gridRef: RefObject<HTMLElement | null>,
  layout: PinnedReportLayout | undefined,
  onCommit: (layout: PinnedReportLayout) => void,
) {
  const [geometry, setGeometry] = useState({ columns: 1, minSpan: 1, canResize: false })
  const [rowOf, setRowOf] = useState<Record<string, number>>({})
  const [sizes, setSizes] = useState<CardSizes>(() => sizesFromLayout(layout))
  const sizesRef = useRef(sizes)
  sizesRef.current = sizes
  const rowOfRef = useRef(rowOf)
  rowOfRef.current = rowOf

  const measure = useCallback(() => {
    const grid = gridRef.current
    if (!grid) return
    const { columns, minSpan, canResize } = readGeometry(grid)
    setGeometry((current) =>
      current.columns === columns
        && current.minSpan === minSpan
        && current.canResize === canResize
        ? current
        : { columns, minSpan, canResize })
    const rows = assignRows(grid, columns)
    setRowOf((current) => sameRows(current, rows) ? current : rows)
  }, [gridRef])

  /** Before paint, so a card never shows its natural height for a frame and then snaps to
   *  its row's. Both setters bail when nothing changed, so re-running on every render
   *  settles after one pass instead of looping. */
  useLayoutEffect(measure)

  useEffect(() => {
    const next = sizesFromLayout(layout)
    sizesRef.current = next
    setSizes(next)
  }, [layout])

  useEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    const observer = new ResizeObserver(measure)
    observer.observe(grid)
    return () => observer.disconnect()
  }, [gridRef, measure])

  const commit = useCallback((next: CardSizes) => {
    setSizes(next)
    onCommit(layoutFromSizes(next))
  }, [onCommit])

  /** Clears the card's own width and the height of the row it sits in -- the row is the
   *  unit the height was set on, so it is the unit that gets undone. */
  const reset = useCallback((key: string) => {
    const spans = { ...sizesRef.current.spans }
    const rowHeights = { ...sizesRef.current.rowHeights }
    delete spans[key]
    const row = rowOfRef.current[key]
    if (row !== undefined) delete rowHeights[row]
    commit({ spans, rowHeights })
  }, [commit])

  const startResize = useCallback(
    (event: ReactPointerEvent, key: string, axis: ResizeAxis) => {
      const grid = gridRef.current
      const item = (event.currentTarget as HTMLElement).closest<HTMLElement>(".pinned-report-item")
      if (!grid || !item) return
      event.preventDefault()

      const { columns, gap, track, minSpan } = readGeometry(grid)
      const rect = item.getBoundingClientRect()
      const row = rowOfRef.current[key]
      let pending: CardSizes | null = null

      const onMove = (moved: globalThis.PointerEvent) => {
        const next: CardSizes = {
          spans: { ...sizesRef.current.spans },
          rowHeights: { ...sizesRef.current.rowHeights },
        }
        if (axis !== "height") {
          // The pointer names a pixel width; the grid can only honour whole columns, so it
          // snaps to the nearest one instead of leaving the card off the shared rhythm.
          const wanted = moved.clientX - rect.left
          next.spans[key] = clamp((wanted + gap) / (track + gap), minSpan, columns)
        }
        if (axis !== "width" && row !== undefined) {
          next.rowHeights[row] =
            clamp(moved.clientY - rect.top, CARD_MIN_HEIGHT, CARD_MAX_HEIGHT)
        }
        pending = next
        setSizes(pending)
      }

      const onUp = () => {
        document.removeEventListener("pointermove", onMove)
        document.removeEventListener("pointerup", onUp)
        document.body.style.cursor = ""
        document.body.style.userSelect = ""
        if (pending) commit(pending)
      }

      document.addEventListener("pointermove", onMove)
      document.addEventListener("pointerup", onUp)
      document.body.style.cursor = CURSORS[axis]
      document.body.style.userSelect = "none"
    },
    [commit, gridRef],
  )

  /**
   * A stored span is re-clamped against the grid as it is now. The upper bound stops a card
   * from spanning columns that do not exist; the lower bound is the one that matters in
   * practice, because a span saved on a wide screen becomes an unreadable sliver on a
   * narrow one -- one twelfth of a 524px grid is 29px.
   */
  const styleFor = useCallback((key: string) => {
    if (!geometry.canResize) return undefined
    const span = sizes.spans[key]
    const row = rowOf[key]
    const height = row === undefined ? undefined : sizes.rowHeights[row]
    if (!span && height === undefined) return undefined
    return {
      gridColumn: span
        ? `span ${Math.min(Math.max(span, geometry.minSpan), geometry.columns)}`
        : undefined,
      height: height === undefined ? undefined : `${height}px`,
    }
  }, [geometry, rowOf, sizes])

  const isResized = useCallback(
    (key: string) => geometry.canResize
      && (Boolean(sizes.spans[key]) || sizes.rowHeights[rowOf[key]] !== undefined),
    [geometry.canResize, rowOf, sizes],
  )

  return {
    columns: geometry.columns,
    canResize: geometry.canResize,
    startResize,
    reset,
    styleFor,
    isResized,
  }
}
