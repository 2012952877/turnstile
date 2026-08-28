import { useCallback, useEffect, useRef, useState } from "react"
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react"

/** SmartHive's `packages/core/chat/store.ts`. */
export const ASSISTANT_MIN_W = 360
export const ASSISTANT_MIN_H = 480
export const ASSISTANT_DEFAULT_W = 380
export const ASSISTANT_DEFAULT_H = 600

const INSET = 16
const MOBILE_BREAKPOINT = 680
const STORAGE_KEY = "finops_assistant_size"
const DOCK_STORAGE_KEY = "finops_assistant_dock"

/** A docked panel takes its width out of the dashboard instead of covering it, so the
 *  ceiling is what the dashboard can spare rather than what the window can hold. The
 *  floating panel's 90% would leave a strip too narrow to read a chart in, which is the
 *  one thing the mode exists to avoid. Docking is kept in its own storage entry: the two
 *  modes have independent widths, so undocking must return the panel to the size it had
 *  while floating rather than to whatever the dock was last dragged to. */
const DOCK_MAX_FRACTION = 0.5

/** How much vertical room the panel leaves above itself. The value is bounded on both
 *  sides by measured geometry: the page header's divider sits at 64px and the filter chips
 *  run 77-107px, so the top edge has to land in that 13px band for the panel to cover the
 *  toolbar rather than cut through it. 84px puts it at 68px; the mobile header is 56px, so
 *  the band moves up with it. */
const DESKTOP_RESERVE = 84
const MOBILE_RESERVE = 68

/** A bottom-anchored panel with a fixed height does not merely sit below the toolbar on a
 *  tall window: as the viewport grows its top edge sweeps down through every y value,
 *  including the chip band, where it slices the filter buttons in half. Measured at
 *  1440x700 with a 600px panel, the top landed on 84px. Below this height the default is
 *  full height with the top pinned at the reserve; at and above it the panel takes
 *  SmartHive's 600px and starts at 124px, already clear of the chips. Raising the fixed
 *  height instead would only move which viewport heights break. */
const BAND_JUMP_HEIGHT = 740

export type DragDirection = "left" | "top" | "corner"
export type AssistantSize = { width: number; height: number }
type DockState = { docked: boolean; width: number }

const DOCK_DEFAULT: DockState = { docked: false, width: ASSISTANT_DEFAULT_W }

/** A window shorter than the minimum still has to fit under the page header: staying out
 *  of the header beats honouring the minimum, so the lower bound yields to the upper one.
 *  Rounding keeps a fractional pointer position from persisting a subpixel width. */
function clampSize(value: number, min: number, max: number) {
  return Math.round(Math.max(Math.min(min, max), Math.min(max, value)))
}

function readStored(): AssistantSize | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AssistantSize>
    if (typeof parsed.width !== "number" || typeof parsed.height !== "number") return null
    return { width: parsed.width, height: parsed.height }
  } catch {
    return null
  }
}

function readDock(): DockState {
  try {
    const raw = window.localStorage.getItem(DOCK_STORAGE_KEY)
    if (!raw) return DOCK_DEFAULT
    const parsed = JSON.parse(raw) as Partial<DockState>
    return {
      docked: parsed.docked === true,
      width: typeof parsed.width === "number" ? parsed.width : ASSISTANT_DEFAULT_W,
    }
  } catch {
    return DOCK_DEFAULT
  }
}

const CURSORS: Record<DragDirection, string> = {
  left: "col-resize",
  top: "row-resize",
  corner: "nw-resize",
}

