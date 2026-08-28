import { useMemo, useState } from "react"
import type { MouseEvent } from "react"
import { RotateCcw } from "lucide-react"
import { ReferenceArea } from "recharts"

type ChartPointerState = { activeLabel?: string | number }

export function useChartDragZoom<Row extends { date: string }>(rows: Row[]) {
  const [selection, setSelection] = useState<{ start: string; end: string } | null>(null)
  const [zoomRange, setZoomRange] = useState<{ start: string; end: string } | null>(null)
  const zoomedRows = useMemo(() => {
    if (!zoomRange) return rows
    const startIndex = rows.findIndex((row) => row.date === zoomRange.start)
    const endIndex = rows.findIndex((row) => row.date === zoomRange.end)
    if (startIndex < 0 || endIndex < 0) return rows
    return rows.slice(Math.min(startIndex, endIndex), Math.max(startIndex, endIndex) + 1)
  }, [rows, zoomRange])
  const isZoomed = zoomRange !== null && zoomedRows.length < rows.length
  const eventDate = (state: ChartPointerState) => {
    const date = String(state.activeLabel ?? "")
    return zoomedRows.some((row) => row.date === date) ? date : null
  }
  const startSelection = (
    state: ChartPointerState,
    event: MouseEvent<SVGGraphicsElement>,
  ) => {
    const date = eventDate(state)
    if (event.button !== 0 || !date) return
    event.preventDefault()
    setSelection({ start: date, end: date })
  }
  const moveSelection = (state: ChartPointerState) => {
    if (!selection) return
    const date = eventDate(state)
    if (date && date !== selection.end) setSelection({ ...selection, end: date })
  }
  const finishSelection = (state?: ChartPointerState) => {
    if (!selection) return
    const finalEnd = state ? eventDate(state) ?? selection.end : selection.end
    const startIndex = zoomedRows.findIndex((row) => row.date === selection.start)
    const endIndex = zoomedRows.findIndex((row) => row.date === finalEnd)
    setSelection(null)
    if (startIndex < 0 || endIndex < 0 || startIndex === endIndex) return
    const left = Math.min(startIndex, endIndex)
    const right = Math.max(startIndex, endIndex)
    if (left === 0 && right === zoomedRows.length - 1) return
    setZoomRange({ start: zoomedRows[left].date, end: zoomedRows[right].date })
  }
  const resetZoom = () => {
    setSelection(null)
    setZoomRange(null)
  }
  const selectionArea = selection && selection.start !== selection.end
    ? <ReferenceArea
        x1={selection.start}
        x2={selection.end}
        fill="var(--brand)"
        fillOpacity={0.12}
        stroke="var(--brand)"
        strokeOpacity={0.55}
        pointerEvents="none"
      />
    : null

  return {
    finishSelection,
    isZoomed,
    moveSelection,
    resetZoom,
    selectionArea,
    startSelection,
    zoomedRows,
  }
}

export function ChartZoomReset({ onClick }: { onClick: () => void }) {
  return <button
    type="button"
    className="usage-chart-zoom-reset"
    aria-label="重置图表缩放"
    title="重置图表缩放"
    onClick={onClick}
  ><RotateCcw size={14} /></button>
}