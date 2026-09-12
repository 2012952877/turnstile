import { LoaderCircle, Save, X } from "lucide-react"
import { useId, useState, type FormEvent } from "react"

import type { ManagedModel, ModelRegistry } from "../../data-sources/apim/types"
import {
  GatewayBrandLogo,
  ProviderBrandLogo,
  gatewayBrandFromIdentity,
  providerBrandFromMetadata,
} from "../brand-logos"
import { Button } from "../ui/button"
import { Checkbox } from "../ui/checkbox"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../ui/dialog"
import { Input } from "../ui/input"
import { FieldHelp } from "./field-help"
import { ModelVendorLogo } from "./model-vendor-select"
import { modelVendorFromMetadata, modelVendorLabel } from "./openai-compatible"
import {
  createModelEditDraft,
  modelEditHasChanges,
  modelEditPayload,
  modelEditRoleOptions,
  setModelEditDefault,
  setModelEditEnabled,
  toggleModelRole,
  validateModelEdit,
  type ModelEditDraft,
  type ModelEditError,
} from "./model-edit-form"

const VALIDATION_MESSAGES: Record<ModelEditError, string> = {
  display_name: "请输入 1 至 255 个字符的显示名称。",
  context_window: "上下文窗口必须是正整数。",
  prices: "价格必须是非负有限数值。",
  default_disabled: "默认模型必须启用。",
}

function ModelEditNumberField({ id, label, value, onChange, busy, integer = false, help }: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  busy: boolean
  integer?: boolean
  help?: string
}) {
  return <div className="registry-field">
    <div className="registry-field-label-row">
      <label htmlFor={id} className="registry-field-label">{label}</label>
      {help && <FieldHelp>{help}</FieldHelp>}
    </div>
    <Input id={id} type="number" inputMode={integer ? "numeric" : "decimal"}
      min={integer ? 1 : 0} step={integer ? 1 : "any"} value={value}
      onChange={(event) => onChange(event.target.value)} disabled={busy} />
  </div>
}

