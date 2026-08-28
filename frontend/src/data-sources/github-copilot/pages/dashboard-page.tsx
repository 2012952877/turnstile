import { Fragment, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Activity,
  AlertTriangle,
  Building2,
  CalendarRange,
  ChartPie,
  CircleDollarSign,
  Code2,
  LayoutDashboard,
  LineChart,
  PanelLeft,
  RefreshCw,
  Settings,
  Upload,
  Users,
  Zap,
} from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { copilotApi } from "../api"
import { copilotKeys, copilotQueries } from "../queries"
import type {
  CopilotAiCreditItem,
  CopilotBreakdownItem,
  CopilotDashboard,
  CopilotDailyUsage,
  CopilotMemberUsage,
  CopilotImportedUsage,
  CopilotUsageImportKind,
} from "../types"
import { CopilotConnectButton } from "../connect-button"
import { CopilotLogo } from "../../../components/brand-logos"
import { ChartSeriesLegend, useSeriesToggle } from "../../../components/finops/chart-legend"
import { ChartZoomReset, useChartDragZoom } from "../../../components/finops/chart-drag-zoom"
import { ChartModeToggle, type ChartMode } from "../../../components/finops/chart-mode-toggle"
import { CategoryAxisTick, categoryAxisWidth, categoryTickGutter, useChartWidthKey } from "../../../components/finops/category-axis"
import { FilterMenuField } from "../../../components/finops/filter-menu-field"
import { Button } from "../../../components/ui/button"
import { ResizableGridTable, ResizableTable } from "../../../components/ui/resizable-table"
import { FINOPS_NAVIGATE_EVENT } from "../../../lib/navigation"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select"
import { getIntlLocale } from "../../../locales"

export type CopilotAnalyticsTab = "overview" | "analytics" | "trends"

const headers: Record<CopilotAnalyticsTab, { title: string; icon: typeof Activity }> = {
  overview: { title: "使用指标", icon: LayoutDashboard },
  analytics: { title: "AI 用量", icon: ChartPie },
  trends: { title: "使用报告", icon: LineChart },
}

const compact = new Intl.NumberFormat(getIntlLocale(), {
  notation: "compact",
  maximumFractionDigits: 1,
})
const decimal = new Intl.NumberFormat(getIntlLocale(), { maximumFractionDigits: 1 })
const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
})

function queryError(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function openPage(page: string) {
  const url = new URL(window.location.href)
  url.searchParams.set("page", page)
  window.history.replaceState(null, "", url)
  window.dispatchEvent(new Event(FINOPS_NAVIGATE_EVENT))
}

function WorkspaceState({
  kind,
  title,
  detail,
  action,
}: {
  kind: "loading" | "error" | "empty"
  title: string
  detail?: string
  action?: React.ReactNode
}) {
  return <div className={`copilot-workspace-state ${kind}`}>
    {kind === "loading"
      ? <RefreshCw className="spin" size={20} />
      : kind === "error"
        ? <AlertTriangle size={20} />
        : <CopilotLogo size={24} />}
    <b>{title}</b>
    {detail && <span>{detail}</span>}
    {action}
  </div>
}

function Kpi({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  detail: string
  icon: typeof Activity
}) {
  return <div>
    <span className="finops-kpi-icon"><Icon size={14} /></span>
    <label>{label}</label>
    <strong>{value}</strong>
    <small>{detail}</small>
  </div>
}

function ChartTooltip({ active, payload, label, labelFormatter }: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; color?: string }>
  label?: string
  labelFormatter?: (label: string) => string
}) {
  if (!active || !payload?.length) return null
  return <div className="finops-chart-tooltip">
    <b className="finops-chart-tooltip-label">{labelFormatter?.(label ?? "") ?? label}</b>
    <div className="finops-chart-tooltip-items">
      {payload.map((item) => <div className="finops-chart-tooltip-item" key={item.name}>
        <i style={{ background: item.color }} />
        <span>{item.name}</span>
        <strong>{decimal.format(item.value ?? 0)}</strong>
      </div>)}
    </div>
  </div>
}

type DailySeriesKey =
  | "active_users"
  | "weekly_active_users"
  | "monthly_active_users"
  | "agent_users"
  | "chat_users"
  | "interactions"
  | "generations"
  | "acceptances"
  | "loc_added"
  | "loc_deleted"
  | "acceptance_rate"

type DailyChartRow = { date: string; day: string; label: string } & Record<DailySeriesKey, number>
type DailySeries = { key: DailySeriesKey; label: string; color: string }

const activitySeries: DailySeries[] = [
  { key: "interactions", label: "交互", color: "var(--chart-1)" },
  { key: "acceptances", label: "采纳", color: "var(--chart-4)" },
]

const activeUserSeries: DailySeries[] = [
  { key: "agent_users", label: "Agent", color: "var(--destructive)" },
  { key: "chat_users", label: "Chat", color: "var(--warning)" },
  { key: "active_users", label: "DAU", color: "var(--success)" },
  { key: "monthly_active_users", label: "MAU", color: "var(--chart-3)" },
  { key: "weekly_active_users", label: "WAU", color: "var(--chart-1)" },
]

const engagementFallbackSeries: DailySeries[] = [
  { key: "active_users", label: "DAU", color: "var(--success)" },
  { key: "interactions", label: "交互", color: "var(--chart-1)" },
  { key: "generations", label: "生成", color: "var(--chart-3)" },
  { key: "acceptances", label: "采纳", color: "var(--chart-4)" },
]

const locSeries: DailySeries[] = [
  { key: "loc_added", label: "新增行", color: "var(--chart-1)" },
  { key: "loc_deleted", label: "删除行", color: "var(--chart-4)" },
]

const acceptanceRateSeries: DailySeries[] = [
  { key: "acceptance_rate", label: "采纳率", color: "var(--success)" },
]

const dailyRows = (daily: CopilotDailyUsage[]): DailyChartRow[] => daily.map((item) => ({
  date: item.day,
  day: item.day,
  label: new Intl.DateTimeFormat(getIntlLocale(), { month: "2-digit", day: "2-digit" })
    .format(new Date(`${item.day}T00:00:00Z`)),
  active_users: item.active_users,
  weekly_active_users: item.weekly_active_users ?? 0,
  monthly_active_users: item.monthly_active_users ?? 0,
  agent_users: item.agent_users ?? 0,
  chat_users: item.chat_users ?? 0,
  interactions: item.interactions,
  generations: item.generations,
  acceptances: item.acceptances,
  loc_added: item.loc_added,
  loc_deleted: item.loc_deleted,
  acceptance_rate: item.generations ? item.acceptances / item.generations * 100 : 0,
}))

