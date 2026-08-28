import { queryOptions } from "@tanstack/react-query"

import { cachePolicies } from "../../api/cache-policies"
import { assistantApi } from "./api"
import type { AssistantSource } from "./types"

export const pinnedChartsKey = (owner: string) =>
  ["assistant", "pinned-charts", owner] as const
export const pinnedChartsQuery = (owner: string | null) => queryOptions({
  queryKey: pinnedChartsKey(owner ?? ""),
  queryFn: assistantApi.pinnedReports,
  enabled: Boolean(owner),
  ...cachePolicies.reference,
})

export const conversationsKey = (source: AssistantSource, owner: string) =>
  ["assistant", source, "conversations", owner] as const
export const conversationKey = (source: AssistantSource, owner: string, id: string) =>
  ["assistant", source, "conversation", owner, id] as const

export const conversationsQuery = (
  source: AssistantSource,
  owner: string | null,
) => queryOptions({
  queryKey: conversationsKey(source, owner ?? ""),
  queryFn: () => assistantApi.conversations(source),
  enabled: Boolean(owner),
  ...cachePolicies.aggregate,
})

export const conversationQuery = (
  source: AssistantSource,
  owner: string | null,
  id: string | null,
) => queryOptions({
  queryKey: conversationKey(source, owner ?? "", id ?? ""),
  queryFn: () => assistantApi.conversation(source, id!),
  enabled: Boolean(owner && id),
  ...cachePolicies.requestDetail,
})

export const assistantSettingsKey = ["assistant", "settings"] as const
export const assistantSettingsQuery = () => queryOptions({
  queryKey: assistantSettingsKey,
  queryFn: assistantApi.settings,
  ...cachePolicies.reference,
})
