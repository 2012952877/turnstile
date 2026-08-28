import { request, writeJson } from "../../api/client"
import type {
  CopilotBudgetRequest,
  CopilotBudgetRequestList,
  CopilotConnectionSummary,
  CopilotCostCenterOption,
  CopilotCostCenterRequest,
  CopilotCostCenterRequestList,
  CopilotDashboard,
  CopilotGovernance,
  CopilotIdentityMapping,
  CopilotImportedUsage,
  CopilotOAuthConfigSummary,
  CopilotStatus,
  CopilotUsageImportKind,
  CopilotUsageImportSummary,
} from "./types"

const organizationQuery = (organization?: string) =>
  organization ? `?organization=${encodeURIComponent(organization)}` : ""

export const copilotApi = {
  status: () => request<CopilotStatus>("/api/v1/copilot/status"),
  oauthConfig: () => request<CopilotOAuthConfigSummary>("/api/v1/copilot/oauth/config"),
  saveOAuthConfig: (value: {
    client_id: string
    client_secret?: string
    callback_url?: string
  }) => writeJson<CopilotOAuthConfigSummary>("/api/v1/copilot/oauth/config", value, "PUT"),
  oauthLoginUrl: (
    returnPath: string,
    purpose: "identity" | "organization" = "identity",
    organization?: string,
  ) => `/api/v1/copilot/oauth/login?${new URLSearchParams({
    return_origin: window.location.origin,
    return_path: returnPath,
    purpose,
    ...(organization ? { organization } : {}),
  })}`,
  saveConnection: (value: {
    organization: string
    token: string
    write_enabled: boolean
    set_default: boolean
  }) => writeJson<CopilotConnectionSummary>("/api/v1/copilot/connections", value, "PUT"),
  dashboard: (organization?: string) => request<CopilotDashboard>(
    `/api/v1/copilot/dashboard${organizationQuery(organization)}`,
  ),
  governance: (organization?: string) => request<CopilotGovernance>(
    `/api/v1/copilot/governance${organizationQuery(organization)}`,
  ),
  importedUsage: (sourceKind: CopilotUsageImportKind, organization?: string) =>
    request<CopilotImportedUsage>(
      `/api/v1/copilot/imported-usage?${new URLSearchParams({
        source_kind: sourceKind,
        ...(organization ? { organization } : {}),
      })}`,
    ),
  usageImports: (sourceKind: CopilotUsageImportKind, organization?: string) =>
    request<CopilotUsageImportSummary[]>(
      `/api/v1/copilot/usage-imports?${new URLSearchParams({
        source_kind: sourceKind,
        ...(organization ? { organization } : {}),
      })}`,
    ),
  importUsage: (file: File, organization?: string) => request<CopilotUsageImportSummary>(
    `/api/v1/copilot/usage-imports?${new URLSearchParams({
      filename: file.name,
      ...(organization ? { organization } : {}),
    })}`,
    { method: "POST", headers: { "content-type": file.type || "text/csv" }, body: file },
  ),
  costCenterOptions: (organization?: string) => request<CopilotCostCenterOption[]>(
    `/api/v1/copilot/cost-centers/options${organizationQuery(organization)}`,
  ),
  costCenterRequests: () => request<CopilotCostCenterRequestList>(
    "/api/v1/copilot/cost-center-requests",
  ),
  createCostCenterRequest: (value: {
    organization: string
    cost_center_id: string
    reason: string
  }) => writeJson<CopilotCostCenterRequest>("/api/v1/copilot/cost-center-requests", value),
  reviewCostCenterRequest: (
    requestId: string,
    value: { decision: "approve" | "reject"; comment: string; apply_to_github: boolean },
  ) => writeJson<CopilotCostCenterRequest>(
    `/api/v1/copilot/cost-center-requests/${encodeURIComponent(requestId)}/review`,
    value,
  ),
  identities: () => request<CopilotIdentityMapping[]>("/api/v1/copilot/identities"),
  saveIdentity: (appUserId: string, githubLogin: string) => writeJson<CopilotIdentityMapping>(
    `/api/v1/copilot/identities/${encodeURIComponent(appUserId)}`,
    { github_login: githubLogin },
    "PUT",
  ),
  budgetRequests: () => request<CopilotBudgetRequestList>("/api/v1/copilot/budget-requests"),
  createBudgetRequest: (value: {
    organization: string
    amount_usd: number
    reason: string
  }) => writeJson<CopilotBudgetRequest>("/api/v1/copilot/budget-requests", value),
  reviewBudgetRequest: (
    requestId: string,
    value: {
      decision: "approve" | "reject"
      approved_amount_usd?: number
      comment: string
      apply_to_github: boolean
    },
  ) => writeJson<CopilotBudgetRequest>(
    `/api/v1/copilot/budget-requests/${encodeURIComponent(requestId)}/review`,
    value,
  ),
}