function ActivityHeatmap({
  data,
  visibleSeries,
}: {
  data: DailyChartRow[]
  visibleSeries: DailySeries[]
}) {
  const maxima = Object.fromEntries(visibleSeries.map((series) => [
    series.key,
    Math.max(...data.map((item) => item[series.key]), 1),
  ])) as Record<DailySeriesKey, number>
  return <div className="copilot-activity-heatmap">
    <div className="copilot-activity-heatmap-scroll">
      <div className="copilot-activity-heatmap-grid" style={{ gridTemplateColumns: `56px repeat(${data.length}, minmax(18px, 1fr))` }}>
        {visibleSeries.map((series) => <Fragment key={series.key}>
          <b>{series.label}</b>
          {data.map((item) => {
            const value = item[series.key]
            const intensity = value === 0 ? 0 : Math.max(18, Math.round(value / maxima[series.key] * 100))
            return <i
              key={`${series.key}-${item.day}`}
              title={`${item.day} · ${series.label} ${decimal.format(value)}`}
              style={{ background: intensity === 0 ? "var(--muted)" : `color-mix(in oklch, ${series.color} ${intensity}%, transparent)` }}
            />
          })}
        </Fragment>)}
        <span />
        {data.map((item, index) => <time key={item.day}>{index === 0 || index === data.length - 1 || index % 7 === 0 ? item.label : ""}</time>)}
      </div>
    </div>
    <div className="copilot-heatmap-intensity"><span>低</span>{[0, 25, 50, 75, 100].map((intensity) => <i key={intensity} style={{ background: intensity === 0 ? "var(--muted)" : `color-mix(in oklch, var(--chart-1) ${intensity}%, transparent)` }} />)}<span>高</span></div>
  </div>
}

function ActivityChart({ daily, mode, hiddenSeries, series, percent = false }: {
  daily: CopilotDailyUsage[]
  mode: ChartMode
  hiddenSeries: string[]
  series: DailySeries[]
  percent?: boolean
}) {
  const data = dailyRows(daily)
  const {
    finishSelection,
    isZoomed,
    moveSelection,
    resetZoom,
    selectionArea,
    startSelection,
    zoomedRows,
  } = useChartDragZoom(data)
  if (!daily.length) {
    return <WorkspaceState kind="empty" title="暂无活动数据" detail="当前报告周期没有 Copilot 活动。" />
  }
  const visibleSeries = series.filter((item) => !hiddenSeries.includes(item.key))
  if (mode === "heatmap") return <ActivityHeatmap data={data} visibleSeries={visibleSeries} />
  const labelByDate = new Map(zoomedRows.map((row) => [row.date, row.label]))
  const formatDateLabel = (value: string) => labelByDate.get(value) ?? value
  return <div className="copilot-chart-canvas usage-daily-chart finops-chart-surface" data-zoomed={isZoomed || undefined}>
    {isZoomed && <ChartZoomReset onClick={resetZoom} />}
    <ResponsiveContainer width="100%" height="100%">
      {mode === "bar"
        ? <BarChart data={zoomedRows} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} onMouseDown={startSelection} onMouseMove={moveSelection} onMouseUp={finishSelection} onMouseLeave={() => finishSelection()}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="date" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} tickMargin={8} interval="preserveStartEnd" />
          <YAxis tickLine={false} axisLine={false} tickMargin={8} width={48} allowDecimals={percent} tickFormatter={(value) => percent ? `${decimal.format(Number(value))}%` : compact.format(Number(value))} />
          <Tooltip cursor={false} content={<ChartTooltip labelFormatter={formatDateLabel} />} />
          {selectionArea}
          {visibleSeries.map((series) => <Bar key={series.key} dataKey={series.key} name={series.label} fill={series.color} radius={[3, 3, 0, 0]} maxBarSize={42} isAnimationActive={false} />)}
        </BarChart>
        : <RechartsLineChart data={zoomedRows} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} onMouseDown={startSelection} onMouseMove={moveSelection} onMouseUp={finishSelection} onMouseLeave={() => finishSelection()}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="date" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} tickMargin={8} interval="preserveStartEnd" />
          <YAxis tickLine={false} axisLine={false} tickMargin={8} width={48} allowDecimals={percent} tickFormatter={(value) => percent ? `${decimal.format(Number(value))}%` : compact.format(Number(value))} />
          <Tooltip cursor={false} content={<ChartTooltip labelFormatter={formatDateLabel} />} />
          {selectionArea}
          {visibleSeries.map((series) => <Line key={series.key} type="linear" dataKey={series.key} name={series.label} stroke={series.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />)}
        </RechartsLineChart>}
    </ResponsiveContainer>
  </div>
}

function ActivityPanel({
  daily,
  title = "Copilot 活动",
  series = activitySeries,
  fullWidth = true,
  defaultMode = "bar",
  percent = false,
}: {
  daily: CopilotDailyUsage[]
  title?: string
  series?: DailySeries[]
  fullWidth?: boolean
  defaultMode?: ChartMode
  percent?: boolean
}) {
  const [mode, setMode] = useState<ChartMode>(defaultMode)
  const { hiddenSeries, toggleSeries } = useSeriesToggle()
  const rows = dailyRows(daily)
  const availableSeries = series.filter((item) => rows.some((row) => row[item.key] > 0))
  return <section className={`finops-panel ${fullWidth ? "span-3" : ""} copilot-activity-panel`}>
    <div className="finops-panel-title copilot-activity-heading">
      <div className="copilot-activity-title">
        <h2>{title}</h2>
        <ChartModeToggle value={mode} onChange={setMode} />
      </div>
      <ChartSeriesLegend className="copilot-activity-legend" items={availableSeries} hiddenSeries={hiddenSeries} onToggle={toggleSeries} />
    </div>
    <ActivityChart daily={daily} mode={mode} hiddenSeries={hiddenSeries} series={availableSeries} percent={percent} />
  </section>
}

function SubscriptionPanel({ data }: { data: CopilotDashboard }) {
  const breakdown = data.subscription.seat_breakdown
  const rows = [
    ["总席位", breakdown.total ?? data.totals.seats],
    ["本周期活跃", breakdown.active_this_cycle ?? data.totals.active_users],
    ["本周期新增", breakdown.added_this_cycle ?? 0],
    ["待取消", breakdown.pending_cancellation ?? 0],
  ] as const
  return <section className="finops-panel span-3 copilot-subscription-panel">
    <div className="finops-panel-title"><h2>订阅概况</h2><span className="finops-panel-title-meta">{data.subscription.plan_type}</span></div>
    <dl className="copilot-fact-list">
      {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{compact.format(value)}</dd></div>)}
      <div><dt>席位单价</dt><dd>{currency.format(data.subscription.price_per_seat)} / 月</dd></div>
      <div><dt>席位月费估算</dt><dd>{currency.format(data.subscription.estimated_monthly_seat_cost)}</dd></div>
    </dl>
  </section>
}

