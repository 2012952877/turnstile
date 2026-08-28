import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Check,
  CircleDollarSign,
  Clock3,
  PanelLeft,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  WalletCards,
  X,
} from "lucide-react"

import { copilotApi } from "../api"
import { CopilotConnectButton } from "../connect-button"
import { copilotKeys, copilotQueries } from "../queries"
import type { CopilotBudgetRequest, CopilotCostCenterRequest } from "../types"
import { CopilotLogo } from "../../../components/brand-logos"
import { FINOPS_NAVIGATE_EVENT } from "../../../lib/navigation"
import { Button } from "../../../components/ui/button"
import { Checkbox } from "../../../components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog"
import { Input } from "../../../components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../../components/ui/select"
import { Textarea } from "../../../components/ui/textarea"
import { ResizableGridTable } from "../../../components/ui/resizable-table"
import { getIntlLocale } from "../../../locales"

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
})

function queryError(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function openSettings() {
  const url = new URL(window.location.href)
  url.searchParams.set("page", "settings")
  window.history.replaceState(null, "", url)
  window.dispatchEvent(new Event(FINOPS_NAVIGATE_EVENT))
}

function BudgetState({
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

function RequestKpi({ label, value, detail, icon: Icon }: {
  label: string
  value: string
  detail: string
  icon: typeof WalletCards
}) {
  return <div>
    <span className="finops-kpi-icon"><Icon size={14} /></span>
    <label>{label}</label>
    <strong>{value}</strong>
    <small>{detail}</small>
  </div>
}

const statusLabels = {
  pending: "待审核",
  approved: "已批准",
  rejected: "已拒绝",
} as const

const syncLabels = {
  not_requested: "未同步",
  skipped: "未同步",
  created: "已创建",
  updated: "已更新",
  failed: "同步失败",
} as const

function RequestForm({ organizations }: { organizations: string[] }) {
  const queryClient = useQueryClient()
  const [organization, setOrganization] = useState(organizations[0] ?? "")
  const [amount, setAmount] = useState("30")
  const [reason, setReason] = useState("")
  useEffect(() => {
    if (!organization && organizations[0]) setOrganization(organizations[0])
  }, [organization, organizations])
  const create = useMutation({
    mutationFn: () => copilotApi.createBudgetRequest({
      organization,
      amount_usd: Number(amount),
      reason: reason.trim(),
    }),
    onSuccess: () => {
      setReason("")
      void queryClient.invalidateQueries({ queryKey: copilotKeys.budgetRequests })
    },
  })
  const wholeAmount = Number(amount)
  const valid = organization.length > 0
    && Number.isInteger(wholeAmount)
    && wholeAmount > 0
    && wholeAmount <= 1_000_000
  return <section className="finops-panel copilot-request-form-panel">
    <div className="finops-panel-title"><h2>申请个人预算</h2><span className="finops-panel-title-meta">每月 AI Credits</span></div>
    <form className="copilot-budget-form" onSubmit={(event) => { event.preventDefault(); if (valid) create.mutate() }}>
      <label>
        <span>GitHub 组织</span>
        <Select value={organization} onValueChange={(value) => value && setOrganization(value)}>
          <SelectTrigger aria-label="GitHub 组织"><SelectValue>{organization}</SelectValue></SelectTrigger>
          <SelectContent>{organizations.map((item) => <SelectItem value={item} key={item}>{item}</SelectItem>)}</SelectContent>
        </Select>
      </label>
      <label>
        <span>申请金额（USD/月）</span>
        <Input value={amount} type="number" min={1} max={1_000_000} step={1} inputMode="numeric" onChange={(event) => setAmount(event.target.value)} />
      </label>
      <label className="copilot-budget-reason">
        <span>申请说明</span>
        <Textarea value={reason} maxLength={1000} rows={4} placeholder="说明用途或项目背景" onChange={(event) => setReason(event.target.value)} />
      </label>
      <div className="copilot-budget-form-footer">
        <small>GitHub 用户预算按整美元、每月计费周期生效。</small>
        <Button type="submit" disabled={!valid || create.isPending}><Send size={14} />{create.isPending ? "正在提交" : "提交申请"}</Button>
      </div>
      {create.isSuccess && <p className="copilot-form-success"><Check size={14} />预算申请已提交。</p>}
      {create.error && <p className="copilot-form-error"><AlertTriangle size={14} />{queryError(create.error)}</p>}
    </form>
  </section>
}

function RequestTable({
  requests,
  canReview,
  onReview,
}: {
  requests: CopilotBudgetRequest[]
  canReview: boolean
  onReview: (request: CopilotBudgetRequest) => void
}) {
  return <section className="finops-panel copilot-request-table-panel">
    <div className="finops-panel-title"><h2>预算申请</h2><span className="finops-panel-title-meta">{`${requests.length} 条记录`}</span></div>
    {!requests.length
      ? <BudgetState kind="empty" title="暂无预算申请" />
      : <ResizableGridTable className="copilot-request-table" role="table" aria-label="GitHub Copilot 预算申请" headerSelector=".copilot-request-head" minWidths={[160, 120, 100, 86, 88, 120, 64]} columnGap={12} horizontalPadding={32}>
        <div className="copilot-request-head" role="row"><span>申请人</span><span>组织</span><span>申请金额</span><span>状态</span><span>GitHub</span><span>提交时间</span><span /></div>
        {requests.map((request) => <div className="copilot-request-row" role="row" key={request.id}>
          <div><b>{request.user_display_name ?? request.github_login}</b><small data-no-localize>{request.github_login}</small></div>
          <span data-no-localize>{request.organization}</span>
          <span>{currency.format(request.requested_amount_usd)}{request.approved_amount_usd != null && request.approved_amount_usd !== request.requested_amount_usd ? <small>批准 {currency.format(request.approved_amount_usd)}</small> : null}</span>
          <span className="copilot-request-status" data-status={request.status}>{statusLabels[request.status]}</span>
          <span className="copilot-sync-status" data-status={request.github_sync_status} title={request.github_sync_error ?? undefined}>{syncLabels[request.github_sync_status]}</span>
          <time>{new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(request.created_at))}</time>
          <span>{canReview && request.status === "pending" && <Button className="copilot-review-action" size="sm" onClick={() => onReview(request)}>审核</Button>}</span>
        </div>)}
      </ResizableGridTable>}
  </section>
}

function ReviewDialog({
  request,
  writeEnabled,
  onClose,
}: {
  request: CopilotBudgetRequest
  writeEnabled: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [amount, setAmount] = useState(String(request.requested_amount_usd))
  const [comment, setComment] = useState("")
  const [applyToGitHub, setApplyToGitHub] = useState(false)
  const review = useMutation({
    mutationFn: (decision: "approve" | "reject") => copilotApi.reviewBudgetRequest(
      request.id,
      {
        decision,
        approved_amount_usd: decision === "approve" ? Number(amount) : undefined,
        comment: comment.trim(),
        apply_to_github: decision === "approve" && applyToGitHub,
      },
    ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: copilotKeys.budgetRequests })
      void queryClient.invalidateQueries({ queryKey: ["copilot", "dashboard"] })
      onClose()
    },
  })
  const validAmount = Number.isInteger(Number(amount)) && Number(amount) > 0
  return <Dialog open onOpenChange={(open) => { if (!open && !review.isPending) onClose() }}>
    <DialogContent className="registry-editor-dialog copilot-review-dialog">
      <div className="registry-editor">
        <DialogHeader className="registry-editor-header">
          <DialogTitle>审核 Copilot 预算</DialogTitle>
          <DialogDescription><span data-no-localize>{request.github_login}</span> · {request.organization}</DialogDescription>
          <DialogClose className="registry-editor-close" aria-label="关闭"><X size={16} /></DialogClose>
        </DialogHeader>
        <div className="registry-editor-body copilot-review-body">
          <div className="copilot-review-summary"><span>申请金额</span><strong>{currency.format(request.requested_amount_usd)}</strong><p>{request.reason || "未填写申请说明"}</p></div>
          <label className="registry-field"><span className="registry-field-label">批准金额（USD/月）</span><Input value={amount} type="number" min={1} step={1} onChange={(event) => setAmount(event.target.value)} /></label>
          <label className="registry-field"><span className="registry-field-label">审核意见</span><Textarea value={comment} rows={3} maxLength={1000} onChange={(event) => setComment(event.target.value)} /></label>
          <label className="copilot-github-sync-option" data-disabled={!writeEnabled || undefined}>
            <Checkbox checked={applyToGitHub} disabled={!writeEnabled} onCheckedChange={(checked) => setApplyToGitHub(checked === true)} />
            <span><b>同步到 GitHub</b><small>{writeEnabled ? "批准后创建或更新真实用户预算。默认关闭。" : "此连接为只读，不能同步外部变更。"}</small></span>
          </label>
          {review.error && <p className="copilot-form-error"><AlertTriangle size={14} />{queryError(review.error)}</p>}
        </div>
        <DialogFooter className="registry-editor-footer">
          <Button variant="outline" disabled={review.isPending} onClick={() => review.mutate("reject")}><X size={14} />拒绝</Button>
          <Button disabled={review.isPending || !validAmount} onClick={() => review.mutate("approve")}><Check size={14} />批准</Button>
        </DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
}

function CostCenterRequestTable({
  requests,
  canReview,
  onReview,
}: {
  requests: CopilotCostCenterRequest[]
  canReview: boolean
  onReview: (request: CopilotCostCenterRequest) => void
}) {
  return <section className="finops-panel copilot-request-table-panel">
    <div className="finops-panel-title"><h2>成本中心申请</h2><span className="finops-panel-title-meta">{`${requests.length} 条记录`}</span></div>
    {!requests.length
      ? <BudgetState kind="empty" title="暂无成本中心申请" />
      : <ResizableGridTable className="copilot-request-table" role="table" aria-label="GitHub 成本中心申请" headerSelector=".copilot-request-head" minWidths={[160, 120, 100, 86, 88, 120, 64]} columnGap={12} horizontalPadding={32}>
        <div className="copilot-request-head" role="row"><span>申请人</span><span>组织</span><span>目标成本中心</span><span>状态</span><span>GitHub</span><span>提交时间</span><span /></div>
        {requests.map((request) => <div className="copilot-request-row" role="row" key={request.id}>
          <div><b>{request.user_display_name ?? request.github_login}</b><small data-no-localize>{request.github_login}</small></div>
          <span data-no-localize>{request.organization}</span>
          <span data-no-localize>{request.cost_center_name}</span>
          <span className="copilot-request-status" data-status={request.status}>{statusLabels[request.status]}</span>
          <span className="copilot-sync-status" data-status={request.github_sync_status} title={request.github_sync_error ?? undefined}>{syncLabels[request.github_sync_status]}</span>
          <time>{new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(request.created_at))}</time>
          <span>{canReview && request.status === "pending" && <Button className="copilot-review-action" size="sm" onClick={() => onReview(request)}>审核</Button>}</span>
        </div>)}
      </ResizableGridTable>}
  </section>
}

