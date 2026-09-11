import type {
  GatewayPublicationCreate,
  ModelProvider,
  ModelRegistry,
  ModelRuntime,
} from "../../data-sources/apim/types"

export function publicationRuntimeEligible(runtime: ModelRuntime): boolean {
  if (runtime.brand_key === "microsoft_foundry") {
    return runtime.config.control_plane_managed === true
      && typeof runtime.config.project_endpoint === "string"
      && runtime.config.project_endpoint.trim().length > 0
  }
  return runtime.config.api_format === "anthropic_messages"
    || runtime.config.api_format === "openai_chat"
    || runtime.brand_key === "amazon_bedrock"
    || runtime.brand_key === "azure_databricks"
}

export function publicationConnections(registry: ModelRegistry, gatewayId: string): ModelRuntime[] {
  const gateway = registry.gateways.find((item) => item.id === gatewayId)
  if (!gateway?.enabled || gateway.implementation !== "apim") return []
  const providers = new Set(registry.providers.filter((item) => item.enabled).map((item) => item.id))
  return registry.runtimes.filter((runtime) => runtime.gateway_profile_id === gatewayId
    && runtime.enabled
    && providers.has(runtime.provider_id)
    && publicationRuntimeEligible(runtime))
}

export function preferredPublicationConnection(
  connections: ModelRuntime[],
  preferredId?: string,
): ModelRuntime | undefined {
  return connections.find((item) => item.id === preferredId)
    ?? connections.find((item) => item.is_default)
    ?? connections[0]
}

export function publicationConnectionAuth(runtime: ModelRuntime, provider?: ModelProvider) {
  const strategy = runtime.config.auth_strategy
  if (strategy === "named_value_bearer" || strategy === "named_value_api_key") return "api_key"
  if (strategy === "managed_identity") return "managed_identity"
  if (provider?.auth_type === "azure_ad") return "managed_identity"
  if (provider?.auth_type === "api_key") return "api_key"
  return "connection"
}

export function publicationConnectionEndpoint(runtime: ModelRuntime): string | null {
  for (const key of ["project_endpoint", "base_url", "backend_url"]) {
    const value = runtime.config[key]
    if (typeof value !== "string" || !value.trim()) continue
    try {
      const endpoint = new URL(value)
      if (endpoint.protocol !== "https:") continue
      return `${endpoint.origin}${endpoint.pathname.replace(/\/$/, "")}`
    } catch {
      continue
    }
  }
  return null
}

export function publicationConnectionNeedsCredential(runtime: ModelRuntime): boolean {
  return runtime.config.credential_provisioned === false
    && ["named_value_bearer", "named_value_api_key"].includes(String(runtime.config.auth_strategy ?? ""))
}

export function publicationConnectionTarget(
  registry: ModelRegistry,
  gatewayId: string,
  runtimeId: string,
  apiKey = "",
): Pick<GatewayPublicationCreate, "gateway_profile_id" | "provider" | "runtime"> {
  const runtime = publicationConnections(registry, gatewayId).find((item) => item.id === runtimeId)
  if (!runtime) throw new Error("connection_unavailable")
  const needsCredential = publicationConnectionNeedsCredential(runtime)
  if (needsCredential && !apiKey.trim()) throw new Error("credential_required")
  return {
    gateway_profile_id: gatewayId,
    provider: { existing_id: runtime.provider_id },
    runtime: {
      existing_id: runtime.id,
      ...(needsCredential ? { api_key: apiKey.trim() } : {}),
    },
  }
}