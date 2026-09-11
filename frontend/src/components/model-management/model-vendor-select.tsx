import { Database } from "lucide-react"

import type { ModelVendorKey } from "../../data-sources/apim/types"
import { ClaudeLogo, DeepSeekLogo, KimiLogo, OpenAiLogo } from "../brand-logos"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select"
import { MODEL_VENDOR_OPTIONS, modelVendorLabel } from "./openai-compatible"

export function ModelVendorLogo({ value, size = 16 }: { value: ModelVendorKey; size?: number }) {
  if (value === "kimi") return <KimiLogo size={size} />
  if (value === "deepseek") return <DeepSeekLogo size={size} />
  if (value === "openai") return <OpenAiLogo size={size} />
  if (value === "anthropic") return <ClaudeLogo size={size} />
  return <Database aria-hidden="true" size={size} />
}

export function ModelVendorSelect({ value, disabled, onChange }: {
  value: ModelVendorKey
  disabled?: boolean
  onChange: (value: ModelVendorKey) => void
}) {
  return <Select value={value} onValueChange={(next) => {
    const option = MODEL_VENDOR_OPTIONS.find((item) => item.value === next)
    if (option) onChange(option.value)
  }} disabled={disabled}>
    <SelectTrigger className="registry-select-trigger" aria-label="API 服务商">
      <SelectValue><span className="registry-option"><ModelVendorLogo value={value} size={15} /><span>{modelVendorLabel(value)}</span></span></SelectValue>
    </SelectTrigger>
    <SelectContent align="start" alignItemWithTrigger={false}>
      {MODEL_VENDOR_OPTIONS.map((option) => <SelectItem key={option.value} value={option.value}>
        <span className="registry-option"><ModelVendorLogo value={option.value} size={15} /><span>{option.label}</span></span>
      </SelectItem>)}
    </SelectContent>
  </Select>
}