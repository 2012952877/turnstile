import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  AlertTriangle,
  Building2,
  CircleDollarSign,
  PanelLeft,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  UserRoundX,
  UsersRound,
  WalletCards,
} from "lucide-react"

import { copilotApi } from "../api"
import { copilotKeys, copilotQueries } from "../queries"
import type {
  CopilotBudgetSummary,
  CopilotCostCenterSummary,
  CopilotDashboard,
  CopilotGovernance,
  CopilotGovernanceMember,
  CopilotTeamSummary,
} from "../types"
import { CopilotLogo } from "../../../components/brand-logos"
import { FinOpsChartTooltip } from "../../../components/finops/chart-tooltip"
import { Button } from "../../../components/ui/button"
import { Input } from "../../../components/ui/input"
import { Textarea } from "../../../components/ui/textarea"
import { ResizableGridTable } from "../../../components/ui/resizable-table"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select"
import { getIntlLocale } from "../../../locales"

export type CopilotGovernanceView = "teams" | "cost-centers" | "unassigned" | "budgets"

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
})
const decimal = new Intl.NumberFormat(getIntlLocale(), { maximumFractionDigits: 1 })

const headers = {
  teams: { title: "企业团队", icon: UsersRound },
  "cost-centers": { title: "成本中心", icon: Building2 },
  unassigned: { title: "未分配用户", icon: UserRoundX },
  budgets: { title: "预算", icon: WalletCards },
} as const

function queryError(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function GovernanceState({
  kind,
  title,
  detail,
}: {
  kind: "loading" | "error" | "empty"
  title: string
  detail?: string
}) {
  return <div className={`copilot-workspace-state ${kind}`}>
    {kind === "loading"
      ? <RefreshCw className="spin" size={20} />
      : kind === "error"
        ? <AlertTriangle size={20} />
        : <CopilotLogo size={24} />}
    <b>{title}</b>
    {detail && <span>{detail}</span>}
  </div>
}

function Kpi({ label, value, detail, icon: Icon }: {
  label: string
  value: string
  detail: string
  icon: typeof UsersRound
}) {
  return <div>
    <span className="finops-kpi-icon"><Icon size={14} /></span>
    <label>{label}</label>
    <strong>{value}</strong>
    <small>{detail}</small>
  </div>
}

function MemberIdentity({ member }: { member: CopilotGovernanceMember }) {
  return <div className="copilot-member-identity">
    {member.avatar_url
      ? <img src={member.avatar_url} alt="" />
      : <span>{member.login.charAt(0).toUpperCase()}</span>}
    <span>
      <b data-no-localize>{member.login}</b>
      <small>{member.has_seat ? member.plan_type ?? "Copilot" : "无 Copilot 席位"}</small>
    </span>
  </div>
}

function SearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <label className="copilot-governance-search">
    <Search size={14} />
    <Input value={value} aria-label="搜索" placeholder="搜索用户或名称" onChange={(event) => onChange(event.target.value)} />
  </label>
}

