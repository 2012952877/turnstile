import type { ModelRegistry, ModelRuntime } from "../data-sources/apim/types"

export type RuntimeAccessPoint = {
  id: string
  title: string
  section: "gateway" | "direct"
  runtimes: ModelRuntime[]
  subtitle: string
}

/** Group runtimes by the channel through which callers actually reach them. */
export function runtimeAccessPoints(registry: ModelRegistry): RuntimeAccessPoint[] {
  const grouped = new Map<string, ModelRuntime[]>()
  registry.runtimes.forEach((runtime) => {
    const key = runtime.gateway_profile_id
      ? `gateway:${runtime.gateway_profile_id}`
      : `provider:${runtime.provider_id}`
    grouped.set(key, [...(grouped.get(key) ?? []), runtime])
  })
  return [...grouped].map(([id, runtimes]) => {
    const viaGateway = id.startsWith("gateway:")
    return {
      id,
      title: (viaGateway ? runtimes[0]?.gateway_name : runtimes[0]?.provider_name)
        ?? "未命名接入来源",
      section: viaGateway ? "gateway" : "direct",
      runtimes,
      subtitle: viaGateway ? "模型网关" : "直连提供方",
    } satisfies RuntimeAccessPoint
  })
}