function MemberTable({ members, personal }: { members: CopilotMemberUsage[]; personal: boolean }) {
  return <section className="finops-panel copilot-member-panel">
    <div className="finops-panel-title">
      <h2>{personal ? "我的 Copilot" : "组织成员"}</h2>
      <span className="finops-panel-title-meta">{`${members.length} 个席位`}</span>
    </div>
    {!members.length
      ? <WorkspaceState kind="empty" title="没有 Copilot 席位" />
      : <div className="copilot-member-table-scroll">
        <ResizableTable className="copilot-member-table" aria-label="GitHub Copilot 成员用量" minWidths={[180, 90, 90, 90, 90, 160]}>
          <thead><tr><th scope="col">成员</th><th scope="col">最近活动</th><th scope="col">交互</th><th scope="col">采纳率</th><th scope="col">AI Credits</th><th scope="col">预算</th></tr></thead>
          <tbody>{members.map((member) => <tr key={member.login}>
            <td><div className="copilot-member-identity">
              {member.avatar_url
                ? <img src={member.avatar_url} alt="" />
                : <span>{member.login.charAt(0).toUpperCase()}</span>}
              <span><b data-no-localize>{member.login}</b><small>{member.adoption_phase ?? member.plan_type}</small></span>
            </div></td>
            <td>{member.last_activity_at
              ? new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric" }).format(new Date(member.last_activity_at))
              : "--"}</td>
            <td>{compact.format(member.totals.interactions)}</td>
            <td>{decimal.format(member.totals.acceptance_rate)}%</td>
            <td>{decimal.format(member.totals.ai_credits_used)}</td>
            <td>{member.budget_amount == null
              ? "未设置"
              : `${currency.format(member.budget_consumed ?? 0)} / ${currency.format(member.budget_amount)}`}</td>
          </tr>)}</tbody>
        </ResizableTable>
      </div>}
  </section>
}

function BreakdownTable({
  title,
  dimensionLabel,
  items,
  controls,
}: {
  title: string
  dimensionLabel: string
  items: CopilotBreakdownItem[]
  controls?: React.ReactNode
}) {
  return <section className="finops-panel span-3 copilot-metrics-detail-panel">
    <div className="finops-panel-title copilot-detail-heading">
      <div className="copilot-detail-title"><h2>{title}</h2>{controls}</div>
      <span className="finops-panel-title-meta">{`${items.length} 条记录`}</span>
    </div>
    {!items.length
      ? <WorkspaceState kind="empty" title="暂无维度数据" />
      : <div className="copilot-matrix-table-scroll">
        <ResizableTable className="copilot-matrix-table" aria-label={title} minWidths={[86, 160, 80, 80, 80, 90, 80]}>
          <thead><tr><th scope="col">{dimensionLabel}</th><th scope="col">交互</th><th scope="col">生成</th><th scope="col">采纳</th><th scope="col">采纳率</th><th scope="col">新增行</th><th scope="col">删除行</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.key}>
            <td data-no-localize>{item.label}</td>
            <td>{compact.format(item.interactions)}</td>
            <td>{compact.format(item.generations)}</td>
            <td>{compact.format(item.acceptances)}</td>
            <td>{decimal.format(item.generations ? item.acceptances / item.generations * 100 : 0)}%</td>
            <td>{compact.format(item.loc_added)}</td>
            <td>{compact.format(item.loc_deleted ?? 0)}</td>
          </tr>)}</tbody>
        </ResizableTable>
      </div>}
  </section>
}

type BreakdownView = "features" | "models" | "languages" | "ides"

function BreakdownExplorer({ data }: { data: CopilotDashboard }) {
  const [view, setView] = useState<BreakdownView>("features")
  const options: Array<{
    value: BreakdownView
    label: string
    dimensionLabel: string
    items: CopilotBreakdownItem[]
  }> = [
    { value: "features", label: "功能", dimensionLabel: "功能", items: data.features },
    { value: "models", label: "模型", dimensionLabel: "模型", items: data.models },
    { value: "languages", label: "语言", dimensionLabel: "语言", items: data.languages },
    { value: "ides", label: "IDE", dimensionLabel: "IDE", items: data.ides },
  ]
  const selected = options.find((option) => option.value === view) ?? options[0]
  return <BreakdownTable
    title="使用结构明细"
    dimensionLabel={selected.dimensionLabel}
    items={selected.items}
    controls={<div className="trend-segment" role="group" aria-label="使用结构明细维度">
      {options.map((option) => <button className={view === option.value ? "active" : ""} type="button" onClick={() => setView(option.value)} key={option.value}>{option.label}</button>)}
    </div>}
  />
}

function AiCreditTable({ items }: { items: CopilotAiCreditItem[] }) {
  return <section className="finops-panel span-3 copilot-credit-panel">
    <div className="finops-panel-title"><h2>模型 AI Credit 明细</h2><span className="finops-panel-title-meta">{`${items.length} 条记录`}</span></div>
    {!items.length
      ? <WorkspaceState kind="empty" title="暂无模型 AI Credit 明细" />
      : <div className="copilot-matrix-table-scroll">
        <ResizableTable className="copilot-matrix-table copilot-credit-table" aria-label="GitHub Copilot 模型 AI Credit 明细" minWidths={[180, 100, 100, 100, 100]}>
          <thead><tr><th scope="col">模型</th><th scope="col">原始 AI Credits</th><th scope="col">折扣 AI Credits</th><th scope="col">净 AI Credits</th><th scope="col">净成本</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.model}>
            <td data-no-localize>{item.model}</td>
            <td>{decimal.format(item.gross_quantity)}</td>
            <td>{decimal.format(item.discount_quantity)}</td>
            <td>{decimal.format(item.net_quantity)}</td>
            <td>{currency.format(item.net_cost)}</td>
          </tr>)}</tbody>
        </ResizableTable>
      </div>}
  </section>
}

function MetricsGroup({
  title,
  columns,
  children,
}: {
  title?: string
  columns: 1 | 2 | 3
  children: React.ReactNode
}) {
  return <section className="copilot-metrics-group">
    {title && <div className="copilot-metrics-section"><h2>{title}</h2></div>}
    <div className={`copilot-metrics-group-grid columns-${columns}`}>{children}</div>
  </section>
}

