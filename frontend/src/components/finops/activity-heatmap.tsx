import { useMemo } from "react"

import { timezoneBucketKey } from "../../lib/timezone"
import { getIntlLocale } from "../../locales/index"
import type { UsageRequestSummary } from "../../data-sources/apim/types"

const HEATMAP_WEEKS = 26
const CELL_SIZE = 16
const CELL_GAP = 3
const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]
const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

export type ActivityMetric = "cost" | "tokens"

/** One calendar day of the heatmap, already bucketed in the viewer's timezone. */
export type ActivityDay = { date: string; value: number }

function addDaysIso(iso: string, days: number) {
  const date = new Date(`${iso}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function weekStartIso(iso: string) {
  const date = new Date(`${iso}T00:00:00Z`)
  const mondayOffset = (date.getUTCDay() + 6) % 7
  date.setUTCDate(date.getUTCDate() - mondayOffset)
  return date.toISOString().slice(0, 10)
}

function getHeatmapColor(level: number) {
  if (level === 0) return "var(--muted)"
  const opacities = ["20%", "45%", "70%", "100%"]
  return `color-mix(in oklch, var(--chart-1) ${opacities[level - 1]}, transparent)`
}

function formatMetric(value: number, metric: ActivityMetric) {
  if (metric === "cost") return value >= 100 ? `$${value.toFixed(0)}` : `$${value.toFixed(2)}`
  return `${Math.round(value).toLocaleString(getIntlLocale())} tokens`
}

function formatDate(iso: string) {
  return new Date(`${iso}T00:00:00Z`).toLocaleString("en", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })
}

export function ActivityHeatmap({ days, metric, timezone }: { days: ActivityDay[]; metric: ActivityMetric; timezone: string }) {
  const { cells, monthLabels, insights } = useMemo(() => {
    const dateValues = new Map<string, number>()
    for (const day of days) dateValues.set(day.date, (dateValues.get(day.date) ?? 0) + day.value)

    const today = timezoneBucketKey(new Date(), "day", timezone)
    const lastWeekStart = weekStartIso(today)
    const startDate = addDaysIso(lastWeekStart, -(HEATMAP_WEEKS - 1) * 7)
    const todayDate = new Date(`${today}T00:00:00Z`)
    const todayIndex = (HEATMAP_WEEKS - 1) * 7 + ((todayDate.getUTCDay() + 6) % 7)

    const allCells = Array.from({ length: todayIndex + 1 }, (_, index) => {
      const date = addDaysIso(startDate, index)
      return {
        date,
        dayOfWeek: index % 7,
        week: Math.floor(index / 7),
        value: dateValues.get(date) ?? 0,
      }
    })

    const nonZero = allCells.map((cell) => cell.value).filter((value) => value > 0).sort((a, b) => a - b)
    const level = (value: number) => {
      if (value === 0) return 0
      if (nonZero.length <= 1) return 4
      const percentile = nonZero.indexOf(value) / (nonZero.length - 1)
      if (percentile <= 0.25) return 1
      if (percentile <= 0.5) return 2
      if (percentile <= 0.75) return 3
      return 4
    }
    const cells = allCells.map((cell) => ({ ...cell, level: level(cell.value) }))

    const monthLabels: Array<{ label: string; week: number }> = []
    let lastMonth = -1
    for (const cell of cells) {
      const date = new Date(`${cell.date}T00:00:00Z`)
      const month = date.getUTCMonth()
      if (month !== lastMonth && cell.dayOfWeek === 0) {
        monthLabels.push({ label: date.toLocaleString("en", { month: "short", timeZone: "UTC" }), week: cell.week })
        lastMonth = month
      }
    }

    let busiestDay: { date: string; value: number } | null = null
    let total = 0
    const weekdaySum = Array(7).fill(0) as number[]
    const weekdayCount = Array(7).fill(0) as number[]
    for (const cell of allCells) {
      total += cell.value
      weekdaySum[cell.dayOfWeek] += cell.value
      weekdayCount[cell.dayOfWeek] += 1
      if (cell.value > 0 && (!busiestDay || cell.value > busiestDay.value)) busiestDay = { date: cell.date, value: cell.value }
    }
    const weekdayAverage = weekdaySum.map((sum, index) => sum / Math.max(weekdayCount[index], 1))
    let busiestWeekday = 0
    let quietestWeekday = 0
    weekdayAverage.forEach((value, index) => {
      if (value > weekdayAverage[busiestWeekday]) busiestWeekday = index
      if (value < weekdayAverage[quietestWeekday]) quietestWeekday = index
    })

    return {
      cells,
      monthLabels,
      insights: {
        busiestDay,
        busyDayName: total > 0 ? WEEKDAY_NAMES[busiestWeekday] : null,
        busyDayAverage: weekdayAverage[busiestWeekday],
        quietDayName: total > 0 ? WEEKDAY_NAMES[quietestWeekday] : null,
        quietDayAverage: weekdayAverage[quietestWeekday],
        total,
        windowDays: allCells.length,
      },
    }
  }, [days, timezone])

  const labelWidth = 28
  const svgWidth = labelWidth + HEATMAP_WEEKS * (CELL_SIZE + CELL_GAP)
  const svgHeight = 14 + 7 * (CELL_SIZE + CELL_GAP)

  return <div className="usage-heatmap">
    <div className="usage-heatmap-grid">
      <div className="usage-heatmap-scroll">
        <svg width={svgWidth} height={svgHeight} aria-label="最近 26 周活动热力图">
          {monthLabels.map((month) => <text key={`${month.label}-${month.week}`} x={labelWidth + month.week * (CELL_SIZE + CELL_GAP)} y={10} fontSize={9}>{month.label}</text>)}
          {DAY_LABELS.map((label, index) => label ? <text key={label} x={0} y={14 + index * (CELL_SIZE + CELL_GAP) + CELL_SIZE - 1} fontSize={9}>{label}</text> : null)}
          {cells.map((cell) => <rect key={cell.date} x={labelWidth + cell.week * (CELL_SIZE + CELL_GAP)} y={14 + cell.dayOfWeek * (CELL_SIZE + CELL_GAP)} width={CELL_SIZE} height={CELL_SIZE} rx={3} fill={getHeatmapColor(cell.level)}><title>{cell.date}: {cell.value > 0 ? formatMetric(cell.value, metric) : "No activity"}</title></rect>)}
        </svg>
      </div>
      <div className="usage-heatmap-legend"><span>少</span>{[0, 1, 2, 3, 4].map((level) => <i key={level} style={{ backgroundColor: getHeatmapColor(level) }} />)}<span>多</span></div>
    </div>
    <dl className="usage-heatmap-insights">
      <Insight label="Busiest day" value={insights.busiestDay ? formatDate(insights.busiestDay.date) : "—"} sub={insights.busiestDay ? formatMetric(insights.busiestDay.value, metric) : null} />
      <Insight label="Most active weekday" value={insights.busyDayName ?? "—"} sub={insights.busyDayName ? `avg ${formatMetric(insights.busyDayAverage, metric)}` : null} />
      <Insight label="Quietest weekday" value={insights.quietDayName ?? "—"} sub={insights.quietDayName ? `avg ${formatMetric(insights.quietDayAverage, metric)}` : null} />
      <Insight label={`${insights.windowDays}-day total`} value={formatMetric(insights.total, metric)} />
    </dl>
  </div>
}

function Insight({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return <div><dt>{label}</dt><dd>{value}{sub != null && <span>{sub}</span>}</dd></div>
}
