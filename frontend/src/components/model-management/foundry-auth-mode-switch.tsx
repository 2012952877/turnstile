import { KeyRound, ShieldCheck } from "lucide-react"

import { ButtonGroup } from "../ui/button-group"

export type FoundryAuthMode = "managed_identity" | "api_key"

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
