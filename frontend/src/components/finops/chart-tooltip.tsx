import { getIntlLocale } from "../../locales/index"

type TooltipEntry = {
  dataKey?: string | number
  name?: string | number
  value?: string | number
  color?: string
  fill?: string
  payload?: unknown
}

type FinOpsChartTooltipProps = {
  active?: boolean
  label?: unknown
  payload?: readonly TooltipEntry[]
  labelFormatter?: (label: unknown) => string
  nameFormatter?: (name: string) => string
  valueFormatter?: (value: string | number, name: string, payload?: unknown) => string
}

export function FinOpsChartTooltip({
  active,
  label,
  payload,
  labelFormatter = (value) => String(value ?? ""),
  nameFormatter = (value) => value,
  valueFormatter = (value) => Number(value).toLocaleString(getIntlLocale()),
}: FinOpsChartTooltipProps) {
  const entries = payload?.filter((item) => item.value != null) ?? []
  if (!active || entries.length === 0) return null

  return <div className="finops-chart-tooltip">
    {label != null && <div className="finops-chart-tooltip-label">{labelFormatter(label)}</div>}
    <div className="finops-chart-tooltip-items">
      {entries.map((item, index) => {
        const name = String(item.name ?? item.dataKey ?? "")
        return <div className="finops-chart-tooltip-item" key={`${name}-${index}`}>
          <i style={{ background: item.color ?? item.fill ?? "var(--chart-1)" }} />
          <span>{nameFormatter(name)}</span>
          <strong>{valueFormatter(item.value!, name, item.payload)}</strong>
        </div>
      })}
    </div>
  </div>
}