function TeamsView({ data, dashboard, search }: {
  data: CopilotGovernance
  dashboard?: CopilotDashboard
  search: string
}) {
  const needle = search.trim().toLowerCase()
  const teams = data.teams.filter((team) => !needle
    || team.name.toLowerCase().includes(needle)
    || team.slug.toLowerCase().includes(needle)
    || team.members.some((member) => member.login.toLowerCase().includes(needle)))
  const coveredSeatLogins = new Set(
    data.teams.flatMap((team) => team.members.filter((member) => member.has_seat).map((member) => member.login.toLowerCase())),
  )
  const uniqueMembers = new Set(data.teams.flatMap((team) => team.members.map((member) => member.login.toLowerCase())))
  const outsideTeams = data.seats.filter((member) => !uniqueMembers.has(member.login.toLowerCase()))
  const usageByLogin = new Map(
    dashboard?.members.map((member) => [member.login.toLowerCase(), member]) ?? [],
  )
  const coverage = data.seats.length ? coveredSeatLogins.size / data.seats.length * 100 : 0
  return <div className="finops-grid copilot-governance-grid">
    <section className="finops-kpis span-3">
      <Kpi label="企业团队" value={String(data.teams.length)} detail={`${teams.length} 个当前结果`} icon={UsersRound} />
      <Kpi label="团队成员" value={String(uniqueMembers.size)} detail={`${coveredSeatLogins.size} 个持有席位`} icon={UsersRound} />
      <Kpi label="席位覆盖率" value={`${decimal.format(coverage)}%`} detail={`${data.seats.length} 个 Copilot 席位`} icon={ShieldCheck} />
      <Kpi label="未覆盖席位" value={String(Math.max(0, data.seats.length - coveredSeatLogins.size))} detail="未加入企业团队" icon={UserRoundX} />
    </section>
    <section className="finops-panel span-3 copilot-governance-panel">
      <div className="finops-panel-title"><h2>企业团队</h2><span className="finops-panel-title-meta">{`${teams.length} 个团队`}</span></div>
      {!teams.length
        ? <GovernanceState kind="empty" title="没有符合条件的企业团队" />
        : <ResizableGridTable className="copilot-governance-list copilot-team-list" role="table" aria-label="GitHub Enterprise 团队" headerSelector=".copilot-team-head" minWidths={[180, 80, 64, 64, 64, 64, 80, 90, 100]} columnGap={12} horizontalPadding={32}>
          <div className="copilot-team-head" role="row"><span>团队</span><span>组织</span><span>成员</span><span>席位</span><span>无席位</span><span>活跃</span><span>交互</span><span>AI Credits</span><span>席位成本 / 月</span></div>
          {teams.map((team) => <TeamRow team={team} usageByLogin={usageByLogin} seatPrice={dashboard?.subscription.price_per_seat} key={team.slug} />)}
        </ResizableGridTable>}
    </section>
    <section className="finops-panel span-3 copilot-governance-panel">
      <div className="finops-panel-title"><h2>团队外席位</h2><span className="finops-panel-title-meta">{`${outsideTeams.length} 个用户`}</span></div>
      {!outsideTeams.length
        ? <GovernanceState kind="empty" title="所有席位都已加入企业团队" />
        : <GovernanceMemberTable members={outsideTeams} label="团队外 Copilot 席位" />}
    </section>
  </div>
}

function TeamRow({ team, usageByLogin, seatPrice }: {
  team: CopilotTeamSummary
  usageByLogin: Map<string, CopilotDashboard["members"][number]>
  seatPrice?: number
}) {
  const usage = team.members.flatMap((member) => {
    const row = usageByLogin.get(member.login.toLowerCase())
    return row ? [row] : []
  })
  const interactions = usage.reduce((sum, member) => sum + member.totals.interactions, 0)
  const credits = usage.reduce((sum, member) => sum + member.totals.ai_credits_used, 0)
  const active = usage.filter((member) => member.totals.interactions + member.totals.generations > 0).length
  return <details className="copilot-governance-detail copilot-team-detail">
    <summary>
      <span><b data-no-localize>{team.name}</b><small data-no-localize>{team.slug}</small></span>
      <span>{`${team.organizations.length} 个组织`}</span>
      <span>{team.member_count}</span>
      <span>{team.seat_count}</span>
      <span>{Math.max(0, team.member_count - team.seat_count)}</span>
      <span>{dashboardValue(active, usageByLogin.size > 0)}</span>
      <span>{dashboardValue(interactions, usageByLogin.size > 0)}</span>
      <span>{usageByLogin.size > 0 ? decimal.format(credits) : "--"}</span>
      <span>{seatPrice == null ? "--" : currency.format(team.seat_count * seatPrice)}</span>
    </summary>
    <div className="copilot-governance-detail-body">
      {team.description && <p>{team.description}</p>}
      {team.organizations.length > 0 && <div className="copilot-governance-tags">{team.organizations.map((organization) => <span data-no-localize key={organization}>{organization}</span>)}</div>}
      <div className="copilot-governance-members">
        {team.members.map((member) => <MemberIdentity member={member} key={member.login} />)}
      </div>
    </div>
  </details>
}

function dashboardValue(value: number, available: boolean) {
  return available ? decimal.format(value) : "--"
}