function CostCenterReviewDialog({
  request,
  writeEnabled,
  onClose,
}: {
  request: CopilotCostCenterRequest
  writeEnabled: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [comment, setComment] = useState("")
  const [applyToGitHub, setApplyToGitHub] = useState(false)
  const review = useMutation({
    mutationFn: (decision: "approve" | "reject") => copilotApi.reviewCostCenterRequest(
      request.id,
      { decision, comment: comment.trim(), apply_to_github: decision === "approve" && applyToGitHub },
    ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: copilotKeys.costCenterRequests })
      void queryClient.invalidateQueries({ queryKey: ["copilot", "governance"] })
      onClose()
    },
  })
  return <Dialog open onOpenChange={(open) => { if (!open && !review.isPending) onClose() }}>
    <DialogContent className="registry-editor-dialog copilot-review-dialog">
      <div className="registry-editor">
        <DialogHeader className="registry-editor-header">
          <DialogTitle>审核成本中心申请</DialogTitle>
          <DialogDescription><span data-no-localize>{request.github_login}</span> · <span data-no-localize>{request.cost_center_name}</span></DialogDescription>
          <DialogClose className="registry-editor-close" aria-label="关闭"><X size={16} /></DialogClose>
        </DialogHeader>
        <div className="registry-editor-body copilot-review-body">
          <div className="copilot-review-summary"><span>目标成本中心</span><strong data-no-localize>{request.cost_center_name}</strong><p>{request.reason || "未填写申请说明"}</p></div>
          <label className="registry-field"><span className="registry-field-label">审核意见</span><Textarea value={comment} rows={3} maxLength={1000} onChange={(event) => setComment(event.target.value)} /></label>
          <label className="copilot-github-sync-option" data-disabled={!writeEnabled || undefined}>
            <Checkbox checked={applyToGitHub} disabled={!writeEnabled} onCheckedChange={(checked) => setApplyToGitHub(checked === true)} />
            <span><b>同步到 GitHub</b><small>{writeEnabled ? "批准后把真实 GitHub 用户添加到目标成本中心。默认关闭。" : "此连接为只读，不能同步外部变更。"}</small></span>
          </label>
          {review.error && <p className="copilot-form-error"><AlertTriangle size={14} />{queryError(review.error)}</p>}
        </div>
        <DialogFooter className="registry-editor-footer">
          <Button variant="outline" disabled={review.isPending} onClick={() => review.mutate("reject")}><X size={14} />拒绝</Button>
          <Button disabled={review.isPending} onClick={() => review.mutate("approve")}><Check size={14} />批准</Button>
        </DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
}

