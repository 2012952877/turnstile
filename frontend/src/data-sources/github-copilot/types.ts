export type CopilotConnectionSummary = {
  id: string
  organization: string
  display_name: string
  credential_configured: boolean
  credential_hint: string | null
  write_enabled: boolean
  is_default: boolean
}

export type CopilotStatus = {
  configured: boolean
  oauth_configured: boolean
  viewer_role: "owner" | "member"
  viewer_github_login: string | null
  connections: CopilotConnectionSummary[]
}

export type CopilotOAuthConfigSummary = {
  configured: boolean
  client_id: string | null
  client_secret_hint: string | null
  callback_url: string | null
  effective_callback_url: string
}

export type CopilotUsageTotals = {
  seats: number
  active_users: number
  interactions: number
  generations: number
  acceptances: number
  acceptance_rate: number
  loc_added: number
  loc_deleted: number
  ai_credits_used: number
  gross_amount: number
  net_amount: number
  cli_prompt_tokens: number
  cli_output_tokens: number
}

export type CopilotDailyUsage = {
  day: string
  active_users: number
  weekly_active_users?: number
  monthly_active_users?: number
  agent_users?: number
  chat_users?: number
  interactions: number
  generations: number
  acceptances: number
  loc_added: number
  loc_deleted: number
  ai_credits_used: number
}

export type CopilotBreakdownItem = {
  key: string
  label: string
  interactions: number
  generations: number
  acceptances: number
  loc_added: number
  loc_deleted?: number
}

export type CopilotAiCreditItem = {
  model: string
  gross_quantity: number
  discount_quantity: number
  net_quantity: number
  net_cost: number
}

export type CopilotMemberUsage = {
  login: string
  avatar_url: string | null
  plan_type: string
  seat_created_at: string | null
  last_activity_at: string | null
  last_activity_editor: string | null
  pending_cancellation_date: string | null
  assigning_team: string | null
  adoption_phase: string | null
  budget_amount: number | null
  budget_consumed: number | null
  budget_remaining: number | null
  totals: CopilotUsageTotals
}

export type CopilotDashboard = {
  organization: string
  report_start_day: string | null
  report_end_day: string | null
  generated_at: string
  viewer_github_login: string | null
  can_view_members: boolean
  subscription: {
    plan_type: string
    seat_management_setting: string | null
    seat_breakdown: Record<string, number>
    estimated_monthly_seat_cost: number
    price_per_seat: number
  }
  totals: CopilotUsageTotals
  daily: CopilotDailyUsage[]
  models: CopilotBreakdownItem[]
  features: CopilotBreakdownItem[]
  languages: CopilotBreakdownItem[]
  ides: CopilotBreakdownItem[]
  ai_credit_breakdown?: CopilotAiCreditItem[]
  members: CopilotMemberUsage[]
  warnings: string[]
}

export type CopilotGovernanceMember = {
  login: string
  avatar_url: string | null
  has_seat: boolean
  last_activity_at: string | null
  last_activity_editor: string | null
  plan_type: string | null
}

export type CopilotTeamSummary = {
  slug: string
  name: string
  description: string | null
  organization_selection_type: string | null
  organizations: string[]
  members: CopilotGovernanceMember[]
  member_count: number
  seat_count: number
}

export type CopilotCostCenterSummary = {
  id: string
  name: string
  state: "active" | "archived"
  resources: Array<{ type: string; name: string }>
  members: CopilotGovernanceMember[]
  member_count: number
}

export type CopilotBudgetSummary = {
  id: string
  budget_type: string
  scope: string
  entity_name: string
  product_skus: string[]
  amount: number
  consumed_amount: number | null
  remaining_amount: number | null
  usage_percent: number | null
  prevent_further_usage: boolean
  will_alert: boolean
  alert_recipients: string[]
}

export type CopilotGovernance = {
  organization: string
  generated_at: string
  write_enabled: boolean
  seats: CopilotGovernanceMember[]
  teams: CopilotTeamSummary[]
  cost_centers: CopilotCostCenterSummary[]
  unassigned_seats: CopilotGovernanceMember[]
  budgets: CopilotBudgetSummary[]
  warnings: string[]
}

export type CopilotUsageImportKind = "ai_usage" | "usage_report"

export type CopilotUsageImportSummary = {
  upload_id: string
  source_kind: CopilotUsageImportKind
  filename: string
  content_sha256: string
  row_count: number
  inserted_count: number
  duplicate_count: number
  first_usage_date: string
  last_usage_date: string
  created_at: string
}

export type CopilotImportedUsageBreakdown = {
  key: string
  quantity: number
  gross_amount: number
  net_amount: number
  user_count: number
}

export type CopilotImportedUsage = {
  source_kind: CopilotUsageImportKind
  has_data: boolean
  first_usage_date: string | null
  last_usage_date: string | null
  total_quantity: number
  total_gross_amount: number
  total_net_amount: number
  unique_users: number
  unique_organizations: number
  daily: Array<{
    day: string
    quantity: number
    gross_amount: number
    net_amount: number
    active_users: number
  }>
  primary_breakdown: CopilotImportedUsageBreakdown[]
  product_breakdown?: CopilotImportedUsageBreakdown[]
  organization_breakdown: CopilotImportedUsageBreakdown[]
  cost_center_breakdown: CopilotImportedUsageBreakdown[]
  users: Array<{
    login: string
    organization: string
    cost_center_name: string | null
    quantity: number
    gross_amount: number
    net_amount: number
    active_days: number
    monthly_quota: number | null
    usage_percent: number | null
  }>
  organizations: string[]
  cost_centers: string[]
  products: string[]
  skus: string[]
}

export type CopilotCostCenterOption = {
  id: string
  name: string
  state: "active" | "archived"
}

export type CopilotCostCenterRequest = {
  id: string
  organization: string
  app_user_id: string
  user_email: string
  user_display_name: string | null
  github_login: string
  cost_center_id: string
  cost_center_name: string
  reason: string
  status: "pending" | "approved" | "rejected"
  github_sync_status: "not_requested" | "skipped" | "updated" | "failed"
  github_sync_error: string | null
  reviewed_by: string | null
  review_comment: string | null
  created_at: string
  updated_at: string
  reviewed_at: string | null
}

export type CopilotCostCenterRequestList = {
  items: CopilotCostCenterRequest[]
  can_review: boolean
  pending_count: number
  approved_count: number
  rejected_count: number
}

export type CopilotIdentityMapping = {
  app_user_id: string
  email: string
  display_name: string | null
  github_login: string | null
}

export type CopilotBudgetRequest = {
  id: string
  organization: string
  app_user_id: string
  user_email: string
  user_display_name: string | null
  github_login: string
  requested_amount_usd: number
  approved_amount_usd: number | null
  reason: string
  status: "pending" | "approved" | "rejected"
  github_sync_status: "not_requested" | "skipped" | "created" | "updated" | "failed"
  github_sync_error: string | null
  reviewed_by: string | null
  review_comment: string | null
  created_at: string
  updated_at: string
  reviewed_at: string | null
}

export type CopilotBudgetRequestList = {
  items: CopilotBudgetRequest[]
  can_review: boolean
  pending_count: number
  approved_count: number
  rejected_count: number
}