function CostCentersView({ data, search }: { data: CopilotGovernance; search: string }) {
  const [state, setState] = useState<"all" | "active" | "archived">("active")
  const needle = search.trim().toLowerCase()
  const centers = data.cost_centers.filter((center) => (state === "all" || center.state === state)
    && (!needle
      || center.name.toLowerCase().includes(needle)
      || center.members.some((member) => member.login.toLowerCase().includes(needle))))
  const active = data.cost_centers.filter((center) => center.state === "active")
  const members = new Set(active.flatMap((center) => center.members.map((member) => member.login.toLowerCase())))
  return <div className="finops-grid copilot-governance-grid">
    <div className="copilot-dimension-toolbar span-3">
      <span>状态</span>
      <div className="trend-segment" role="group" aria-label="成本中心状态">
        {(["active", "archived", "all"] as const).map((value) => <button type="button" className={state === value ? "active" : ""} onClick={() => setState(value)} key={value}>{value === "active" ? "启用" : value === "archived" ? "已归档" : "全部"}</button>)}
      </div>
    </div>
    <section className="finops-kpis span-3">
      <Kpi label="启用成本中心" value={String(active.length)} detail={`${data.cost_centers.length - active.length} 个已归档`} icon={Building2} />
      <Kpi label="已分配成员" value={String(members.size)} detail={`${data.seats.length} 个 Copilot 席位`} icon={UsersRound} />
      <Kpi label="未分配用户" value={String(data.unassigned_seats.length)} detail="不属于启用成本中心" icon={UserRoundX} />
      <Kpi label="资源映射" value={String(active.reduce((sum, center) => sum + center.resources.length, 0))} detail="用户、组织与团队" icon={ShieldCheck} />
    </section>
    <section className="finops-panel span-3 copilot-governance-panel">
      <div className="finops-panel-title"><h2>成本中心</h2><span className="finops-panel-title-meta">{`${centers.length} 个结果`}</span></div>
      {!centers.length
        ? <GovernanceState kind="empty" title="没有符合条件的成本中心" />
        : <div className="copilot-governance-list">
          {centers.map((center) => <CostCenterRow center={center} key={center.id} />)}
        </div>}
    </section>
    <CostCenterUserMapping centers={active} />
  </div>
}

function CostCenterUserMapping({ centers }: { centers: CopilotCostCenterSummary[] }) {
  const mappings = new Map<string, {
    member: CopilotGovernanceMember
    centers: string[]
    sources: string[]
  }>()
  for (const center of centers) {
    const sources = [...new Set(center.resources.map((resource) => resource.type))]
    for (const member of center.members) {
      const key = member.login.toLowerCase()
      const current = mappings.get(key) ?? { member, centers: [], sources: [] }
      current.centers.push(center.name)
      current.sources.push(...sources)
      mappings.set(key, current)
    }
  }
  const rows = [...mappings.values()].sort((left, right) => left.member.login.localeCompare(right.member.login))
  return <section className="finops-panel span-3 copilot-governance-panel">
    <div className="finops-panel-title"><h2>用户与成本中心映射</h2><span className="finops-panel-title-meta">{`${rows.length} 个用户`}</span></div>
    {!rows.length
      ? <GovernanceState kind="empty" title="暂无用户成本中心映射" />
      : <ResizableGridTable className="copilot-governance-table copilot-cost-center-map" role="table" aria-label="GitHub 用户成本中心映射" headerSelector=".copilot-governance-table-head" minWidths={[180, 180, 140]} columnGap={12} horizontalPadding={32}>
        <div className="copilot-governance-table-head" role="row"><span>用户</span><span>成本中心</span><span>来源</span></div>
        {rows.map((row) => <div className="copilot-governance-table-row" role="row" key={row.member.login}>
          <MemberIdentity member={row.member} />
          <span data-no-localize>{[...new Set(row.centers)].join(", ")}</span>
          <span>{[...new Set(row.sources)].join(", ") || "GitHub 资源"}</span>
        </div>)}
      </ResizableGridTable>}
  </section>
}