function TopActiveUsersTable({ members, personal }: {
  members: CopilotMemberUsage[]
  personal: boolean
}) {
  const rows = [...members].sort(
    (left, right) => right.totals.interactions - left.totals.interactions,
  )
  return <section className="finops-panel span-3 copilot-matrix-member-panel">
    <div className="finops-panel-title"><h2>{personal ? "我的使用明细" : "活跃用户排行"}</h2><span className="finops-panel-title-meta">{`${rows.length} 个用户`}</span></div>
    {!rows.length
      ? <WorkspaceState kind="empty" title="没有成员用量" />
      : <div className="copilot-matrix-member-scroll">
        <ResizableTable className="copilot-matrix-member-table copilot-active-user-table" aria-label="GitHub Copilot 活跃用户排行" minWidths={[180, 80, 80, 80, 90, 80, 80, 90, 100, 100]}>
          <thead><tr><th scope="col">成员</th><th scope="col">交互</th><th scope="col">生成</th><th scope="col">采纳</th><th scope="col">采纳率</th><th scope="col">新增行</th><th scope="col">删除行</th><th scope="col">AI Credits</th><th scope="col">编辑器</th><th scope="col">最近活动</th></tr></thead>
          <tbody>{rows.map((member) => <tr key={member.login}>
            <td><div className="copilot-member-identity">
              {member.avatar_url
                ? <img src={member.avatar_url} alt="" />
                : <span>{member.login.charAt(0).toUpperCase()}</span>}
              <span><b data-no-localize>{member.login}</b><small>{member.adoption_phase ?? member.plan_type}</small></span>
            </div></td>
            <td>{compact.format(member.totals.interactions)}</td>
            <td>{compact.format(member.totals.generations)}</td>
            <td>{compact.format(member.totals.acceptances)}</td>
            <td>{decimal.format(member.totals.acceptance_rate)}%</td>
            <td>{compact.format(member.totals.loc_added)}</td>
            <td>{compact.format(member.totals.loc_deleted)}</td>
            <td>{decimal.format(member.totals.ai_credits_used)}</td>
            <td data-no-localize>{member.last_activity_editor ?? "--"}</td>
            <td>{member.last_activity_at
              ? new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric" }).format(new Date(member.last_activity_at))
              : "--"}</td>
          </tr>)}</tbody>
        </ResizableTable>
      </div>}
  </section>
}

function Overview({ data, daily, days }: { data: CopilotDashboard; daily: CopilotDailyUsage[]; days: number }) {
  const activityTotals = daily.reduce((totals, item) => ({
    interactions: totals.interactions + item.interactions,
    generations: totals.generations + item.generations,
    acceptances: totals.acceptances + item.acceptances,
    locAdded: totals.locAdded + item.loc_added,
    locDeleted: totals.locDeleted + item.loc_deleted,
  }), { interactions: 0, generations: 0, acceptances: 0, locAdded: 0, locDeleted: 0 })
  const acceptanceRate = activityTotals.generations ? activityTotals.acceptances / activityTotals.generations * 100 : 0
  const hasActualBilling = data.totals.gross_amount > 0 || data.totals.net_amount > 0
  const displayedCost = hasActualBilling
    ? data.totals.net_amount
    : data.subscription.estimated_monthly_seat_cost
  const costLabel = hasActualBilling ? "本月实际账单" : "席位月费估算"
  const costDetail = hasActualBilling
    ? `原价 ${currency.format(data.totals.gross_amount)} · AI Credits ${decimal.format(data.totals.ai_credits_used)}`
    : `AI Credits ${decimal.format(data.totals.ai_credits_used)}`
  const hasAudienceSeries = daily.some((item) => (
    (item.agent_users ?? 0)
    + (item.chat_users ?? 0)
    + (item.weekly_active_users ?? 0)
    + (item.monthly_active_users ?? 0)
  ) > 0)
  return <div className="copilot-overview">
    <section className="finops-kpis">
      <Kpi label="Copilot 席位" value={compact.format(data.totals.seats)} detail={`${data.totals.active_users} 个活跃用户`} icon={Users} />
      <Kpi label="用户交互" value={compact.format(activityTotals.interactions)} detail={`${days} 天 · ${compact.format(activityTotals.generations)} 次生成`} icon={Activity} />
      <Kpi label="采纳率" value={`${decimal.format(acceptanceRate)}%`} detail={`${days} 天 · ${compact.format(activityTotals.acceptances)} 次采纳`} icon={Zap} />
      <Kpi label="代码变更" value={compact.format(activityTotals.locAdded + activityTotals.locDeleted)} detail={`${days} 天 · ${compact.format(activityTotals.locAdded)} 行新增`} icon={Code2} />
      <Kpi label={costLabel} value={currency.format(displayedCost)} detail={costDetail} icon={CircleDollarSign} />
    </section>
    <MetricsGroup columns={1}>
      <ActivityPanel
        daily={daily}
        title={hasAudienceSeries ? "活跃用户趋势" : "活跃与交互趋势"}
        series={hasAudienceSeries ? activeUserSeries : engagementFallbackSeries}
        fullWidth={false}
        defaultMode="bar"
      />
    </MetricsGroup>
    <MetricsGroup title="采纳与生产力" columns={2}>
      <ActivityPanel daily={daily} title="代码行趋势" series={locSeries} fullWidth={false} defaultMode="line" />
      <ActivityPanel daily={daily} title="采纳率趋势" series={acceptanceRateSeries} fullWidth={false} defaultMode="line" percent />
    </MetricsGroup>
    <MetricsGroup title="使用结构" columns={2}>
      <BreakdownPanel title="语言分布" items={data.languages} metric="generations" />
      <BreakdownPanel title="IDE 分布" items={data.ides} metric="interactions" />
      <BreakdownExplorer data={data} />
      <AiCreditTable items={data.ai_credit_breakdown ?? []} />
    </MetricsGroup>
    <MetricsGroup title="人员与席位" columns={1}>
      <SubscriptionPanel data={data} />
      <TopActiveUsersTable members={data.members} personal={!data.can_view_members} />
    </MetricsGroup>
  </div>
}

type BreakdownMetric = "interactions" | "generations" | "acceptances" | "loc_added"

const breakdownMetrics: Array<{ value: BreakdownMetric; label: string }> = [
  { value: "interactions", label: "交互" },
  { value: "generations", label: "生成" },
  { value: "acceptances", label: "采纳" },
  { value: "loc_added", label: "新增行" },
]

