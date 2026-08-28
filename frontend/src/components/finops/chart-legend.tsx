import { useCallback, useState } from "react"

export type ChartLegendItem = {
  key: string
  label: string
  color?: string
  markClassName?: string
}

export function useSeriesToggle() {
  const [hiddenSeries, setHiddenSeries] = useState<string[]>([])
  const toggleSeries = useCallback((key: string) => {
    setHiddenSeries((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }, [])
  const isHidden = useCallback((key: string) => hiddenSeries.includes(key), [hiddenSeries])
  return { hiddenSeries, toggleSeries, isHidden }
}

export function ChartSeriesLegend({ className, items, hiddenSeries, onToggle }: {
  className: string
  items: ChartLegendItem[]
  hiddenSeries: string[]
  onToggle: (key: string) => void
}) {
  return <div className={className}>
    {items.map((item) => {
      const hidden = hiddenSeries.includes(item.key)
      return <button
        key={item.key}
        type="button"
        className="chart-legend-toggle"
        aria-pressed={!hidden}
        data-hidden={hidden ? "true" : undefined}
        onClick={() => onToggle(item.key)}
      >
        <i className={item.markClassName} style={item.color ? { background: item.color } : undefined} />{item.label}
      </button>
    })}
  </div>
}