function GovernanceMemberTable({ members, label }: {
  members: CopilotGovernanceMember[]
  label: string
}) {
  return <ResizableGridTable className="copilot-governance-table copilot-governance-member-table" role="table" aria-label={label} headerSelector=".copilot-governance-table-head" minWidths={[180, 100, 120, 160]} columnGap={12} horizontalPadding={32}>
    <div className="copilot-governance-table-head" role="row"><span>用户</span><span>计划</span><span>最近活动</span><span>编辑器</span></div>
    {members.map((member) => <div className="copilot-governance-table-row" role="row" key={member.login}>
      <MemberIdentity member={member} />
      <span>{member.plan_type ?? "--"}</span>
      <span>{member.last_activity_at ? new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric", year: "numeric" }).format(new Date(member.last_activity_at)) : "--"}</span>
      <span data-no-localize>{member.last_activity_editor ?? "--"}</span>
    </div>)}
  </ResizableGridTable>
}

function CostCenterRow({ center }: { center: CopilotCostCenterSummary }) {
  return <details className="copilot-governance-detail">
    <summary>
      <span><b data-no-localize>{center.name}</b><small>{center.state === "active" ? "启用" : "已归档"}</small></span>
      <span>{`${center.member_count} 个成员`}</span>
      <span>{`${center.resources.length} 个资源`}</span>
      <span data-no-localize>{center.id}</span>
    </summary>
    <div className="copilot-governance-detail-body">
      <div className="copilot-governance-tags">{center.resources.map((resource, index) => <span data-no-localize key={`${resource.type}-${resource.name}-${index}`}>{resource.type}: {resource.name}</span>)}</div>
      <div className="copilot-governance-members">
        {center.members.map((member) => <MemberIdentity member={member} key={member.login} />)}
      </div>
    </div>
  </details>
}

function UnassignedView({ data, search }: { data: CopilotGovernance; search: string }) {
  const needle = search.trim().toLowerCase()
  const users = data.unassigned_seats.filter((member) => !needle || member.login.toLowerCase().includes(needle))
  const assigned = data.seats.length - data.unassigned_seats.length
  const coverage = data.seats.length ? assigned / data.seats.length * 100 : 0
  return <div className="finops-grid copilot-governance-grid">
    <section className="finops-kpis span-3">
      <Kpi label="Copilot 用户" value={String(data.seats.length)} detail="Enterprise 席位用户" icon={UsersRound} />
      <Kpi label="已分配" value={String(assigned)} detail="属于启用成本中心" icon={ShieldCheck} />
      <Kpi label="未分配" value={String(data.unassigned_seats.length)} detail={`${users.length} 个当前结果`} icon={UserRoundX} />
      <Kpi label="成本中心覆盖率" value={`${decimal.format(coverage)}%`} detail="按 GitHub 资源展开计算" icon={Building2} />
    </section>
    <section className="finops-panel span-3 copilot-governance-panel">
      <div className="finops-panel-title"><h2>未分配用户</h2><span className="finops-panel-title-meta">{`${users.length} 个用户`}</span></div>
      {!users.length
        ? <GovernanceState kind="empty" title="没有未分配用户" />
        : <ResizableGridTable className="copilot-governance-table" role="table" aria-label="未分配 Copilot 用户" headerSelector=".copilot-governance-table-head" minWidths={[180, 100, 120, 100, 90]} columnGap={12} horizontalPadding={32}>
          <div className="copilot-governance-table-head" role="row"><span>用户</span><span>计划</span><span>最近活动</span><span>编辑器</span><span>状态</span></div>
          {users.map((member) => <div className="copilot-governance-table-row" role="row" key={member.login}>
            <MemberIdentity member={member} />
            <span>{member.plan_type ?? "--"}</span>
            <span>{member.last_activity_at ? new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric", year: "numeric" }).format(new Date(member.last_activity_at)) : "--"}</span>
            <span data-no-localize>{member.last_activity_editor ?? "--"}</span>
            <span className="copilot-governance-status warning">未分配</span>
          </div>)}
        </ResizableGridTable>}
    </section>
  </div>
}

function CostCenterRequestForm({ organization }: { organization: string }) {
  const queryClient = useQueryClient()
  const options = useQuery(copilotQueries.costCenterOptions(organization, Boolean(organization)))
  const requests = useQuery(copilotQueries.costCenterRequests(Boolean(organization)))
  const [costCenterId, setCostCenterId] = useState("")
  const [reason, setReason] = useState("")
  const activeOptions = options.data?.filter((option) => option.state === "active") ?? []
  const selected = costCenterId || activeOptions[0]?.id || ""
  const create = useMutation({
    mutationFn: () => copilotApi.createCostCenterRequest({
      organization,
      cost_center_id: selected,
      reason: reason.trim(),
    }),
    onSuccess: () => {
      setReason("")
      void queryClient.invalidateQueries({ queryKey: copilotKeys.costCenterRequests })
    },
  })
  return <section className="finops-panel copilot-cost-center-request-panel">
    <div className="finops-panel-title"><h2>申请成本中心</h2><span className="finops-panel-title-meta">Owner 审批后生效</span></div>
    <form className="copilot-budget-form" onSubmit={(event) => { event.preventDefault(); if (selected) create.mutate() }}>
      <label><span>目标成本中心</span><Select value={selected} onValueChange={(value) => value && setCostCenterId(value)}>
        <SelectTrigger aria-label="目标成本中心"><SelectValue>{activeOptions.find((option) => option.id === selected)?.name ?? "选择成本中心"}</SelectValue></SelectTrigger>
        <SelectContent>{activeOptions.map((option) => <SelectItem value={option.id} key={option.id}>{option.name}</SelectItem>)}</SelectContent>
      </Select></label>
      <label><span>申请说明</span><Textarea value={reason} maxLength={1000} rows={3} placeholder="说明项目、团队或费用归属" onChange={(event) => setReason(event.target.value)} /></label>
      <div className="copilot-budget-form-footer"><small>提交申请不会直接修改 GitHub。</small><Button type="submit" disabled={!selected || create.isPending}><Send size={14} />{create.isPending ? "正在提交" : "提交申请"}</Button></div>
      {create.isSuccess && <p className="copilot-form-success"><ShieldCheck size={14} />成本中心申请已提交。</p>}
      {create.error && <p className="copilot-form-error"><AlertTriangle size={14} />{queryError(create.error)}</p>}
      {requests.data && <small>{`我的申请：待审核 ${requests.data.pending_count} · 已批准 ${requests.data.approved_count} · 已拒绝 ${requests.data.rejected_count}`}</small>}
    </form>
  </section>
}

function BudgetsView({ data, search }: { data: CopilotGovernance; search: string }) {
  const [scope, setScope] = useState("all")
  const scopes = [...new Set(data.budgets.map((budget) => budget.scope).filter(Boolean))].sort()
  const needle = search.trim().toLowerCase()
  const budgets = data.budgets.filter((budget) => (scope === "all" || budget.scope === scope)
    && (!needle || `${budget.entity_name} ${budget.scope} ${budget.product_skus.join(" ")}`.toLowerCase().includes(needle)))
  const total = budgets.reduce((sum, budget) => sum + budget.amount, 0)
  const consumed = budgets.reduce((sum, budget) => sum + (budget.consumed_amount ?? 0), 0)
  const tracked = budgets.filter((budget) => budget.consumed_amount != null)
  return <div className="finops-grid copilot-governance-grid">
    <div className="copilot-dimension-toolbar span-3">
      <span>预算范围</span>
      <Select value={scope} onValueChange={(value) => value && setScope(value)}>
        <SelectTrigger aria-label="预算范围"><SelectValue>{scope === "all" ? "全部范围" : scope}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">全部范围</SelectItem>{scopes.map((value) => <SelectItem value={value} key={value}>{value}</SelectItem>)}</SelectContent>
      </Select>
    </div>
    <section className="finops-kpis span-3 copilot-budget-kpis">
      <Kpi label="预算" value={String(budgets.length)} detail={`${tracked.length} 个含消耗数据`} icon={WalletCards} />
      <Kpi label="预算总额" value={currency.format(total)} detail="当前筛选范围" icon={CircleDollarSign} />
      <Kpi label="硬限制" value={String(budgets.filter((budget) => budget.prevent_further_usage).length)} detail={`${budgets.length} 条预算`} icon={ShieldCheck} />
      <Kpi label="提醒已启用" value={String(budgets.filter((budget) => budget.will_alert).length)} detail={`${budgets.length} 条预算`} icon={AlertTriangle} />
      <Kpi label="已使用" value={currency.format(consumed)} detail={`${decimal.format(total ? consumed / total * 100 : 0)}% 使用率`} icon={CircleDollarSign} />
      <Kpi label="剩余" value={currency.format(tracked.reduce((sum, budget) => sum + (budget.remaining_amount ?? 0), 0))} detail={`${budgets.filter((budget) => budget.prevent_further_usage).length} 个硬限制`} icon={ShieldCheck} />
    </section>
    <BudgetScopeChart budgets={budgets} />
    <section className="finops-panel span-3 copilot-governance-panel">
      <div className="finops-panel-title"><h2>预算</h2><span className="finops-panel-title-meta">{`${budgets.length} 条记录`}</span></div>
      {!budgets.length
        ? <GovernanceState kind="empty" title="没有符合条件的预算" detail="GitHub 返回了真实空结果。" />
        : <BudgetTable budgets={budgets} />}
    </section>
  </div>
}

function BudgetScopeChart({ budgets }: { budgets: CopilotBudgetSummary[] }) {
  const grouped = new Map<string, { scope: string; amount: number; used: number }>()
  for (const budget of budgets) {
    const row = grouped.get(budget.scope) ?? { scope: budget.scope, amount: 0, used: 0 }
    row.amount += budget.amount
    row.used += budget.consumed_amount ?? 0
    grouped.set(budget.scope, row)
  }
  const rows = [...grouped.values()].sort((left, right) => right.amount - left.amount)
  const hasValues = rows.some((row) => row.amount > 0 || row.used > 0)
  return <section className="finops-panel span-3 copilot-budget-chart-panel">
    <div className="finops-panel-title"><h2>按范围预算分布</h2><span className="finops-panel-title-meta">{`${rows.length} 个范围`}</span></div>
    {!rows.length
      ? <GovernanceState kind="empty" title="暂无预算分布" />
      : !hasValues
        ? <GovernanceState kind="empty" title="预算金额均为零" detail="GitHub 返回了预算记录，但当前金额与已使用金额均为零。" />
      : <div className="copilot-budget-chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 20, bottom: 8, left: 0 }}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={(value) => currency.format(Number(value))} />
            <YAxis type="category" dataKey="scope" tickLine={false} axisLine={false} tickMargin={8} width={96} />
            <Tooltip cursor={false} content={<FinOpsChartTooltip valueFormatter={(value) => currency.format(Number(value))} />} />
            <Bar dataKey="amount" name="预算总额" fill="var(--chart-1)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
            <Bar dataKey="used" name="已使用" fill="var(--chart-4)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>}
  </section>
}

function BudgetTable({ budgets }: { budgets: CopilotBudgetSummary[] }) {
  return <ResizableGridTable className="copilot-budget-summary-table" role="table" aria-label="GitHub Enterprise 预算" headerSelector=".copilot-budget-summary-head" minWidths={[100, 140, 130, 100, 110, 100, 76, 76]} columnGap={12} horizontalPadding={32}>
    <div className="copilot-budget-summary-head" role="row"><span>范围</span><span>实体</span><span>产品 / SKU</span><span>金额</span><span>已使用</span><span>剩余</span><span>限制</span><span>提醒</span></div>
    {budgets.map((budget) => <div className="copilot-budget-summary-row" role="row" key={budget.id}>
      <span>{budget.scope}</span>
      <span data-no-localize>{budget.entity_name || "--"}</span>
      <span data-no-localize>{budget.product_skus.join(", ") || "--"}</span>
      <strong>{currency.format(budget.amount)}</strong>
      <span>{budget.consumed_amount == null ? "--" : currency.format(budget.consumed_amount)}{budget.usage_percent != null && <small>{decimal.format(budget.usage_percent)}%</small>}</span>
      <span>{budget.remaining_amount == null ? "--" : currency.format(budget.remaining_amount)}</span>
      <span className={`copilot-governance-status ${budget.prevent_further_usage ? "danger" : "neutral"}`}>{budget.prevent_further_usage ? "硬限制" : "软限制"}</span>
      <span className={`copilot-governance-status ${budget.will_alert ? "success" : "neutral"}`}>{budget.will_alert ? "已启用" : "关闭"}</span>
    </div>)}
  </ResizableGridTable>
}

export function CopilotGovernancePage({ view, onToggleSidebar }: {
  view: CopilotGovernanceView
  onToggleSidebar: () => void
}) {
  const queryClient = useQueryClient()
  const status = useQuery(copilotQueries.status())
  const [organization, setOrganization] = useState<string | undefined>()
  const [search, setSearch] = useState("")
  const selectedOrganization = organization
    ?? status.data?.connections.find((connection) => connection.is_default)?.organization
    ?? status.data?.connections[0]?.organization
  const canRead = status.data?.configured === true && status.data.viewer_role === "owner"
  const canRequest = status.data?.configured === true
    && (status.data.viewer_role === "owner" || Boolean(status.data.viewer_github_login))
  const governance = useQuery(copilotQueries.governance(selectedOrganization, canRead))
  const teamUsage = useQuery(copilotQueries.dashboard(
    selectedOrganization,
    canRead && view === "teams",
  ))
  const HeaderIcon = headers[view].icon
  const refresh = () => queryClient.invalidateQueries({ queryKey: copilotKeys.all })
  return <div className="finops-workspace copilot-workspace copilot-governance-workspace">
    <header className="finops-header">
      <div>
        <Button variant="ghost" size="icon-sm" className="finops-sidebar-trigger" aria-label="切换导航栏" title="切换导航栏" onClick={onToggleSidebar}><PanelLeft size={16} /></Button>
        <span className="finops-header-icon copilot-header-icon"><HeaderIcon size={17} /></span>
        <h1>{headers[view].title}</h1>
      </div>
      <Button variant="ghost" size="icon-sm" className="finops-header-refresh" aria-label="刷新 GitHub 治理数据" title="刷新 GitHub 治理数据" disabled={status.isFetching || governance.isFetching} onClick={() => void refresh()}><RefreshCw className={status.isFetching || governance.isFetching ? "spin" : undefined} size={15} /></Button>
    </header>
    <div className="finops-filterbar copilot-filterbar">
      {(status.data?.connections.length ?? 0) > 1 && <Select value={selectedOrganization} onValueChange={(value) => value && setOrganization(value)}>
        <SelectTrigger aria-label="GitHub 组织"><SelectValue>{selectedOrganization}</SelectValue></SelectTrigger>
        <SelectContent>{status.data?.connections.map((connection) => <SelectItem key={connection.id} value={connection.organization}>{connection.display_name}</SelectItem>)}</SelectContent>
      </Select>}
      <SearchField value={search} onChange={setSearch} />
      {governance.data && <span className="copilot-governance-mode" data-enabled={governance.data.write_enabled || undefined}>{governance.data.write_enabled ? "允许受控写入" : "只读"}</span>}
    </div>
    <div className="finops-scroll-region">
      <div className="finops-content">
        {status.isLoading && <GovernanceState kind="loading" title="正在读取 GitHub Copilot 配置" />}
        {status.error && <GovernanceState kind="error" title="GitHub Copilot 状态不可用" detail={queryError(status.error)} />}
        {status.data && !status.data.configured && <GovernanceState kind="empty" title="尚未连接 GitHub Copilot Enterprise" detail="请由 Owner 在设置中连接 Enterprise Billing PAT。" />}
        {status.data?.configured && status.data.viewer_role !== "owner" && view !== "unassigned" && <GovernanceState kind="empty" title="需要 Owner 权限" detail="企业团队、成本中心、全员席位与完整预算仅对 Owner 开放。" />}
        {status.data?.configured && status.data.viewer_role !== "owner" && view === "unassigned" && !canRequest && <GovernanceState kind="empty" title="GitHub 身份尚未关联" detail="连接当前 GitHub 账号后即可提交成本中心申请。" />}
        {status.data?.configured && status.data.viewer_role !== "owner" && view === "unassigned" && canRequest && <div className="copilot-member-request-layout"><CostCenterRequestForm organization={selectedOrganization ?? ""} /></div>}
        {canRead && governance.isLoading && <GovernanceState kind="loading" title="正在读取 GitHub Enterprise 治理数据" />}
        {canRead && governance.error && <GovernanceState kind="error" title="GitHub Enterprise 治理数据不可用" detail={queryError(governance.error)} />}
        {Boolean(governance.data?.warnings.length) && <div className="copilot-warning" role="status"><AlertTriangle size={14} /><span>部分 GitHub Enterprise 数据暂不可用；其余内容仍来自实时 API。</span></div>}
        {governance.data && view === "teams" && <TeamsView data={governance.data} dashboard={teamUsage.data} search={search} />}
        {governance.data && view === "cost-centers" && <CostCentersView data={governance.data} search={search} />}
        {governance.data && view === "unassigned" && <UnassignedView data={governance.data} search={search} />}
        {governance.data && view === "unassigned" && canRequest && <CostCenterRequestForm organization={selectedOrganization ?? ""} />}
        {governance.data && view === "budgets" && <BudgetsView data={governance.data} search={search} />}
      </div>
    </div>
  </div>
}