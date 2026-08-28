import {
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react"

type ResizablePaneOptions = {
  storageKey: string
  defaultWidth: number
  minWidth: number
  maxWidth: number
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value))
}

export function useResizablePane({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
}: ResizablePaneOptions) {
  const [width, setWidth] = useState(() => {
    const stored = Number(localStorage.getItem(storageKey))
    return Number.isFinite(stored) && stored > 0
      ? clamp(stored, minWidth, maxWidth)
      : defaultWidth
  })

  const saveWidth = (value: number) => {
    const next = clamp(value, minWidth, maxWidth)
    setWidth(next)
    localStorage.setItem(storageKey, String(next))
  }

  const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = width
    const handle = event.currentTarget
    const pointerId = event.pointerId
    let nextWidth = width
    const move = (moveEvent: PointerEvent) => {
      nextWidth = clamp(startWidth + moveEvent.clientX - startX, minWidth, maxWidth)
      setWidth(nextWidth)
    }
    const finish = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", finish)
      window.removeEventListener("pointercancel", finish)
      window.removeEventListener("blur", finish)
      handle.removeEventListener("lostpointercapture", finish)
      if (handle.hasPointerCapture?.(pointerId)) handle.releasePointerCapture?.(pointerId)
      document.body.classList.remove("is-resizing-runtime-pane")
      localStorage.setItem(storageKey, String(nextWidth))
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", finish)
    window.addEventListener("pointercancel", finish)
    window.addEventListener("blur", finish)
    handle.addEventListener("lostpointercapture", finish)
    if (event.isTrusted) handle.setPointerCapture?.(pointerId)
    document.body.classList.add("is-resizing-runtime-pane")
  }

  const resizeWithKeyboard = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const step = event.shiftKey ? 32 : 16
    if (event.key === "ArrowLeft") {
      event.preventDefault()
      saveWidth(width - step)
    } else if (event.key === "ArrowRight") {
      event.preventDefault()
      saveWidth(width + step)
    } else if (event.key === "Home") {
      event.preventDefault()
      saveWidth(minWidth)
    } else if (event.key === "End") {
      event.preventDefault()
      saveWidth(maxWidth)
    }
  }

  return {
    width,
    minWidth,
    maxWidth,
    startResize,
    resizeWithKeyboard,
    resetWidth: () => saveWidth(defaultWidth),
  }
}
