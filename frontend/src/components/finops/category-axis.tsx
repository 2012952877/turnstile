import { useEffect, useRef, useState } from "react"

const categoryTickFontSize = 11
const categoryTickLineHeight = 13
const categoryTickMaxLines = 2
export const categoryTickGutter = 10
const categoryAxisGutter = 14
const categoryLabelBreakAfter = new Set([" ", "-", "_", ".", "@", "/", ":", ",", "，", "、", "·", ")", "]", "}"])

let categoryMeasureContext: CanvasRenderingContext2D | null = null

function categoryLabelWidth(name: string) {
  if (typeof document === "undefined") return name.length * 7
  if (!categoryMeasureContext) {
    const context = document.createElement("canvas").getContext("2d")
    if (context) context.font = `400 ${categoryTickFontSize}px ${getComputedStyle(document.body).fontFamily}`
    categoryMeasureContext = context
  }
  if (!categoryMeasureContext) return name.length * 7
  return categoryMeasureContext.measureText(name).width
}

export function categoryAxisWidth(names: string[], chartWidth: number) {
  const capText = Math.max(56, Math.min(Math.floor(chartWidth * 0.3), 132))
  const hardCap = Math.max(capText, Math.min(Math.floor(chartWidth * 0.4), 150))
  let needed = 0
  for (const name of names) needed = Math.max(needed, categoryLabelBlockWidth(name, capText, hardCap))
  return Math.min(hardCap, Math.max(50, Math.ceil(needed))) + categoryAxisGutter
}

function categoryLabelBlockWidth(name: string, maxWidth: number, hardCap: number) {
  const full = categoryLabelWidth(name)
  if (full <= maxWidth) return full
  const bySeparator = splitCategoryLabel(name, hardCap, categoryLabelSeparatorPoints(name))
  if (bySeparator.balanced) return bySeparator.balanced.widest
  const byCharacter = splitCategoryLabel(name, hardCap, categoryLabelCharacterPoints(name))
  if (byCharacter.balanced) return byCharacter.balanced.widest
  return hardCap
}

function isCjkLabelChar(char: string) {
  return /[\u2E80-\u303F\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF]/.test(char)
}

function categoryLabelSeparatorPoints(text: string) {
  const points: number[] = []
  for (let index = 0; index < text.length - 1; index += 1) {
    if (categoryLabelBreakAfter.has(text[index]) || isCjkLabelChar(text[index]) || isCjkLabelChar(text[index + 1])) {
      points.push(index + 1)
    }
  }
  return points
}

function categoryLabelCharacterPoints(text: string) {
  const points: number[] = []
  for (let index = 1; index < text.length; index += 1) {
    if (!categoryLabelBreakAfter.has(text[index])) points.push(index)
  }
  return points
}

function truncateCategoryLabel(text: string, maxWidth: number) {
  if (categoryLabelWidth(text) <= maxWidth) return text
  let end = text.length - 1
  while (end > 1 && categoryLabelWidth(`${text.slice(0, end)}…`) > maxWidth) end -= 1
  return `${text.slice(0, end)}…`
}

function splitCategoryLabel(value: string, maxWidth: number, points: number[]) {
  let balanced: { lines: [string, string]; widest: number } | null = null
  let greedy: [string, string] | null = null
  for (const point of points) {
    const head = value.slice(0, point).trimEnd()
    const tail = value.slice(point).trimStart()
    if (!head || !tail) continue
    const headWidth = categoryLabelWidth(head)
    if (headWidth > maxWidth) break
    greedy = [head, tail]
    const tailWidth = categoryLabelWidth(tail)
    if (tailWidth > maxWidth) continue
    const widest = Math.max(headWidth, tailWidth)
    if (!balanced || widest < balanced.widest) balanced = { lines: [head, tail], widest }
  }
  return { balanced, greedy }
}

function wrapCategoryLabel(text: string, maxWidth: number, maxLines: number) {
  const value = text.trim()
  if (!value || maxWidth <= 0) return [value]
  if (categoryLabelWidth(value) <= maxWidth) return [value]
  if (maxLines < 2) return [truncateCategoryLabel(value, maxWidth)]
  const bySeparator = splitCategoryLabel(value, maxWidth, categoryLabelSeparatorPoints(value))
  if (bySeparator.balanced) return bySeparator.balanced.lines
  const byCharacter = splitCategoryLabel(value, maxWidth, categoryLabelCharacterPoints(value))
  if (byCharacter.balanced) return byCharacter.balanced.lines
  const greedy = bySeparator.greedy ?? byCharacter.greedy
  if (greedy) return [greedy[0], truncateCategoryLabel(greedy[1], maxWidth)]
  return [truncateCategoryLabel(value, maxWidth)]
}

export function CategoryAxisTick({ x = 0, y = 0, maxWidth = 0, payload }: {
  x?: number
  y?: number
  maxWidth?: number
  payload?: { value?: string | number; offset?: number }
}) {
  const band = typeof payload?.offset === "number" ? payload.offset * 2 : 0
  const maxLines = band > 0 && band < categoryTickLineHeight * categoryTickMaxLines + 2 ? 1 : categoryTickMaxLines
  const lines = wrapCategoryLabel(String(payload?.value ?? ""), maxWidth, maxLines)
  const top = y - (lines.length - 1) * categoryTickLineHeight / 2
  return <text x={x} y={top} dominantBaseline="central" textAnchor="end" fill="var(--muted-foreground)" fontSize={categoryTickFontSize}>
    {lines.map((line, index) => <tspan key={`${index}-${line}`} x={x} dy={index === 0 ? 0 : categoryTickLineHeight}>{line}</tspan>)}
  </text>
}

export function useChartWidthKey() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const update = (nextWidth: number) => {
      const rounded = Math.round(nextWidth)
      setWidth((current) => current === rounded ? current : rounded)
    }
    update(element.getBoundingClientRect().width)
    const observer = new ResizeObserver((entries) => update(entries[0]?.contentRect.width ?? 0))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width] as const
}