export function useAssistantResize() {
  const [viewport, setViewport] = useState(
    () => ({ width: window.innerWidth, height: window.innerHeight }),
  )
  const [size, setSize] = useState<AssistantSize | null>(readStored)
  const [dock, setDock] = useState<DockState>(readDock)
  /** Which edge is being dragged, so the rail can stay lit for the whole gesture. Hover
   *  alone is not enough: the edge travels with the pointer and a fast drag outruns it,
   *  so the line would go out exactly while it is being used. */
  const [dragging, setDragging] = useState<DragDirection | null>(null)
  const latest = useRef<AssistantSize | null>(null)
  const dockLatest = useRef<number | null>(null)

  useEffect(() => {
    const onResize = () => setViewport({ width: window.innerWidth, height: window.innerHeight })
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  const mobile = viewport.width <= MOBILE_BREAKPOINT
  const reserve = mobile ? MOBILE_RESERVE : DESKTOP_RESERVE
  const maxWidth = Math.min(Math.floor(viewport.width * 0.9), viewport.width - INSET * 2)
  const maxHeight = viewport.height - reserve

  const base: AssistantSize = size ?? {
    width: ASSISTANT_DEFAULT_W,
    height: viewport.height >= BAND_JUMP_HEIGHT ? ASSISTANT_DEFAULT_H : maxHeight,
  }

  /** Mobile pins the panel to both side insets and has no pointer-precise dragging, so it
   *  keeps the stylesheet's geometry rather than an inline size that would override it. */
  const rendered: AssistantSize | null = mobile ? null : {
    width: clampSize(base.width, ASSISTANT_MIN_W, maxWidth),
    height: clampSize(base.height, ASSISTANT_MIN_H, maxHeight),
  }

  const atMax = rendered != null && rendered.width >= maxWidth && rendered.height >= maxHeight

  /** Docking is a two-pane layout and a phone has room for one pane, so the mode is not
   *  offered below the mobile breakpoint. The stored preference survives untouched: a
   *  narrow window suppresses docking rather than cancelling it. */
  const dockable = !mobile
  const docked = dock.docked && dockable
  const maxDockWidth = Math.round(viewport.width * DOCK_MAX_FRACTION)
  const dockWidth = clampSize(dock.width, ASSISTANT_MIN_W, maxDockWidth)

  const commit = useCallback((next: AssistantSize | null) => {
    setSize(next)
    try {
      if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      else window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // Private browsing denies storage; the size still applies for this session.
    }
  }, [])

  const commitDock = useCallback((next: DockState) => {
    setDock(next)
    try {
      window.localStorage.setItem(DOCK_STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Private browsing denies storage; the mode still applies for this session.
    }
  }, [])

  const toggleDock = useCallback(() => {
    commitDock({ docked: !dock.docked, width: dock.width })
  }, [commitDock, dock])

  /** Restoring drops the stored size rather than writing SmartHive's minimum back, so the
   *  panel returns to the default the band-jump rule owns instead of to the smallest size
   *  it can take. */
  const toggleExpand = useCallback(() => {
    commit(atMax ? null : { width: maxWidth, height: maxHeight })
  }, [atMax, commit, maxWidth, maxHeight])

  const startDrag = useCallback((event: ReactPointerEvent, direction: DragDirection) => {
    if (!rendered) return
    event.preventDefault()
    setDragging(direction)
    const startX = event.clientX
    const startY = event.clientY
    const startWidth = docked ? dockWidth : rendered.width
    const startHeight = rendered.height

    const onMove = (moved: globalThis.PointerEvent) => {
      const width = direction === "top" ? startWidth : startWidth - (moved.clientX - startX)
      if (docked) {
        const next = clampSize(width, ASSISTANT_MIN_W, maxDockWidth)
        dockLatest.current = next
        setDock((previous) => ({ ...previous, width: next }))
        return
      }
      const height = direction === "left" ? startHeight : startHeight - (moved.clientY - startY)
      const next = {
        width: clampSize(width, ASSISTANT_MIN_W, maxWidth),
        height: clampSize(height, ASSISTANT_MIN_H, maxHeight),
      }
      latest.current = next
      setSize(next)
    }

    const onUp = () => {
      document.removeEventListener("pointermove", onMove)
      document.removeEventListener("pointerup", onUp)
      setDragging(null)
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
      if (dockLatest.current != null) commitDock({ docked: true, width: dockLatest.current })
      if (latest.current) commit(latest.current)
      dockLatest.current = null
      latest.current = null
    }

    document.addEventListener("pointermove", onMove)
    document.addEventListener("pointerup", onUp)
    document.body.style.cursor = CURSORS[direction]
    document.body.style.userSelect = "none"
  }, [rendered, docked, dockWidth, commit, commitDock, maxDockWidth, maxWidth, maxHeight])

  /** A docked panel is full height by definition and its width is bounded by the pane
   *  beside it, so only the left edge can be dragged. Mobile keeps the stylesheet's
   *  geometry and has no pointer-precise dragging at all. */
  const handles: DragDirection[] = rendered == null
    ? []
    : docked ? ["left"] : ["left", "top", "corner"]

  const style: CSSProperties | undefined = docked
    ? { width: dockWidth }
    : rendered ? { width: rendered.width, height: rendered.height } : undefined

  return { style, handles, dragging, atMax, toggleExpand, startDrag, docked, dockable, toggleDock }
}