function BreakdownPanel({ title, items, metric }: {
  title: string
  items: CopilotBreakdownItem[]
  metric: BreakdownMetric
}) {
  const data = items.slice(0, 10)
  const [chartRef, chartWidth] = useChartWidthKey()
  const axisWidth = categoryAxisWidth(data.map((item) => item.label), chartWidth)
  return <section className="finops-panel copilot-breakdown-panel">
    <div className="finops-panel-title"><h2>{title}</h2><span className="finops-panel-title-meta">Top {data.length}</span></div>
    {!data.length
      ? <WorkspaceState kind="empty" title="暂无维度数据" />
      : <div ref={chartRef} className="copilot-breakdown-chart">
        <ResponsiveContainer key={chartWidth} width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 8, right: 18, bottom: 8, left: 0 }}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" tickLine={false} axisLine={false} allowDecimals={false} />
            <YAxis type="category" dataKey="label" interval={0} tick={<CategoryAxisTick maxWidth={axisWidth - categoryTickGutter} />} tickLine={false} axisLine={false} tickMargin={8} width={axisWidth} />
            <Tooltip cursor={false} content={<ChartTooltip />} />
            <Bar dataKey={metric} name={breakdownMetrics.find((item) => item.value === metric)?.label} fill="var(--chart-1)" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>}
  </section>
}

function UsageMatrixMembers({ members, personal }: {
  members: CopilotMemberUsage[]
  personal: boolean
}) {
  return <section className="finops-panel span-3 copilot-matrix-member-panel">
    <div className="finops-panel-title">
      <h2>{personal ? "我的用量明细" : "成员用量明细"}</h2>
      <span className="finops-panel-title-meta">{members.length} 个用户</span>
    </div>
    {!members.length
      ? <WorkspaceState kind="empty" title="没有成员用量" />
      : <div className="copilot-matrix-member-scroll">
        <ResizableTable className="copilot-matrix-member-table" aria-label="GitHub Copilot 成员用量矩阵" minWidths={[180, 90, 100, 100, 80, 80, 80, 90, 100, 120]}>
          <thead><tr><th scope="col">成员</th><th scope="col">计划</th><th scope="col">编辑器</th><th scope="col">最近活动</th><th scope="col">交互</th><th scope="col">生成</th><th scope="col">采纳</th><th scope="col">采纳率</th><th scope="col">AI Credits</th><th scope="col">预算</th></tr></thead>
          <tbody>{members.map((member) => <tr key={member.login}>
            <td><div className="copilot-member-identity">
              {member.avatar_url
                ? <img src={member.avatar_url} alt="" />
                : <span>{member.login.charAt(0).toUpperCase()}</span>}
              <span><b data-no-localize>{member.login}</b><small>{member.adoption_phase ?? member.plan_type}</small></span>
            </div></td>
            <td>{member.plan_type}</td>
            <td data-no-localize>{member.last_activity_editor ?? "--"}</td>
            <td>{member.last_activity_at
              ? new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric" }).format(new Date(member.last_activity_at))
              : "--"}</td>
            <td>{compact.format(member.totals.interactions)}</td>
            <td>{compact.format(member.totals.generations)}</td>
            <td>{compact.format(member.totals.acceptances)}</td>
            <td>{decimal.format(member.totals.acceptance_rate)}%</td>
            <td>{decimal.format(member.totals.ai_credits_used)}</td>
            <td>{member.budget_amount == null
              ? "未设置"
              : `${currency.format(member.budget_consumed ?? 0)} / ${currency.format(member.budget_amount)}`}</td>
          </tr>)}</tbody>
        </ResizableTable>
      </div>}
  </section>
}

function Analytics({ data, daily, days }: {
  data: CopilotDashboard
  daily: CopilotDailyUsage[]
  days: number
}) {
  const [metric, setMetric] = useState<BreakdownMetric>("interactions")
  const totals = daily.reduce((result, item) => ({
    interactions: result.interactions + item.interactions,
    generations: result.generations + item.generations,
    acceptances: result.acceptances + item.acceptances,
  }), { interactions: 0, generations: 0, acceptances: 0 })
  const actualCost = data.totals.net_amount || data.totals.gross_amount
  return <div className="finops-grid copilot-grid">
    <section className="finops-kpis span-3 copilot-matrix-kpis">
      <Kpi label="AI Credits" value={decimal.format(data.totals.ai_credits_used)} detail={`${days} 天真实用量`} icon={Zap} />
      <Kpi label="实际账单" value={currency.format(actualCost)} detail={`原价 ${currency.format(data.totals.gross_amount)}`} icon={CircleDollarSign} />
      <Kpi label="活跃用户" value={compact.format(data.totals.active_users)} detail={`${data.members.length} 个成员有明细`} icon={Users} />
      <Kpi label="用户交互" value={compact.format(totals.interactions)} detail={`${compact.format(totals.generations)} 次生成`} icon={Activity} />
      <Kpi label="采纳率" value={`${decimal.format(totals.generations ? totals.acceptances / totals.generations * 100 : 0)}%`} detail={`${compact.format(totals.acceptances)} 次采纳`} icon={Code2} />
    </section>
    <ActivityPanel daily={daily} title="用量趋势" />
    <div className="copilot-dimension-toolbar span-3">
      <span>统计指标</span>
      <div className="trend-segment" role="group" aria-label="Copilot 分布指标">
        {breakdownMetrics.map((item) => <button className={metric === item.value ? "active" : ""} type="button" onClick={() => setMetric(item.value)} key={item.value}>{item.label}</button>)}
      </div>
    </div>
    <BreakdownPanel title="模型用量" items={data.models} metric={metric} />
    <BreakdownPanel title="功能用量" items={data.features} metric={metric} />
    <UsageMatrixMembers members={data.members} personal={!data.can_view_members} />
  </div>
}

function ImportedUsagePanel({ data }: { data: CopilotImportedUsage }) {
  if (!data.has_data) {
    return <section className="finops-panel span-3 copilot-import-panel">
      <div className="finops-panel-title"><h2>GitHub CSV 补充数据</h2><span className="finops-panel-title-meta">可选</span></div>
      <WorkspaceState kind="empty" title="尚未导入补充数据" detail="实时 GitHub API 数据仍是主数据源。" />
    </section>
  }
  return <ImportedUsageCharts data={data} />
}

type ImportedMetric = "quantity" | "net_amount" | "user_count"

const importedMetricLabels: Record<ImportedMetric, string> = {
  quantity: "用量",
  net_amount: "净金额",
  user_count: "用户",
}

