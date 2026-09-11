import type { ManagedModel } from "../../data-sources/apim/types"

export type ModelEditDraft = {
  displayName: string
  contextWindow: string
  inputPrice: string
  outputPrice: string
  cacheReadPrice: string
  cacheWritePrice: string
  allowedRoles: string[]
  enabled: boolean
  isDefault: boolean
}

export type ModelEditError = "display_name" | "context_window" | "prices" | "default_disabled"

export function createModelEditDraft(model: ManagedModel): ModelEditDraft {
  return {
    displayName: model.display_name,
    contextWindow: model.context_window?.toString() ?? "",
    inputPrice: model.input_cost_per_million?.toString() ?? "",
    outputPrice: model.output_cost_per_million?.toString() ?? "",
    cacheReadPrice: model.cached_cost_per_million?.toString() ?? "",
    cacheWritePrice: model.cache_write_cost_per_million?.toString() ?? "",
    allowedRoles: [...model.allowed_roles],
    enabled: model.enabled,
    isDefault: model.is_default,
  }
}

export function modelEditRoleOptions(model: ManagedModel): string[] {
  return [...new Set(["owner", "member", ...model.allowed_roles])]
}

export function toggleModelRole(roles: string[], role: string, checked: boolean): string[] {
  return checked ? [...new Set([...roles, role])] : roles.filter((value) => value !== role)
}

export function setModelEditEnabled(draft: ModelEditDraft, enabled: boolean): ModelEditDraft {
  return { ...draft, enabled, isDefault: enabled && draft.isDefault }
}

export function setModelEditDefault(draft: ModelEditDraft, isDefault: boolean): ModelEditDraft {
  return { ...draft, isDefault, enabled: isDefault || draft.enabled }
}

export function modelEditHasChanges(initial: ModelEditDraft, draft: ModelEditDraft): boolean {
  const comparable = (value: ModelEditDraft) => ({
    ...value,
    allowedRoles: [...value.allowedRoles].sort(),
  })
  return JSON.stringify(comparable(initial)) !== JSON.stringify(comparable(draft))
}

export function validateModelEdit(draft: ModelEditDraft): ModelEditError | null {
  const name = draft.displayName.trim()
  if (!name || name.length > 255) return "display_name"
  if (draft.contextWindow.trim()
    && (!Number.isSafeInteger(Number(draft.contextWindow)) || Number(draft.contextWindow) < 1)) {
    return "context_window"
  }
  const prices = [draft.inputPrice, draft.outputPrice, draft.cacheReadPrice, draft.cacheWritePrice]
  if (prices.some((value) => value.trim() && (!Number.isFinite(Number(value)) || Number(value) < 0))) {
    return "prices"
  }
  if (draft.isDefault && !draft.enabled) return "default_disabled"
  return null
}

function optionalNumber(value: string): number | null {
  return value.trim() ? Number(value) : null
}

export function modelEditPayload(model: ManagedModel, draft: ModelEditDraft) {
  const error = validateModelEdit(draft)
  if (error) throw new Error(error)
  return {
    provider_id: model.provider_id,
    runtime_id: model.runtime_id,
    model_key: model.model_key,
    upstream_model_id: model.upstream_model_id,
    family_key: model.family_key,
    assignment_required: model.assignment_required,
    capabilities: [...model.capabilities],
    display_name: draft.displayName.trim(),
    context_window: optionalNumber(draft.contextWindow),
    input_cost_per_million: optionalNumber(draft.inputPrice),
    output_cost_per_million: optionalNumber(draft.outputPrice),
    cached_cost_per_million: optionalNumber(draft.cacheReadPrice),
    cache_write_cost_per_million: optionalNumber(draft.cacheWritePrice),
    allowed_roles: [...draft.allowedRoles],
    enabled: draft.enabled,
    is_default: draft.isDefault,
  }
}