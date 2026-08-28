import { Save, X } from "lucide-react"
import { useState } from "react"

import type {
  BrandKey,
  ModelConnectionCreate,
  ModelConnectionUpdate,
  ModelRegistry,
  ModelRuntime,
} from "../../data-sources/apim/types"
import {
  GatewayBrandLogo,
  ProviderBrandLogo,
  gatewayBrandFromIdentity,
  providerBrandFromMetadata,
} from "../brand-logos"
import { Button } from "../ui/button"
import { Checkbox } from "../ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog"
import { Input } from "../ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select"
import {
  FoundryAuthModeSwitch,
  type FoundryAuthMode,
} from "./foundry-auth-mode-switch"
import { FieldHelp } from "./field-help"

type ConnectionProviderOption = {
  value: string
  name: string
  brandKey: BrandKey
  existingId?: string
}

function providerOptionBrand(option: ConnectionProviderOption) {
  return providerBrandFromMetadata(option.brandKey, option.name)
}

function isFoundryProjectEndpoint(value: string) {
  try {
    const endpoint = new URL(value.trim())
    return endpoint.protocol === "https:"
      && endpoint.hostname.endsWith(".services.ai.azure.com")
      && /^\/api\/projects\/[a-zA-Z0-9._-]+\/?$/.test(endpoint.pathname)
      && !endpoint.search
      && !endpoint.hash
  } catch {
    return false
  }
}

function isMatchingFoundryInferenceEndpoint(projectValue: string, inferenceValue: string) {
  try {
    const project = new URL(projectValue.trim())
    const inference = new URL(inferenceValue.trim())
    const account = project.hostname.replace(/\.services\.ai\.azure\.com$/i, "")
    return Boolean(account)
      && inference.protocol === "https:"
      && [
        `${account}.openai.azure.com`,
        `${account}.services.ai.azure.com`,
      ].includes(inference.hostname.toLowerCase())
      && inference.pathname.replace(/\/$/, "") === "/openai/v1"
      && !inference.search
      && !inference.hash
  } catch {
    return false
  }
}

function isBedrockRuntimeUrl(value: string) {
  try {
    const endpoint = new URL(value.trim())
    return endpoint.protocol === "https:"
      && endpoint.hostname.startsWith("bedrock-runtime.")
      && endpoint.hostname.endsWith(".amazonaws.com")
      && (endpoint.pathname === "/" || endpoint.pathname === "")
      && !endpoint.search
      && !endpoint.hash
  } catch {
    return false
  }
}