function ImportedUsageCharts({ data }: { data: CopilotImportedUsage }) {
  const [metric, setMetric] = useState<ImportedMetric>(
    data.source_kind === "ai_usage" ? "quantity" : "net_amount",
  )
  return <div className="finops-grid copilot-grid copilot-import-grid" data-source-kind={data.source_kind}>
    <section className="finops-kpis span-3 copilot-import-kpis">
      <Kpi label="导入用量" value={decimal.format(data.total_quantity)} detail={`${data.unique_users} 个用户`} icon={Activity} />
      <Kpi label="原始金额" value={currency.format(data.total_gross_amount)} detail={`${data.unique_organizations} 个组织`} icon={CircleDollarSign} />
      <Kpi label="净金额" value={currency.format(data.total_net_amount)} detail={`${data.first_usage_date} - ${data.last_usage_date}`} icon={CircleDollarSign} />
      <Kpi label="成本中心" value={String(data.cost_centers.length)} detail={`${data.primary_breakdown.length} 个主要维度`} icon={Users} />
    </section>
    <ImportedTrendPanel data={data} />
    <div className="copilot-dimension-toolbar span-3">
      <span>分布指标</span>
      <div className="trend-segment" role="group" aria-label="CSV 分布指标">
        {(Object.keys(importedMetricLabels) as ImportedMetric[]).map((value) => <button className={metric === value ? "active" : ""} type="button" onClick={() => setMetric(value)} key={value}>{importedMetricLabels[value]}</button>)}
      </div>
    </div>
    {data.source_kind === "usage_report" && <ImportedBreakdownChart title="CSV 产品分布" items={data.product_breakdown ?? []} metric={metric} />}
    <ImportedBreakdownChart title={data.source_kind === "ai_usage" ? "CSV 模型分布" : "CSV SKU 分布"} items={data.primary_breakdown} metric={metric} />
    <ImportedBreakdownChart title="CSV 组织分布" items={data.organization_breakdown} metric={metric} />
    <ImportedBreakdownChart title="CSV 成本中心分布" items={data.cost_center_breakdown} metric={metric} />
    <section className="finops-panel span-3 copilot-import-user-panel">
      <div className="finops-panel-title"><h2>CSV 用户明细</h2><span className="finops-panel-title-meta">{`${data.users.length} 个用户`}</span></div>
      <ResizableGridTable className="copilot-import-user-table" role="table" aria-label="GitHub CSV 用户用量" headerSelector=".copilot-import-user-head" minWidths={[150, 130, 130, 90, 110, 110, 100, 100, 80]} columnGap={12} horizontalPadding={32}>
        <div className="copilot-import-user-head" role="row"><span>用户</span><span>组织</span><span>成本中心</span><span>用量</span><span>原始金额</span><span>净金额</span><span>月度配额</span><span>配额使用率</span><span>活跃天数</span></div>
        {data.users.map((user) => <div className="copilot-import-user-row" role="row" key={user.login}>
          <b data-no-localize>{user.login}</b>
          <span data-no-localize>{user.organization}</span>
          <span data-no-localize>{user.cost_center_name ?? "--"}</span>
          <span>{decimal.format(user.quantity)}</span>
          <span>{currency.format(user.gross_amount)}</span>
          <span>{currency.format(user.net_amount)}</span>
          <span>{user.monthly_quota == null ? "--" : decimal.format(user.monthly_quota)}</span>
          <span>{user.usage_percent == null ? "--" : `${decimal.format(user.usage_percent)}%`}</span>
          <span>{user.active_days}</span>
        </div>)}
      </ResizableGridTable>
    </section>
  </div>
}

type ImportedTrendRow = {
  date: string
  label: string
  quantity: number
  gross_amount: number
  net_amount: number
  discount_amount: number
  active_users: number
}