export function ModelEditDialog({ registry, model, busy, error, onClose, onSave }: {
  registry: ModelRegistry
  model: ManagedModel
  busy: boolean
  error: string | null
  onClose: () => void
  onSave: (value: ReturnType<typeof modelEditPayload>) => void
}) {
  const id = useId()
  const [initial] = useState(() => createModelEditDraft(model))
  const [draft, setDraft] = useState<ModelEditDraft>(initial)
  const [submitted, setSubmitted] = useState(false)
  const runtime = registry.runtimes.find((item) => item.id === model.runtime_id)
  const provider = registry.providers.find((item) => item.id === model.provider_id)
  const vendor = runtime && provider?.provider_kind === "openai_compatible"
    ? modelVendorFromMetadata(runtime.config, runtime.name)
    : null
  const gateway = registry.gateways.find((item) => item.id === runtime?.gateway_profile_id)
  const upstreamLabel = runtime?.brand_key === "microsoft_foundry" ? "Deployment Name"
    : runtime?.brand_key === "amazon_bedrock" ? "Model / Inference Profile ID" : "上游模型 ID"
  const roleOptions = modelEditRoleOptions(model)
  const imageGeneration = model.capabilities.includes("image_generation")
  const dirty = modelEditHasChanges(initial, draft)
  const validation = validateModelEdit(draft)
  const imagePriceMissing = imageGeneration && [draft.inputPrice, draft.cacheReadPrice, draft.outputPrice].some(value => !value.trim())
  const message = (dirty || submitted) && imagePriceMissing ? "请填写文字输入、缓存文字和图像输出单价。"
    : validation && (dirty || submitted) ? VALIDATION_MESSAGES[validation] : error
  const update = <Key extends keyof ModelEditDraft>(key: Key, value: ModelEditDraft[Key]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }
  const close = () => { if (!busy) onClose() }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSubmitted(true)
    if (busy || !dirty || validation) return
    if (imagePriceMissing) return
    onSave(modelEditPayload(model, draft))
  }

  return <Dialog open onOpenChange={(open) => { if (!open) close() }}>
    <DialogContent className="registry-editor-dialog simple-model-dialog model-edit-dialog">
      <form className="registry-editor simple-model-form" onSubmit={submit} aria-busy={busy}>
        <DialogHeader className="registry-editor-header">
          <DialogTitle>编辑模型</DialogTitle>
          <DialogDescription className="sr-only">模型配置</DialogDescription>
        </DialogHeader>
        <Button type="button" variant="ghost" size="icon-sm" className="registry-editor-close"
          onClick={close} disabled={busy} aria-label="关闭"><X size={16} /></Button>
        <div className="registry-editor-body simple-model-body">
          <section className="simple-model-section" aria-labelledby={`${id}-identity`}>
            <div className="simple-section-title"><b id={`${id}-identity`}>模型身份</b></div>
            <dl className="model-editor-identity">
              <div><dt>目标网关</dt><dd className="registry-option">
                {gateway && <GatewayBrandLogo brand={gatewayBrandFromIdentity(`${gateway.name} ${gateway.implementation}`)} size={15} />}
                <span data-no-localize>{gateway?.name ?? runtime?.gateway_name ?? "-"}</span>
              </dd></div>
              <div><dt>提供方</dt><dd className="registry-option">
                <ProviderBrandLogo brand={providerBrandFromMetadata(runtime?.brand_key ?? provider?.brand_key, model.provider_name)} size={15} />
                <span data-no-localize>{model.provider_name}</span>
              </dd></div>
              {vendor && <div><dt>API 服务商</dt><dd className="registry-option"><ModelVendorLogo value={vendor} size={15} /><span>{modelVendorLabel(vendor)}</span></dd></div>}
              <div className="model-editor-identity-wide"><dt>连接</dt><dd data-no-localize>{runtime?.name ?? model.runtime_name}</dd></div>
              <div><dt>模型别名</dt><dd data-no-localize><code>{model.model_key}</code></dd></div>
              <div><dt>{upstreamLabel}</dt><dd data-no-localize><code>{model.upstream_model_id ?? "-"}</code></dd></div>
            </dl>
          </section>
          <section className="simple-model-section model-editor-section" aria-labelledby={`${id}-basic`}>
            <div className="simple-section-title"><b id={`${id}-basic`}>基本信息</b></div>
            <label className="registry-field">
              <span className="registry-field-label">显示名称</span>
              <Input value={draft.displayName} onChange={(event) => update("displayName", event.target.value)}
                required maxLength={255} disabled={busy} aria-invalid={validation === "display_name" || undefined} />
            </label>
          </section>
          <section className="simple-model-section model-editor-section" aria-labelledby={`${id}-pricing`}>
            <div className="publication-section-heading">
              <span className="simple-section-title"><b id={`${id}-pricing`}>价格与限制</b></span>
              <span className="model-editor-unit" data-no-localize>USD / 1M Tokens</span>
              {!imageGeneration && <FieldHelp>留空表示未配置，不等于 0。</FieldHelp>}
            </div>
            <div className={imageGeneration ? "form-grid" : "form-grid three"}>
              {!imageGeneration && <ModelEditNumberField id={`${id}-context`} label="上下文窗口" integer value={draft.contextWindow} onChange={(value) => update("contextWindow", value)} busy={busy} />}
              <ModelEditNumberField id={`${id}-input`} label={imageGeneration ? "文字输入单价" : "输入单价"} value={draft.inputPrice} onChange={(value) => update("inputPrice", value)} busy={busy} />
              <ModelEditNumberField id={`${id}-output`} label={imageGeneration ? "图像输出单价" : "输出单价"} value={draft.outputPrice} onChange={(value) => update("outputPrice", value)} busy={busy} />
            </div>
            <div className="form-grid">
              <ModelEditNumberField id={`${id}-cache-read`} label={imageGeneration ? "缓存文字单价" : "缓存读取单价"} value={draft.cacheReadPrice} onChange={(value) => update("cacheReadPrice", value)} busy={busy} help={imageGeneration ? undefined : "留空按输入单价计费；填写 0 表示免费。"} />
              {!imageGeneration && <ModelEditNumberField id={`${id}-cache-write`} label="缓存写入单价" value={draft.cacheWritePrice} onChange={(value) => update("cacheWritePrice", value)} busy={busy} help="留空按缓存读取单价计费；读取单价也未填写时按输入单价。填写 0 表示免费。" />}
            </div>
          </section>
          <section className="simple-model-section model-editor-section" aria-labelledby={`${id}-access`}>
            <div className="simple-section-title"><b id={`${id}-access`}>状态与访问</b></div>
            <div className="model-editor-state">
              <label className="model-editor-checkbox"><Checkbox checked={draft.enabled} disabled={busy}
                onCheckedChange={(checked) => setDraft((current) => setModelEditEnabled(current, checked))} /><span>启用</span></label>
              <label className="model-editor-checkbox"><Checkbox checked={draft.isDefault} disabled={busy || imageGeneration}
                onCheckedChange={(checked) => setDraft((current) => setModelEditDefault(current, checked))} /><span>设为默认</span></label>
            </div>
            <details className="model-editor-advanced">
              <summary>高级访问限制</summary>
              <fieldset className="model-editor-choices" disabled={busy}>
                <legend className="registry-field-label">允许角色</legend>
                <div className="model-editor-checkbox-grid">
                  {roleOptions.map((role) => <label key={role} className="model-editor-checkbox">
                    <Checkbox checked={draft.allowedRoles.includes(role)} disabled={busy}
                      onCheckedChange={(checked) => update("allowedRoles", toggleModelRole(draft.allowedRoles, role, checked))} />
                    <span data-no-localize>{role === "owner" ? "Owner" : role === "member" ? "Member" : role}</span>
                    {role !== "owner" && role !== "member" && <small>兼容角色</small>}
                  </label>)}
                </div>
              </fieldset>
              {draft.allowedRoles.length === 0 && <p className="model-editor-access-warning" role="status">未选择角色；此模型将无法通过应用的角色检查。</p>}
            </details>
          </section>
          {message && <div className="registry-error" role="alert">{message}</div>}
        </div>
        <DialogFooter className="registry-editor-footer">
          <Button type="button" variant="outline" className="publication-dismiss" onClick={close} disabled={busy}>取消</Button>
          <Button type="submit" disabled={busy || !dirty || Boolean(validation) || imagePriceMissing}>
            {busy ? <LoaderCircle className="spin" size={14} /> : <Save size={14} />}{busy ? "正在保存" : "保存更改"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}