export function CopilotBudgetPage({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const queryClient = useQueryClient()
  const status = useQuery(copilotQueries.status())
  const canRead = status.data?.configured === true
    && (status.data.viewer_role === "owner" || Boolean(status.data.viewer_github_login))
  const requests = useQuery(copilotQueries.budgetRequests(canRead))
  const costCenterRequests = useQuery(copilotQueries.costCenterRequests(canRead))
  const [reviewing, setReviewing] = useState<CopilotBudgetRequest | null>(null)
  const [reviewingCostCenter, setReviewingCostCenter] = useState<CopilotCostCenterRequest | null>(null)
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "approved" | "rejected">("all")
  const organizations = status.data?.connections.map((connection) => connection.organization) ?? []
  const reviewingConnection = status.data?.connections.find(
    (connection) => connection.organization === (reviewing?.organization ?? reviewingCostCenter?.organization),
  )
  const budgetItems = requests.data?.items ?? []
  const costCenterItems = costCenterRequests.data?.items ?? []
  const allItems = [...budgetItems, ...costCenterItems]
  const filteredBudgets = budgetItems.filter((request) => statusFilter === "all" || request.status === statusFilter)
  const filteredCostCenters = costCenterItems.filter((request) => statusFilter === "all" || request.status === statusFilter)
  const pendingCount = allItems.filter((request) => request.status === "pending").length
  const approvedCount = allItems.filter((request) => request.status === "approved").length
  const approvedAmount = budgetItems.reduce(
    (sum, request) => sum + (request.status === "approved" ? request.approved_amount_usd ?? request.requested_amount_usd : 0),
    0,
  )
  const pendingAmount = budgetItems.reduce(
    (sum, request) => sum + (request.status === "pending" ? request.requested_amount_usd : 0),
    0,
  )
  const refresh = () => queryClient.invalidateQueries({ queryKey: copilotKeys.all })
  return <div className="finops-workspace copilot-workspace copilot-budget-workspace">
    <header className="finops-header">
      <div>
        <Button variant="ghost" size="icon-sm" className="finops-sidebar-trigger" aria-label="切换导航栏" title="切换导航栏" onClick={onToggleSidebar}><PanelLeft size={16} /></Button>
        <span className="finops-header-icon copilot-header-icon"><WalletCards size={17} /></span>
        <h1>请求审批</h1>
      </div>
      <div className="copilot-header-actions">
        <Button variant="ghost" size="icon-sm" className="finops-header-refresh" aria-label="刷新 Copilot 请求" title="刷新 Copilot 请求" disabled={status.isFetching || requests.isFetching || costCenterRequests.isFetching} onClick={() => void refresh()}><RefreshCw className={status.isFetching || requests.isFetching || costCenterRequests.isFetching ? "spin" : undefined} size={15} /></Button>
      </div>
    </header>
    <div className="finops-filterbar copilot-filterbar">
      {requests.data && <div className="trend-segment" role="group" aria-label="请求状态">
        {(["all", "pending", "approved", "rejected"] as const).map((value) => <button className={statusFilter === value ? "active" : ""} type="button" onClick={() => setStatusFilter(value)} key={value}>{value === "all" ? "全部状态" : statusLabels[value]}</button>)}
      </div>}
    </div>
    <div className="finops-scroll-region">
      <div className="finops-content copilot-budget-content">
        {status.isLoading && <BudgetState kind="loading" title="正在读取 GitHub Copilot 配置" />}
        {status.error && <BudgetState kind="error" title="GitHub Copilot 状态不可用" detail={queryError(status.error)} />}
        {status.data && !status.data.configured && <BudgetState
          kind="empty"
          title={status.data.viewer_github_login ? "GitHub 账号已连接，组织数据尚未连接" : "尚未连接 GitHub Copilot 组织数据"}
          detail={status.data.viewer_role === "owner" ? "身份关联只确认你是谁；Copilot 席位、用量和预算仍需连接组织数据。" : "你的 GitHub 身份已关联，但 Owner 尚未连接组织 Copilot 数据。"}
          action={status.data.viewer_role === "owner" ? <Button className="copilot-configure-action" onClick={openSettings}><Settings size={14} />连接组织数据</Button> : undefined}
        />}
        {status.data?.configured && status.data.viewer_role === "member" && !status.data.viewer_github_login && <BudgetState
          kind="empty"
          title="GitHub 身份尚未关联"
          detail={status.data.oauth_configured ? "连接当前 GitHub 账号后即可提交预算申请。" : "Owner 尚未配置 GitHub 账号连接。"}
          action={status.data.oauth_configured ? <CopilotConnectButton /> : undefined}
        />}
        {canRead && requests.isLoading && <BudgetState kind="loading" title="正在读取预算申请" />}
        {canRead && requests.error && <BudgetState kind="error" title="预算申请不可用" detail={queryError(requests.error)} />}
        {canRead && costCenterRequests.error && <BudgetState kind="error" title="成本中心申请不可用" detail={queryError(costCenterRequests.error)} />}
        {requests.data && costCenterRequests.data && <section className="finops-kpis copilot-request-kpis">
          <RequestKpi label="请求总数" value={String(allItems.length)} detail={`${budgetItems.length} 个预算 · ${costCenterItems.length} 个成本中心`} icon={WalletCards} />
          <RequestKpi label="待审核" value={String(pendingCount)} detail={`${allItems.length} 个请求`} icon={Clock3} />
          <RequestKpi label="已批准" value={String(approvedCount)} detail={`${allItems.length} 个请求`} icon={ShieldCheck} />
          <RequestKpi label="批准金额" value={currency.format(approvedAmount)} detail="已批准预算申请" icon={CircleDollarSign} />
          <RequestKpi label="待审核金额" value={currency.format(pendingAmount)} detail="待审核预算申请" icon={CircleDollarSign} />
        </section>}
        {requests.data && <div className={`copilot-budget-layout ${requests.data.can_review ? "owner" : ""}`}>
          {!requests.data.can_review && <RequestForm organizations={organizations} />}
          <RequestTable requests={filteredBudgets} canReview={requests.data.can_review} onReview={setReviewing} />
          {costCenterRequests.data && <CostCenterRequestTable requests={filteredCostCenters} canReview={costCenterRequests.data.can_review} onReview={setReviewingCostCenter} />}
        </div>}
      </div>
    </div>
    {reviewing && <ReviewDialog request={reviewing} writeEnabled={reviewingConnection?.write_enabled ?? false} onClose={() => setReviewing(null)} />}
    {reviewingCostCenter && <CostCenterReviewDialog request={reviewingCostCenter} writeEnabled={reviewingConnection?.write_enabled ?? false} onClose={() => setReviewingCostCenter(null)} />}
  </div>
}