function ImportedTrendPanel({ data }: { data: CopilotImportedUsage }) {
  const rows: ImportedTrendRow[] = data.daily.map((item) => ({
    ...item,
    date: item.day,
    label: new Intl.DateTimeFormat(getIntlLocale(), { month: "2-digit", day: "2-digit" })
      .format(new Date(`${item.day}T00:00:00Z`)),
    discount_amount: Math.max(item.gross_amount - item.net_amount, 0),
  }))
  const series = data.source_kind === "ai_usage"
    ? [
        { key: "quantity", label: "AI Credits", color: "var(--chart-1)" },
        { key: "active_users", label: "活跃用户", color: "var(--chart-4)" },
      ] as const
    : [
        { key: "gross_amount", label: "原始金额", color: "var(--chart-1)" },
        { key: "net_amount", label: "净金额", color: "var(--success)" },
        { key: "discount_amount", label: "折扣金额", color: "var(--chart-4)" },
      ] as const
  const { hiddenSeries, toggleSeries } = useSeriesToggle()
  const visibleSeries = series.filter((item) => !hiddenSeries.includes(item.key))
  const {
    finishSelection,
    isZoomed,
    moveSelection,
    resetZoom,
    selectionArea,
    startSelection,
    zoomedRows,
  } = useChartDragZoom(rows)
  const labels = new Map(zoomedRows.map((row) => [row.date, row.label]))
  const formatDate = (value: string) => labels.get(value) ?? value
  return <section className="finops-panel span-3 copilot-import-trend-panel">
    <div className="finops-panel-title copilot-activity-heading">
      <h2>CSV 每日趋势</h2>
      <ChartSeriesLegend className="copilot-activity-legend" items={[...series]} hiddenSeries={hiddenSeries} onToggle={toggleSeries} />
    </div>
    {!rows.length
      ? <WorkspaceState kind="empty" title="暂无导入趋势数据" />
      : <div className="copilot-import-trend-chart usage-daily-chart" data-zoomed={isZoomed || undefined}>
        {isZoomed && <ChartZoomReset onClick={resetZoom} />}
        <ResponsiveContainer width="100%" height="100%">
          <RechartsLineChart data={zoomedRows} margin={{ top: 8, right: 12, bottom: 8, left: 0 }} onMouseDown={startSelection} onMouseMove={moveSelection} onMouseUp={finishSelection} onMouseLeave={() => finishSelection()}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="date" tickFormatter={formatDate} tickLine={false} axisLine={false} interval="preserveStartEnd" />
            <YAxis tickLine={false} axisLine={false} tickMargin={8} width={56} />
            <Tooltip cursor={false} content={<ChartTooltip labelFormatter={formatDate} />} />
            {selectionArea}
            {visibleSeries.map((item) => <Line key={item.key} type="linear" dataKey={item.key} name={item.label} stroke={item.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />)}
          </RechartsLineChart>
        </ResponsiveContainer>
      </div>}
  </section>
}

function ImportedBreakdownChart({ title, items, metric }: {
  title: string
  items: CopilotImportedUsage["primary_breakdown"]
  metric: ImportedMetric
}) {
  const rows = items.slice(0, 10)
  const axisWidth = Math.min(
    120,
    Math.max(56, Math.max(...rows.map((item) => Array.from(item.key).length), 0) * 6 + 14),
  )
  return <section className="finops-panel copilot-import-breakdown-panel">
    <div className="finops-panel-title"><h2>{title}</h2><span className="finops-panel-title-meta">Top {rows.length}</span></div>
    {!rows.length
      ? <WorkspaceState kind="empty" title="暂无导入维度数据" />
      : <div className="copilot-import-breakdown-chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 18, bottom: 8, left: 0 }}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={(value) => metric === "net_amount" ? currency.format(Number(value)) : compact.format(Number(value))} />
            <YAxis type="category" dataKey="key" tickLine={false} axisLine={false} tickMargin={8} width={axisWidth} />
            <Tooltip cursor={false} content={<ChartTooltip />} />
            <Bar dataKey={metric} name={importedMetricLabels[metric]} fill="var(--chart-1)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>}
  </section>
}

type TrendMetric = keyof Pick<CopilotDailyUsage, "active_users" | "interactions" | "generations" | "acceptances" | "loc_added" | "ai_credits_used">

const trendMetrics: Array<{ value: TrendMetric; label: string; color: string }> = [
  { value: "active_users", label: "活跃用户", color: "var(--chart-1)" },
  { value: "interactions", label: "交互", color: "var(--chart-2)" },
  { value: "generations", label: "生成", color: "var(--chart-3)" },
  { value: "acceptances", label: "采纳", color: "var(--chart-4)" },
  { value: "loc_added", label: "新增行", color: "var(--chart-5)" },
  { value: "ai_credits_used", label: "AI Credits", color: "var(--chart-2)" },
]

function Trends({ data, daily }: { data: CopilotDashboard; daily: CopilotDailyUsage[] }) {
  const [metric, setMetric] = useState<TrendMetric>("interactions")
  const selected = trendMetrics.find((item) => item.value === metric) ?? trendMetrics[0]
  const chartData = daily.map((item) => ({
    ...item,
    date: item.day,
    label: new Intl.DateTimeFormat(getIntlLocale(), { month: "2-digit", day: "2-digit" })
      .format(new Date(`${item.day}T00:00:00Z`)),
  }))
  const {
    finishSelection,
    isZoomed,
    moveSelection,
    resetZoom,
    selectionArea,
    startSelection,
    zoomedRows,
  } = useChartDragZoom(chartData)
  const labelByDate = new Map(zoomedRows.map((row) => [row.date, row.label]))
  const formatDateLabel = (value: string) => labelByDate.get(value) ?? value
  return <div className="finops-grid copilot-grid">
    <div className="copilot-dimension-toolbar span-3">
      <span>趋势指标</span>
      <div className="trend-segment" role="group" aria-label="Copilot 趋势指标">
        {trendMetrics.map((item) => <button className={metric === item.value ? "active" : ""} type="button" onClick={() => setMetric(item.value)} key={item.value}>{item.label}</button>)}
      </div>
    </div>
    <section className="finops-panel span-3 copilot-trend-panel">
      <div className="finops-panel-title"><h2>{`${selected.label}趋势`}</h2></div>
      {!chartData.length
        ? <WorkspaceState kind="empty" title="暂无趋势数据" />
        : <div className="copilot-trend-chart usage-daily-chart" data-zoomed={isZoomed || undefined}>
          {isZoomed && <ChartZoomReset onClick={resetZoom} />}
          <ResponsiveContainer width="100%" height="100%">
            <RechartsLineChart data={zoomedRows} margin={{ top: 16, right: 20, bottom: 8, left: 0 }} onMouseDown={startSelection} onMouseMove={moveSelection} onMouseUp={finishSelection} onMouseLeave={() => finishSelection()}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="date" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis tickLine={false} axisLine={false} tickMargin={6} width={44} allowDecimals={metric === "ai_credits_used"} />
              <Tooltip cursor={false} content={<ChartTooltip labelFormatter={formatDateLabel} />} />
              {selectionArea}
              <Line type="monotone" dataKey={metric} name={selected.label} stroke={selected.color} strokeWidth={2.5} dot={{ r: 2 }} activeDot={{ r: 4 }} />
            </RechartsLineChart>
          </ResponsiveContainer>
        </div>}
    </section>
    <section className="finops-panel span-3 copilot-report-table-panel">
      <div className="finops-panel-title"><h2>逐日报告</h2><span className="finops-panel-title-meta">{daily.length} 天</span></div>
      {!daily.length
        ? <WorkspaceState kind="empty" title="暂无逐日报告" />
        : <ResizableGridTable className="copilot-daily-report-table" role="table" aria-label="GitHub Copilot 逐日报告" headerSelector=".copilot-daily-report-head" minWidths={[120, 82, 82, 82, 82, 82, 82, 82]} columnGap={12} horizontalPadding={32}>
          <div className="copilot-daily-report-head" role="row"><span>日期</span><span>活跃用户</span><span>交互</span><span>生成</span><span>采纳</span><span>新增行</span><span>删除行</span><span>AI Credits</span></div>
          {daily.map((item) => <div className="copilot-daily-report-row" role="row" key={item.day}>
            <time>{item.day}</time>
            <span>{compact.format(item.active_users)}</span>
            <span>{compact.format(item.interactions)}</span>
            <span>{compact.format(item.generations)}</span>
            <span>{compact.format(item.acceptances)}</span>
            <span>{compact.format(item.loc_added)}</span>
            <span>{compact.format(item.loc_deleted)}</span>
            <span>{decimal.format(item.ai_credits_used)}</span>
          </div>)}
        </ResizableGridTable>}
    </section>
    <MemberTable members={data.members} personal={!data.can_view_members} />
  </div>
}

export function CopilotDashboardPage({
  initialTab,
  onToggleSidebar,
}: {
  initialTab: CopilotAnalyticsTab
  onToggleSidebar: () => void
}) {
  const queryClient = useQueryClient()
  const status = useQuery(copilotQueries.status())
  const [days, setDays] = useState(28)
  const [organization, setOrganization] = useState<string | undefined>()
  const defaultOrganization = status.data?.connections.find((connection) => connection.is_default)?.organization
    ?? status.data?.connections[0]?.organization
  const selectedOrganization = organization ?? defaultOrganization
  const canRead = status.data?.configured === true
    && (status.data.viewer_role === "owner" || Boolean(status.data.viewer_github_login))
  const dashboard = useQuery(copilotQueries.dashboard(selectedOrganization, canRead))
  const importKind: CopilotUsageImportKind = initialTab === "trends" ? "usage_report" : "ai_usage"
  const canReadImports = canRead && status.data?.viewer_role === "owner" && initialTab !== "overview"
  const importedUsage = useQuery(copilotQueries.importedUsage(importKind, selectedOrganization, canReadImports))
  const fileInput = useRef<HTMLInputElement>(null)
  const upload = useMutation({
    mutationFn: (file: File) => copilotApi.importUsage(file, selectedOrganization),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: copilotKeys.importedUsage(importKind, selectedOrganization ?? null) })
      void queryClient.invalidateQueries({ queryKey: copilotKeys.usageImports(importKind, selectedOrganization ?? null) })
    },
  })
  const HeaderIcon = headers[initialTab].icon
  const refresh = () => queryClient.invalidateQueries({ queryKey: copilotKeys.all })
  const filteredDaily = useMemo(() => {
    const daily = dashboard.data?.daily ?? []
    const reportEnd = dashboard.data?.report_end_day ?? daily.at(-1)?.day
    if (!reportEnd) return daily
    const cutoff = new Date(`${reportEnd}T00:00:00Z`)
    cutoff.setUTCDate(cutoff.getUTCDate() - days + 1)
    const cutoffDay = cutoff.toISOString().slice(0, 10)
    return daily.filter((item) => item.day >= cutoffDay && item.day <= reportEnd)
  }, [dashboard.data, days])
  const activeFilterCount = (days === 28 ? 0 : 1) + (organization ? 1 : 0)
  return <div className="finops-workspace copilot-workspace">
    <header className="finops-header">
      <div>
        <Button variant="ghost" size="icon-sm" className="finops-sidebar-trigger" aria-label="切换导航栏" title="切换导航栏" onClick={onToggleSidebar}><PanelLeft size={16} /></Button>
        <span className="finops-header-icon copilot-header-icon"><HeaderIcon size={17} /></span>
        <h1>{headers[initialTab].title}</h1>
      </div>
      <div className="copilot-header-actions">
        {canReadImports && <>
          <input ref={fileInput} className="copilot-file-input" type="file" accept=".csv,text/csv" onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) upload.mutate(file)
            event.target.value = ""
          }} />
          <Button className="copilot-import-action" variant="outline" size="sm" disabled={upload.isPending} onClick={() => fileInput.current?.click()}><Upload size={14} />{upload.isPending ? "正在导入" : "导入 CSV"}</Button>
        </>}
        <Button variant="ghost" size="icon-sm" className="finops-header-refresh" aria-label="刷新 GitHub Copilot 数据" title="刷新 GitHub Copilot 数据" disabled={status.isFetching || dashboard.isFetching} onClick={() => void refresh()}><RefreshCw className={status.isFetching || dashboard.isFetching ? "spin" : undefined} size={15} /></Button>
      </div>
    </header>
    <div className="finops-filterbar copilot-filterbar">
      <FilterMenuField label="时间范围" icon={CalendarRange} value={String(days)} active={days !== 28} allowAll={false} options={[{ value: "7", label: "近 7 天" }, { value: "14", label: "近 14 天" }, { value: "28", label: "近 28 天" }]} onChange={(next) => next && setDays(Number(next))} />
      {(status.data?.connections.length ?? 0) > 0 && <FilterMenuField label="GitHub 组织" icon={Building2} value={selectedOrganization} active={Boolean(organization)} allowAll={false} options={status.data?.connections.map((connection) => ({ value: connection.organization, label: connection.display_name })) ?? []} onChange={(next) => next && setOrganization(next)} />}
      {activeFilterCount > 0 && <Button variant="ghost" size="sm" className="finops-clear" onClick={() => { setDays(28); setOrganization(undefined) }}>重置</Button>}
    </div>
    <div className="finops-scroll-region">
      <div className="finops-content">
        {status.isLoading && <WorkspaceState kind="loading" title="正在读取 GitHub Copilot 配置" />}
        {status.error && <WorkspaceState kind="error" title="GitHub Copilot 状态不可用" detail={queryError(status.error)} />}
        {status.data && !status.data.configured && <WorkspaceState
          kind="empty"
          title={status.data.viewer_github_login ? "GitHub 账号已连接，组织数据尚未连接" : "尚未连接 GitHub Copilot 组织数据"}
          detail={status.data.viewer_role === "owner" ? "身份关联只确认你是谁；Copilot 席位、用量和预算仍需连接组织数据。" : "你的 GitHub 身份已关联，但 Owner 尚未连接组织 Copilot 数据。"}
          action={status.data.viewer_role === "owner" ? <Button className="copilot-configure-action" onClick={() => openPage("settings")}><Settings size={14} />连接组织数据</Button> : undefined}
        />}
        {status.data?.configured && status.data.viewer_role === "member" && !status.data.viewer_github_login && <WorkspaceState
          kind="empty"
          title="GitHub 身份尚未关联"
          detail={status.data.oauth_configured ? "连接当前 GitHub 账号后即可读取你的 Copilot 数据。" : "Owner 尚未配置 GitHub 账号连接。"}
          action={status.data.oauth_configured ? <CopilotConnectButton /> : undefined}
        />}
        {canRead && dashboard.isLoading && <WorkspaceState kind="loading" title="正在读取 GitHub Copilot 用量" />}
        {canRead && dashboard.error && <WorkspaceState kind="error" title="GitHub Copilot 数据不可用" detail={queryError(dashboard.error)} />}
        {Boolean(dashboard.data?.warnings.length) && <div className="copilot-warning" role="status">
          <AlertTriangle size={14} />
          <span>详细用量与账单暂不可用；当前席位和最近活动来自 GitHub 实时数据。</span>
        </div>}
        {upload.isSuccess && <p className="copilot-form-success copilot-import-notice">{`已导入 ${upload.data.inserted_count} 行，跳过 ${upload.data.duplicate_count} 行重复数据。`}</p>}
        {upload.error && <p className="copilot-form-error copilot-import-notice"><AlertTriangle size={14} />{queryError(upload.error)}</p>}
        {dashboard.data && initialTab === "overview" && <Overview data={dashboard.data} daily={filteredDaily} days={days} />}
        {dashboard.data && initialTab === "analytics" && <div className="copilot-combined-view"><Analytics data={dashboard.data} daily={filteredDaily} days={days} />{importedUsage.data && <ImportedUsagePanel data={importedUsage.data} />}</div>}
        {dashboard.data && initialTab === "trends" && <div className="copilot-combined-view"><Trends data={dashboard.data} daily={filteredDaily} />{importedUsage.data && <ImportedUsagePanel data={importedUsage.data} />}</div>}
      </div>
    </div>
  </div>
}