import { KeyRound, ShieldCheck } from "lucide-react"

import { ButtonGroup } from "../ui/button-group"

export type FoundryAuthMode = "managed_identity" | "api_key"
export type DatabricksAuthMode = "managed_identity" | "oauth_m2m"

export function DatabricksAuthModeSwitch({
  value,
  disabled = false,
  oauthSupported = false,
  onChange,
}: {
  value: DatabricksAuthMode
  disabled?: boolean
  oauthSupported?: boolean
  onChange: (value: DatabricksAuthMode) => void
}) {
  return <ButtonGroup className="foundry-auth-mode" aria-label="Databricks 认证方式">
    <button type="button" aria-pressed={value === "managed_identity"} onClick={() => onChange("managed_identity")} disabled={disabled}><ShieldCheck size={15} /><span><b>Managed Identity</b><small>同一 Microsoft Entra 租户</small></span></button>
    <button type="button" aria-pressed={value === "oauth_m2m"} onClick={() => onChange("oauth_m2m")} disabled={disabled || !oauthSupported}><KeyRound size={15} /><span><b>OAuth M2M</b><small>{oauthSupported ? "Databricks 服务主体" : "尚未启用"}</small></span></button>
  </ButtonGroup>
}

export function FoundryAuthModeSwitch({
  value,
  disabled = false,
  onChange,
}: {
  value: FoundryAuthMode
  disabled?: boolean
  onChange: (value: FoundryAuthMode) => void
}) {
  return <ButtonGroup className="foundry-auth-mode" aria-label="Foundry 认证方式">
    <button type="button" aria-pressed={value === "managed_identity"} onClick={() => onChange("managed_identity")} disabled={disabled}><ShieldCheck size={15} /><span><b>Managed Identity</b><small>同租户 · 仅 Project Endpoint</small></span></button>
    <button type="button" aria-pressed={value === "api_key"} onClick={() => onChange("api_key")} disabled={disabled}><KeyRound size={15} /><span><b>API Key</b><small>跨租户 · Project + Inference Endpoint</small></span></button>
  </ButtonGroup>
}