export function ConnectionDialog({
  registry,
  runtime,
  busy,
  error,
  onClose,
  onCreate,
  onUpdate,
}: {
  registry: ModelRegistry
  runtime?: ModelRuntime
  busy: boolean
  error: string | null
  onClose: () => void
  onCreate: (value: ModelConnectionCreate) => void
  onUpdate: (value: ModelConnectionUpdate) => void
}) {
  const editing = Boolean(runtime)
  const runtimeProvider = runtime
    ? registry.providers.find((provider) => provider.id === runtime.provider_id)
    : undefined
  const providers = runtimeProvider
    ? [runtimeProvider]
    : registry.providers.filter(
        (provider) => provider.enabled
          && ["microsoft_foundry", "amazon_bedrock"].includes(provider.brand_key),
      )
  const providerOptions: ConnectionProviderOption[] = [
    ...providers.map((provider) => ({
      value: provider.id,
      name: provider.name,
      brandKey: provider.brand_key as ConnectionProviderOption["brandKey"],
      existingId: provider.id,
    })),
    ...(runtime ? [] : (["microsoft_foundry", "amazon_bedrock"] as const)
      .filter((brandKey) => !providers.some((provider) => provider.brand_key === brandKey))
      .map((brandKey) => ({
        value: `template:${brandKey}`,
        name: brandKey === "microsoft_foundry" ? "Microsoft Foundry" : "Amazon Bedrock",
        brandKey,
      }))),
  ]
  const runtimeGateway = runtime
    ? registry.gateways.find((gateway) => gateway.id === runtime.gateway_profile_id)
    : undefined
  const gateways = runtime
    ? runtimeGateway ? [runtimeGateway] : []
    : registry.gateways.filter(
        (gateway) => gateway.enabled && gateway.implementation === "apim",
      )
  const [providerValue, setProviderValue] = useState(
    runtime?.provider_id
      ?? providerOptions.find((provider) => provider.brandKey === "microsoft_foundry")?.value
      ?? providerOptions[0]?.value
      ?? "",
  )
  const [gatewayId, setGatewayId] = useState(
    runtime?.gateway_profile_id
      ?? gateways.find((gateway) => gateway.is_default)?.id
      ?? gateways[0]?.id
      ?? "",
  )
  const [foundryAuthMode, setFoundryAuthMode] = useState<FoundryAuthMode>(
    runtime?.config.auth_strategy === "named_value_api_key"
      ? "api_key"
      : "managed_identity",
  )
  const [projectEndpoint, setProjectEndpoint] = useState(
    typeof runtime?.config.project_endpoint === "string"
      ? runtime.config.project_endpoint
      : "",
  )
  const [inferenceEndpoint, setInferenceEndpoint] = useState(
    typeof runtime?.config.inference_endpoint === "string"
      ? runtime.config.inference_endpoint
      : "",
  )
  const [bedrockRuntimeUrl, setBedrockRuntimeUrl] = useState(
    typeof runtime?.config.backend_url === "string" ? runtime.config.backend_url : "",
  )
  const [connectionName, setConnectionName] = useState(runtime?.name ?? "")
  const [enabled, setEnabled] = useState(runtime?.enabled ?? true)
  const [isDefault, setIsDefault] = useState(runtime?.is_default ?? false)
  const [formError, setFormError] = useState<string | null>(null)

  const provider = providerOptions.find((item) => item.value === providerValue)
  const gateway = gateways.find((item) => item.id === gatewayId)
  const foundry = provider?.brandKey === "microsoft_foundry"
    || runtime?.runtime_kind === "foundry"
  const bedrock = provider?.brandKey === "amazon_bedrock"
    || runtime?.config.api_format === "anthropic_messages"

  const chooseFoundryAuthMode = (mode: FoundryAuthMode) => {
    setFoundryAuthMode(mode)
    if (mode === "managed_identity") setInferenceEndpoint("")
    setFormError(null)
  }

  const submit = () => {
    if (runtime) {
      if (!connectionName.trim()) return setFormError("请输入连接名称。")
      setFormError(null)
      onUpdate({
        name: connectionName.trim(),
        enabled,
        is_default: isDefault,
      })
      return
    }
    if (!provider) return setFormError("请选择提供方。")
    const templateBrand = provider.brandKey === "microsoft_foundry"
      || provider.brandKey === "amazon_bedrock"
      ? provider.brandKey
      : null
    if (!templateBrand) {
      return setFormError("此提供方不支持托管连接创建。")
    }
    if (!gateway) return setFormError("没有可用的 Azure API Management 网关。")
    if (foundry && !isFoundryProjectEndpoint(projectEndpoint)) {
      return setFormError("请输入有效的 Foundry Project Endpoint。")
    }
    if (
      foundry
      && foundryAuthMode === "api_key"
      && !isMatchingFoundryInferenceEndpoint(projectEndpoint, inferenceEndpoint)
    ) {
      return setFormError("请输入与 Project 属于同一 Foundry 资源的 Inference Endpoint。")
    }
    if (bedrock && !isBedrockRuntimeUrl(bedrockRuntimeUrl)) {
      return setFormError("请输入有效的 Bedrock Runtime URL。")
    }
    setFormError(null)
    onCreate({
      gateway_profile_id: gateway.id,
      provider: provider.existingId
        ? { existing_id: provider.existingId }
        : { template: templateBrand },
      auth_mode: foundry ? foundryAuthMode : undefined,
      foundry_project_endpoint: foundry ? projectEndpoint.trim() : undefined,
      foundry_inference_endpoint: foundry && foundryAuthMode === "api_key"
        ? inferenceEndpoint.trim()
        : undefined,
      bedrock_runtime_url: bedrock ? bedrockRuntimeUrl.trim() : undefined,
    })
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent className="registry-editor-dialog connection-dialog" finalFocus={false}>
      <div className="registry-editor connection-form">
        <DialogHeader className="registry-editor-header">
          <DialogTitle>{editing ? "编辑连接" : "添加连接"}</DialogTitle>
        </DialogHeader>
        <button type="button" className="registry-editor-close" onClick={onClose} disabled={busy} aria-label="关闭"><X size={16} /></button>

        <div className="registry-editor-body connection-dialog-body">
          <div className="form-grid">
            <div className="registry-field">
              <span className="registry-field-label-row"><span className="registry-field-label">提供方</span>{editing && <FieldHelp>提供方、APIM 网关、Endpoint 和认证方式共同定义已发布路由。要更换连接身份，请添加新连接并迁移模型。</FieldHelp>}</span>
              <Select value={providerValue} onValueChange={(value) => { if (value) { setProviderValue(value); setFormError(null) } }} disabled={busy || editing}>
                <SelectTrigger className="registry-select-trigger" aria-label="提供方">
                  <SelectValue>{provider ? <span className="registry-option"><ProviderBrandLogo brand={providerOptionBrand(provider)} size={15} /><span>{provider.name}</span></span> : "选择提供方"}</SelectValue>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {providerOptions.map((item) => <SelectItem key={item.value} value={item.value}><span className="registry-option"><ProviderBrandLogo brand={providerOptionBrand(item)} size={15} /><span>{item.name}</span></span></SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="registry-field">
              <span className="registry-field-label">APIM 网关</span>
              {editing && !runtimeGateway ? <Input value="本地 CLI / 直连" disabled /> : <Select value={gatewayId} onValueChange={(value) => { if (value) { setGatewayId(value); setFormError(null) } }} disabled={busy || editing}>
                <SelectTrigger className="registry-select-trigger" aria-label="APIM 网关">
                  <SelectValue>{gateway ? <span className="registry-option"><GatewayBrandLogo brand={gatewayBrandFromIdentity(`${gateway.name} ${gateway.implementation}`)} size={15} /><span>{gateway.name}</span></span> : "选择 APIM 网关"}</SelectValue>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {gateways.map((item) => <SelectItem key={item.id} value={item.id}><span className="registry-option"><GatewayBrandLogo brand={gatewayBrandFromIdentity(`${item.name} ${item.implementation}`)} size={15} /><span>{item.name}</span></span></SelectItem>)}
                </SelectContent>
              </Select>}
            </div>
          </div>

          {editing && <label className="registry-field"><span className="registry-field-label">连接名称</span><Input value={connectionName} onChange={(event) => setConnectionName(event.target.value)} disabled={busy} /></label>}

          {foundry && <>
            <label className="registry-field"><span className="registry-field-label">Project Endpoint</span><Input type="url" value={projectEndpoint} onChange={(event) => setProjectEndpoint(event.target.value)} disabled={busy || editing} placeholder="https://contoso-ai.services.ai.azure.com/api/projects/finops" /></label>
            <div className="registry-field">
              <span className="registry-field-label-row"><span className="registry-field-label">认证方式</span><FieldHelp>{foundryAuthMode === "api_key" ? "此处不收 API Key。添加首个模型时只需提供一次，后续模型复用此连接无需重复提供。" : "新连接将显示为等待首次模型验证；添加首个模型时通过候选 Revision 完成验证。"}</FieldHelp></span>
              <FoundryAuthModeSwitch value={foundryAuthMode} disabled={busy || editing} onChange={chooseFoundryAuthMode} />
            </div>
            {foundryAuthMode === "api_key" && <label className="registry-field"><span className="registry-field-label">Inference Endpoint</span><Input type="url" value={inferenceEndpoint} onChange={(event) => setInferenceEndpoint(event.target.value)} disabled={busy || editing} placeholder="https://contoso-ai.openai.azure.com/openai/v1" /></label>}
          </>}

          {bedrock && <>
            <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="bedrock-runtime-url">Bedrock Runtime URL</label><FieldHelp>首次为此连接添加模型时，再提供一次性 API Key。</FieldHelp></span><Input id="bedrock-runtime-url" type="url" value={bedrockRuntimeUrl} onChange={(event) => setBedrockRuntimeUrl(event.target.value)} disabled={busy || editing} placeholder="https://bedrock-runtime.ap-southeast-2.amazonaws.com" /></div>
          </>}

          {editing && <div className="form-switches"><div className="registry-checkbox-field"><Checkbox id="connection-enabled" checked={enabled} onCheckedChange={(checked) => { const next = checked === true; setEnabled(next); if (!next) setIsDefault(false) }} disabled={busy} /><label htmlFor="connection-enabled">启用</label></div><div className="registry-checkbox-field"><Checkbox id="connection-default" checked={isDefault} onCheckedChange={(checked) => { const next = checked === true; setIsDefault(next); if (next) setEnabled(true) }} disabled={busy} /><label htmlFor="connection-default">设为默认</label></div></div>}

          {(formError || error) && <div className="registry-error">{formError ?? error}</div>}
        </div>

        <DialogFooter className="registry-editor-footer">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>取消</Button>
          <Button type="button" onClick={submit} disabled={busy || !providerOptions.length || (!editing && !gateways.length)}>{editing && <Save size={14} />}{busy ? "正在保存" : editing ? "保存更改" : "添加连接"}</Button>
        </DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
}
