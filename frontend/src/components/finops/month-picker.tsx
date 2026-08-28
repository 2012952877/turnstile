import { useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover"
import { getIntlLocale } from "../../locales/index"

/** A month picker, because a budget period is a month.
 *
 *  SmartHive has a day-level `Calendar` (react-day-picker) and no month picker, so this
 *  follows that component's visual language — stepper nav plus a grid of cells — without
 *  pulling in a day-granularity dependency to render twelve buttons.
 *
 *  It replaces a flat list of 25 "August 2025 … August 2027" rows. That list made the
 *  reader scan a sentence per option to find a month, and gave no sense of where the
 *  current month sat; a year with twelve cells is read at a glance.
 */

const MONTH_COUNT = 12

export function MonthPicker({
  value,
  min,
  max,
  onChange,
  label,
  ariaLabel,
}: {
  value: string
  min: string
  max: string
  onChange: (value: string) => void
  label: string
  ariaLabel: string
}) {
  const selectedYear = Number(value.slice(0, 4))
  const [open, setOpen] = useState(false)
  // The visible year is its own state: opening the picker on a month in 2026 and paging
  // to 2027 must not change the selection until a cell is actually chosen.
  const [year, setYear] = useState(selectedYear)

  const locale = getIntlLocale()
  const monthName = (index: number) => new Intl.DateTimeFormat(locale, {
    month: "short",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, index, 1)))

  const valueFormat = new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "long",
    timeZone: "UTC",
  })
  const valueLabel = valueFormat.format(new Date(`${value}-01T00:00:00Z`))
  // The caret must not slide as the month changes, but a fixed width is wrong: "September
  // 2026" and "2026年8月" differ by about 40 px. Every candidate label for the selected
  // year is stacked in the same grid cell and hidden, so the browser sizes the area to the
  // widest one this locale can actually produce.
  const widthCandidates = Array.from({ length: MONTH_COUNT }, (_, index) =>
    valueFormat.format(new Date(Date.UTC(selectedYear, index, 1))))

  const current = new Date().toISOString().slice(0, 7)
  const minYear = Number(min.slice(0, 4))
  const maxYear = Number(max.slice(0, 4))

  return <Popover
    open={open}
    onOpenChange={(next) => {
      // Reopening always lands on the selected month's year rather than wherever the
      // reader last paged to, which would otherwise persist as a hidden surprise.
      if (next) setYear(selectedYear)
      setOpen(next)
    }}
  >
    <PopoverTrigger className="month-picker-trigger" aria-label={ariaLabel}>
      <span className="month-picker-label">{label}</span>
      <span className="month-picker-value">
        <span>{valueLabel}</span>
        {widthCandidates.map((label) => <span key={label} aria-hidden>{label}</span>)}
      </span>
      <ChevronRight size={13} className="month-picker-caret" />
    </PopoverTrigger>
    <PopoverContent className="month-picker-popup" align="start">
      <div className="month-picker-nav">
        <button
          type="button"
          onClick={() => setYear(year - 1)}
          disabled={year <= minYear}
          aria-label="上一年"
          title="上一年"
        ><ChevronLeft size={15} /></button>
        <strong>{year}</strong>
        <button
          type="button"
          onClick={() => setYear(year + 1)}
          disabled={year >= maxYear}
          aria-label="下一年"
          title="下一年"
        ><ChevronRight size={15} /></button>
      </div>
      <div className="month-picker-grid">
        {Array.from({ length: MONTH_COUNT }, (_, index) => {
          const key = `${String(year).padStart(4, "0")}-${String(index + 1).padStart(2, "0")}`
          const disabled = key < min || key > max
          return <button
            key={key}
            type="button"
            disabled={disabled}
            data-selected={key === value || undefined}
            data-current={key === current || undefined}
            onClick={() => { onChange(key); setOpen(false) }}
          >{monthName(index)}</button>
        })}
      </div>
    </PopoverContent>
  </Popover>
}
