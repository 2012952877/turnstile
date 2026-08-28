import { queryOptions } from "@tanstack/react-query"

import { cachePolicies } from "../../api/cache-policies"
import { copilotApi } from "./api"
import type { CopilotUsageImportKind } from "./types"

export const copilotKeys = {
  all: ["copilot"] as const,
  status: ["copilot", "status"] as const,
  dashboard: (organization: string | null) => ["copilot", "dashboard", organization] as const,
  governance: (organization: string | null) => ["copilot", "governance", organization] as const,
  importedUsage: (sourceKind: CopilotUsageImportKind, organization: string | null) =>
    ["copilot", "imported-usage", sourceKind, organization] as const,
  usageImports: (sourceKind: CopilotUsageImportKind, organization: string | null) =>
    ["copilot", "usage-imports", sourceKind, organization] as const,
  costCenterOptions: (organization: string | null) =>
    ["copilot", "cost-center-options", organization] as const,
  costCenterRequests: ["copilot", "cost-center-requests"] as const,
  identities: ["copilot", "identities"] as const,
  budgetRequests: ["copilot", "budget-requests"] as const,
  oauthConfig: ["copilot", "oauth-config"] as const,
}

export const copilotQueries = {
  status: () => queryOptions({
    queryKey: copilotKeys.status,
    queryFn: copilotApi.status,
    ...cachePolicies.aggregate,
  }),
  dashboard: (organization?: string, enabled = true) => queryOptions({
    queryKey: copilotKeys.dashboard(organization ?? null),
    queryFn: () => copilotApi.dashboard(organization),
    enabled,
    ...cachePolicies.aggregate,
  }),
  governance: (organization?: string, enabled = true) => queryOptions({
    queryKey: copilotKeys.governance(organization ?? null),
    queryFn: () => copilotApi.governance(organization),
    enabled,
    ...cachePolicies.aggregate,
  }),
  importedUsage: (
    sourceKind: CopilotUsageImportKind,
    organization?: string,
    enabled = true,
  ) => queryOptions({
    queryKey: copilotKeys.importedUsage(sourceKind, organization ?? null),
    queryFn: () => copilotApi.importedUsage(sourceKind, organization),
    enabled,
    ...cachePolicies.aggregate,
  }),
  usageImports: (
    sourceKind: CopilotUsageImportKind,
    organization?: string,
    enabled = true,
  ) => queryOptions({
    queryKey: copilotKeys.usageImports(sourceKind, organization ?? null),
    queryFn: () => copilotApi.usageImports(sourceKind, organization),
    enabled,
    ...cachePolicies.reference,
  }),
  costCenterOptions: (organization?: string, enabled = true) => queryOptions({
    queryKey: copilotKeys.costCenterOptions(organization ?? null),
    queryFn: () => copilotApi.costCenterOptions(organization),
    enabled,
    ...cachePolicies.reference,
  }),
  costCenterRequests: (enabled = true) => queryOptions({
    queryKey: copilotKeys.costCenterRequests,
    queryFn: copilotApi.costCenterRequests,
    enabled,
    ...cachePolicies.aggregate,
  }),
  identities: (enabled = true) => queryOptions({
    queryKey: copilotKeys.identities,
    queryFn: copilotApi.identities,
    enabled,
    ...cachePolicies.reference,
  }),
  budgetRequests: (enabled = true) => queryOptions({
    queryKey: copilotKeys.budgetRequests,
    queryFn: copilotApi.budgetRequests,
    enabled,
    ...cachePolicies.aggregate,
  }),
  oauthConfig: (enabled = true) => queryOptions({
    queryKey: copilotKeys.oauthConfig,
    queryFn: copilotApi.oauthConfig,
    enabled,
    ...cachePolicies.reference,
  }),
}
