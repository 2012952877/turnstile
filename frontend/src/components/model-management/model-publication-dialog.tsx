import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Circle, Copy, Eye, EyeOff, LoaderCircle, Rocket, ShieldCheck, TriangleAlert, X } from "lucide-react"

import { dataSource } from "../../data-sources/apim/api"
import { finopsKeys, finopsQueries } from "../../data-sources/apim/queries"
import type {
  BrandKey,
  GatewayPublication,
  GatewayPublicationCreate,
  GatewayPublicationStatus,
  ModelProvider,
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog"
import { Input } from "../ui/input"
import { Progress } from "../ui/progress"
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

const NEW_BEDROCK_PROVIDER = "template:amazon-bedrock"
const NEW_FOUNDRY_PROVIDER = "template:microsoft-foundry"
const NEW_RUNTIME = "new-runtime"
const TERMINAL_STATUSES: GatewayPublicationStatus[] = [
  "active",
  "failed",
  "rolled_back",
  "superseded",
]

const PUBLICATION_STAGE_STATUSES: GatewayPublicationStatus[] = [
  "validating",
  "provisioning",
  "building_revision",
  "verifying",
  "promoting",
  "active",
]

function publicationStageLabels() {
  return [
    "验证配置",
    "准备后端资源",
    "构建 APIM Revision",
    "验证候选 Revision",
    "切换 APIM Revision",
    "完成",
  ]
}

function publicationStageIndex(status: GatewayPublicationStatus) {
  if (status === "queued") return 0
  if (status === "awaiting_authorization") return 3
  const index = PUBLICATION_STAGE_STATUSES.indexOf(status)
  return index < 0 ? null : index
}

function publicationTitle(kind: GatewayPublication["publication_kind"]) {
  if (kind === "model_remove") return "移除模型"
  if (kind === "credential_rotation") return "更新 API Key"
  if (kind === "route_reconcile") return "刷新网关路由"
  return "发布模型"
}

function PublicationProgress({ publication }: { publication: GatewayPublication }) {
  const stageIndex = publicationStageIndex(publication.status)
  const active = publication.status === "active"
  const completed = active || publication.status === "superseded"
  const failed = publication.status === "failed" || publication.status === "rolled_back"
  const paused = publication.status === "awaiting_authorization"
  const labels = publicationStageLabels()

  const progress = completed ? 100 : stageIndex === null ? 0 : ((stageIndex + 1) / labels.length) * 100

  return <section className="publication-progress" aria-label="模型发布配置进度">
    <div className="publication-progress-head">
      <span>发布进度</span>
      <b>{completed ? "6 / 6" : stageIndex === null ? "—" : `${stageIndex + 1} / 6`}</b>
    </div>
    {stageIndex !== null || completed ? <>
    <Progress value={progress} />
    <ol className="publication-stage-list">
      {labels.map((label, index) => {
        const complete = completed || (stageIndex !== null && index < stageIndex)
        const current = !completed && !failed && stageIndex === index
        return <li key={label} data-state={complete ? "complete" : current ? paused ? "paused" : "current" : failed && stageIndex === index ? "failed" : "pending"}>
          <span className="publication-stage-mark" aria-hidden="true">
            {complete ? <Check size={12} /> : current ? paused ? <ShieldCheck size={12} /> : <LoaderCircle className="spin" size={12} /> : failed && stageIndex === index ? <TriangleAlert size={12} /> : <Circle size={8} />}
          </span>
          <span>{label}</span>
        </li>
      })}
    </ol>
    </> : <p className="publication-progress-unavailable">发布未完成，当前 Revision 未受影响。</p>}
  </section>
}

type ProviderChoice = {
  id: string
  name: string
  brandKey: BrandKey
  provider?: ModelProvider
}

function choiceBrand(choice: ProviderChoice) {
  return providerBrandFromMetadata(
    choice.brandKey,
    `${choice.name} ${choice.provider?.provider_kind ?? ""}`,
  )
}

function numberOrNull(value: string) {
  const text = value.trim()
  return text ? Number(text) : null
}

function optionalNumberError(values: string[]) {
  const numeric = values.filter((value) => value.trim()).map(Number)
  return numeric.some((value) => !Number.isFinite(value) || value < 0)
    ? "上下文窗口和价格必须是非负数字。"
    : null
}

type BedrockModel = {
  displayName: string
  modelKey: string
  upstreamModelId: string
}

function isBedrockRuntimeUrl(value: string) {
  try {
    const endpoint = new URL(value.trim())
    return endpoint.protocol === "https:"
      && endpoint.hostname.startsWith("bedrock-runtime.")
      && endpoint.hostname.endsWith(".amazonaws.com")
      && (endpoint.pathname === "/" || endpoint.pathname === "")
  } catch {
    return false
  }
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

function deriveBedrockModel(value: string): BedrockModel | null {
  const upstreamModelId = value.trim()
  if (!upstreamModelId || upstreamModelId.length > 2048) return null
  const resourceName = upstreamModelId.split("/").at(-1) ?? upstreamModelId
  if (!resourceName.toLowerCase().includes("claude")) return null
  const modelName = resourceName
    .replace(/^(?:[a-z]{2}|apac|global)\.anthropic\./i, "")
    .replace(/^anthropic\./i, "")
  const parts = modelName.split("-").filter(Boolean)
  const versionStart = parts.findIndex((part) => /^\d/.test(part))
  const family = (versionStart < 0 ? parts : parts.slice(0, versionStart))
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
  const version = versionStart < 0 ? "" : parts.slice(versionStart).join(".")
  return {
    displayName: `${family}${version ? ` ${version}` : ""} · Amazon Bedrock`,
    modelKey: `${modelName.toLowerCase().replace(/[^a-z0-9._:-]+/g, "-")}-bedrock`,
    upstreamModelId,
  }
}

function publicationRuntimeEligible(runtime: ModelRuntime) {
  if (runtime.brand_key === "microsoft_foundry") {
    return runtime.config.control_plane_managed === true
      && typeof runtime.config.project_endpoint === "string"
      && runtime.config.project_endpoint.length > 0
  }
  return runtime.config.api_format === "anthropic_messages"
    || runtime.config.api_format === "openai_chat"
    || runtime.brand_key === "amazon_bedrock"
    || runtime.brand_key === "azure_databricks"
}

function runtimeRegion(runtime: ModelRuntime) {
  if (typeof runtime.config.region === "string" && runtime.config.region.trim()) {
    return runtime.config.region.trim()
  }
  if (typeof runtime.config.backend_url !== "string") return null
  try {
    const hostname = new URL(runtime.config.backend_url).hostname
    const match = hostname.match(/^bedrock-runtime\.([^.]+)\.amazonaws\.com$/i)
    return match?.[1] ?? null
  } catch {
    return null
  }
}

function runtimeChoiceLabel(runtime: ModelRuntime) {
  const region = runtimeRegion(runtime)
  return region ? `${runtime.name} · ${region}` : runtime.name
}

function foundryRuntimeChoiceLabel(runtime: ModelRuntime) {
  return `复用已有连接 · ${runtime.name}`
}

function providerChoices(registry: ModelRegistry, runtimes: ModelRuntime[]): ProviderChoice[] {
  const runtimeProviderIds = new Set(runtimes.map((runtime) => runtime.provider_id))
  const existing: ProviderChoice[] = registry.providers
    .filter((provider) => runtimeProviderIds.has(provider.id)
      || provider.brand_key === "amazon_bedrock"
      || provider.brand_key === "microsoft_foundry")
    .map((provider) => ({
      id: provider.id,
      name: provider.name,
      brandKey: provider.brand_key,
      provider,
    }))

  if (!registry.providers.some((provider) => provider.brand_key === "amazon_bedrock")) {
    existing.push({
      id: NEW_BEDROCK_PROVIDER,
      name: "Amazon Bedrock",
      brandKey: "amazon_bedrock",
    })
  }
  if (!registry.providers.some((provider) => provider.brand_key === "microsoft_foundry")) {
    existing.push({
      id: NEW_FOUNDRY_PROVIDER,
      name: "Microsoft Foundry",
      brandKey: "microsoft_foundry",
    })
  }
  return existing
}

export function publicationStatusLabel(status: GatewayPublicationStatus) {
  return {
    queued: "等待发布",
    validating: "正在验证配置",
    provisioning: "正在准备后端",
    building_revision: "正在构建 APIM Revision",
    verifying: "正在验证模型",
    awaiting_authorization: "等待 Foundry 授权",
    promoting: "正在切换 APIM Revision",
    active: "模型已发布",
    failed: "发布失败",
    superseded: "已被后续发布替代",
    rolling_back: "正在回滚",
    rolled_back: "已回滚",
  }[status]
}

export function ModelPublicationDialog({
  registry,
  publicationId,
  onPublicationQueued,
  onClose,
}: {
  registry: ModelRegistry
  publicationId: string | null
  onPublicationQueued: (publicationId: string) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const apimGateways = registry.gateways.filter(
    (gateway) => gateway.implementation === "apim" && gateway.enabled,
  )
  const [gatewayId, setGatewayId] = useState(
    apimGateways.find((gateway) => gateway.is_default)?.id
      ?? apimGateways[0]?.id
      ?? "",
  )
  const selectedGateway = apimGateways.find((gateway) => gateway.id === gatewayId)
  const allApimRuntimes = registry.runtimes.filter(
    (runtime) => runtime.enabled && publicationRuntimeEligible(runtime) && apimGateways.some(
      (gateway) => gateway.id === runtime.gateway_profile_id,
    ),
  )
  const apimRuntimes = registry.runtimes.filter(
    (runtime) => runtime.gateway_profile_id === selectedGateway?.id
      && runtime.enabled
      && publicationRuntimeEligible(runtime),
  )
  const allChoices = providerChoices(registry, allApimRuntimes)
  const choices = allChoices
  const [providerChoiceId, setProviderChoiceId] = useState(
    choices.find((choice) => choice.brandKey === "microsoft_foundry")?.id
      ?? choices[0]?.id
      ?? "",
  )
  const selectedChoice = choices.find((choice) => choice.id === providerChoiceId)
  const providerRuntimes = apimRuntimes.filter(
    (runtime) => runtime.provider_id === selectedChoice?.provider?.id,
  )
  const canCreateRuntime = selectedChoice?.brandKey === "amazon_bedrock"
    || selectedChoice?.brandKey === "microsoft_foundry"
  const [runtimeId, setRuntimeId] = useState(
    canCreateRuntime ? NEW_RUNTIME : providerRuntimes[0]?.id ?? "",
  )
  const selectedRuntime = registry.runtimes.find((runtime) => runtime.id === runtimeId)
  const creatingRuntime = runtimeId === NEW_RUNTIME
  const creatingBedrockConnection = creatingRuntime && selectedChoice?.brandKey === "amazon_bedrock"
  const creatingFoundryConnection = creatingRuntime && selectedChoice?.brandKey === "microsoft_foundry"
  const selectedRuntimeNeedsCredential = !creatingRuntime
    && selectedRuntime?.config.credential_provisioned === false
    && ["named_value_bearer", "named_value_api_key"].includes(
      String(selectedRuntime.config.auth_strategy ?? ""),
    )

  const [bedrockRuntimeUrl, setBedrockRuntimeUrl] = useState("")
  const [bedrockModelId, setBedrockModelId] = useState("")
  const [bedrockApiKey, setBedrockApiKey] = useState("")
  const [foundryProjectEndpoint, setFoundryProjectEndpoint] = useState("")
  const [foundryAuthMode, setFoundryAuthMode] = useState<FoundryAuthMode>("managed_identity")
  const [foundryInferenceEndpoint, setFoundryInferenceEndpoint] = useState("")
  const [foundryApiKey, setFoundryApiKey] = useState("")
  const [foundryDeployment, setFoundryDeployment] = useState("")
  const [keyRevealed, setKeyRevealed] = useState(false)
  const [modelKey, setModelKey] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [upstreamModelId, setUpstreamModelId] = useState("")
  const [contextWindow, setContextWindow] = useState("")
  const [inputPrice, setInputPrice] = useState("")
  const [outputPrice, setOutputPrice] = useState("")
  const [cacheReadPrice, setCacheReadPrice] = useState("")
  const [cacheWritePrice, setCacheWritePrice] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [publishError, setPublishError] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false)
  const activationNotified = useRef(false)
  const requiredCredentialMissing = (
    selectedRuntimeNeedsCredential
    || creatingBedrockConnection
    || (creatingFoundryConnection && foundryAuthMode === "api_key")
  ) && !(selectedChoice?.brandKey === "microsoft_foundry"
    ? foundryApiKey
    : bedrockApiKey
  ).trim()

  const publication = useQuery(finopsQueries.gatewayPublication(publicationId))

  useEffect(() => {
    if (publication.data?.status !== "active" || activationNotified.current) return
    activationNotified.current = true
    void queryClient.invalidateQueries({ queryKey: finopsKeys.registry })
  }, [publication.data?.status, queryClient])

  const chooseGateway = (id: string | null) => {
    if (!id) return
    const gatewayRuntimes = registry.runtimes.filter(
      (runtime) => runtime.gateway_profile_id === id && runtime.enabled,
    )
    const nextChoice = choices.find((choice) => choice.id === providerChoiceId)
      ?? choices[0]
    const nextRuntimes = gatewayRuntimes.filter(
      (runtime) => runtime.provider_id === nextChoice?.provider?.id,
    )
    setGatewayId(id)
    setProviderChoiceId(nextChoice?.id ?? "")
    setRuntimeId(
      nextChoice?.brandKey === "amazon_bedrock" || nextChoice?.brandKey === "microsoft_foundry"
        ? NEW_RUNTIME
        : nextRuntimes[0]?.id ?? "",
    )
    setFormError(null)
  }

  const chooseProvider = (id: string | null) => {
    if (!id) return
    const choice = choices.find((item) => item.id === id)
    const runtimes = apimRuntimes.filter(
      (runtime) => runtime.provider_id === choice?.provider?.id,
    )
    setProviderChoiceId(id)
    setRuntimeId(
      choice?.brandKey === "amazon_bedrock" || choice?.brandKey === "microsoft_foundry"
        ? NEW_RUNTIME
        : runtimes[0]?.id ?? "",
    )
    setFormError(null)
  }

  const chooseFoundryAuthMode = (mode: FoundryAuthMode) => {
    setFoundryAuthMode(mode)
    if (mode === "managed_identity") {
      setFoundryInferenceEndpoint("")
      setFoundryApiKey("")
      setKeyRevealed(false)
    }
    setFormError(null)
  }

  const validate = () => {
    if (!selectedGateway) return "没有可用的 Azure API Management 网关。"
    if (!selectedChoice) return "请选择提供方。"
    if (!runtimeId) return "请选择使用 APIM 的运行时。"
    if (creatingBedrockConnection) {
      if (!isBedrockRuntimeUrl(bedrockRuntimeUrl)) return "请输入有效的 Bedrock Runtime URL。"
      if (!deriveBedrockModel(bedrockModelId)) return "请输入有效的 Claude Model / Inference Profile ID。"
      if (!bedrockApiKey.trim()) return "请输入 Bedrock API Key。"
      return null
    }
    if (selectedRuntimeNeedsCredential) {
      const credential = selectedChoice.brandKey === "microsoft_foundry"
        ? foundryApiKey
        : bedrockApiKey
      if (!credential.trim()) return "请输入该连接首次发布所需的一次性 API Key。"
    }
    if (selectedChoice.brandKey === "microsoft_foundry") {
      if (creatingFoundryConnection && !isFoundryProjectEndpoint(foundryProjectEndpoint)) {
        return "请输入有效的 Foundry Project Endpoint。"
      }
      if (creatingFoundryConnection && foundryAuthMode === "api_key") {
        if (!isMatchingFoundryInferenceEndpoint(
          foundryProjectEndpoint,
          foundryInferenceEndpoint,
        )) return "请输入与 Project 属于同一 Foundry 资源的 Inference Endpoint。"
        if (!foundryApiKey.trim()) return "请输入 Foundry API Key。"
      }
      if (!foundryDeployment.trim()) return "请输入已有的 Foundry Deployment Name。"
      return optionalNumberError([
        contextWindow, inputPrice, outputPrice, cacheReadPrice, cacheWritePrice,
      ])
    }
    if (!/^[a-zA-Z0-9._:-]+$/.test(modelKey.trim())) return "模型 Key 格式无效。"
    if (!displayName.trim()) return "请输入显示名称。"
    if (!upstreamModelId.trim()) return "请输入上游模型 ID。"
    return optionalNumberError([
      contextWindow, inputPrice, outputPrice, cacheReadPrice, cacheWritePrice,
    ])
  }

  const submit = async () => {
    const error = validate()
    setFormError(error)
    setPublishError(null)
    if (error || !selectedGateway || !selectedChoice) return
    const parsedBedrock = creatingBedrockConnection
      ? deriveBedrockModel(bedrockModelId)
      : null
    if (creatingBedrockConnection && !parsedBedrock) return

    const request: GatewayPublicationCreate = {
      gateway_profile_id: selectedGateway.id,
      provider: selectedChoice.provider
        ? { existing_id: selectedChoice.provider.id }
        : {
            template: selectedChoice.brandKey === "microsoft_foundry"
              ? "microsoft_foundry"
              : "amazon_bedrock",
          },
      runtime: creatingRuntime
        ? creatingFoundryConnection ? {
            foundry_project_endpoint: foundryProjectEndpoint.trim(),
            foundry_inference_endpoint: foundryAuthMode === "api_key"
              ? foundryInferenceEndpoint.trim()
              : undefined,
            api_key: foundryAuthMode === "api_key" ? foundryApiKey.trim() : undefined,
          } : {
            bedrock_runtime_url: bedrockRuntimeUrl.trim(),
            api_key: bedrockApiKey.trim(),
          }
        : {
            existing_id: runtimeId,
            api_key: selectedRuntimeNeedsCredential
              ? selectedChoice.brandKey === "microsoft_foundry"
                ? foundryApiKey.trim()
                : bedrockApiKey.trim()
              : undefined,
          },
      model: {
        deployment_name: selectedChoice.brandKey === "microsoft_foundry"
          ? foundryDeployment.trim()
          : undefined,
        model_key: selectedChoice.brandKey === "microsoft_foundry"
          ? undefined
          : parsedBedrock?.modelKey ?? modelKey.trim(),
        display_name: selectedChoice.brandKey === "microsoft_foundry"
          ? undefined
          : parsedBedrock?.displayName ?? displayName.trim(),
        upstream_model_id: selectedChoice.brandKey === "microsoft_foundry"
          ? undefined
          : parsedBedrock?.upstreamModelId ?? upstreamModelId.trim(),
        context_window: parsedBedrock ? null : numberOrNull(contextWindow),
        input_cost_per_million: parsedBedrock ? null : numberOrNull(inputPrice),
        output_cost_per_million: parsedBedrock ? null : numberOrNull(outputPrice),
        cached_cost_per_million: parsedBedrock ? null : numberOrNull(cacheReadPrice),
        cache_write_cost_per_million: parsedBedrock ? null : numberOrNull(cacheWritePrice),
      },
    }
    setPublishing(true)
    try {
      const accepted = await dataSource.publishModel(request)
      setBedrockApiKey("")
      setFoundryApiKey("")
      setKeyRevealed(false)
      queryClient.setQueryData(
        finopsKeys.gatewayPublication(accepted.publication.id),
        accepted.publication,
      )
      onPublicationQueued(accepted.publication.id)
    } catch (requestError) {
      setPublishError(String(requestError))
    } finally {
      setPublishing(false)
    }
  }

  const status = publication.data?.status
  const terminal = status ? TERMINAL_STATUSES.includes(status) : false
  const failed = status === "failed"
  const awaitingAuthorization = status === "awaiting_authorization"
  const active = status === "active"
  const superseded = status === "superseded"
  const rolledBack = status === "rolled_back"
  const dialogTitle = publication.data
    ? publicationTitle(publication.data.publication_kind)
    : publicationId
      ? "发布详情"
      : "添加模型"

  const retry = async () => {
    if (!publicationId) return
    const retryRequiresCredential = publication.data?.retry_requires_credential ?? false
    if (retryRequiresCredential && !bedrockApiKey.trim()) return
    setPublishing(true)
    setPublishError(null)
    try {
      const queued = await dataSource.retryGatewayPublication(
        publicationId,
        retryRequiresCredential ? bedrockApiKey.trim() : undefined,
      )
      setBedrockApiKey("")
      setKeyRevealed(false)
      queryClient.setQueryData(
        finopsKeys.gatewayPublication(publicationId),
        queued,
      )
      void queryClient.invalidateQueries({ queryKey: finopsKeys.gatewayPublications })
      onPublicationQueued(publicationId)
    } catch (requestError) {
      setPublishError(String(requestError))
    } finally {
      setPublishing(false)
    }
  }

  const resumeAuthorization = async () => {
    if (!publicationId) return
    setPublishing(true)
    setPublishError(null)
    try {
      const verifying = await dataSource.resumeGatewayPublicationAuthorization(publicationId)
      queryClient.setQueryData(finopsKeys.gatewayPublication(publicationId), verifying)
      onPublicationQueued(publicationId)
    } catch (requestError) {
      setPublishError(String(requestError))
    } finally {
      setPublishing(false)
    }
  }

  const copyAuthorization = async () => {
    const authorization = publication.data?.authorization
    if (!authorization) return
    try {
      await navigator.clipboard.writeText([
        `Principal ID: ${authorization.principal_id}`,
        `Role: ${authorization.role_name} (${authorization.role_id})`,
        `Project Endpoint: ${authorization.resource_endpoint}`,
      ].join("\n"))
    } catch (error) {
      setPublishError(String(error))
    }
  }

  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
    <DialogContent className="registry-editor-dialog simple-model-dialog" finalFocus={false}>
      <div className="registry-editor simple-model-form">
        <DialogHeader className="registry-editor-header">
          <DialogTitle>{dialogTitle}</DialogTitle>
          {publication.data && <DialogDescription data-no-localize>{`${publication.data.display_name} · ${publication.data.model_key}`}</DialogDescription>}
        </DialogHeader>
        <button type="button" className="registry-editor-close" onClick={onClose} aria-label="关闭"><X size={16} /></button>

        <div className="registry-editor-body simple-model-body">
          {publicationId && publication.isPending && <div className="publication-detail-loading"><LoaderCircle className="spin" size={16} />加载发布详情</div>}
          {publicationId && publication.isError && <div className="publication-detail-error">
            <div className="registry-error">无法加载发布详情：{String(publication.error)}</div>
            <Button type="button" variant="outline" onClick={() => void publication.refetch()}>重新加载</Button>
          </div>}
          {publication.data && <>
            <PublicationProgress publication={publication.data} />
            {!superseded && <div className={`publication-safety-note ${failed || rolledBack ? "failed" : awaitingAuthorization ? "warning" : active ? "active" : "running"}`}>
              {failed || rolledBack ? <TriangleAlert size={15} /> : awaitingAuthorization ? <ShieldCheck size={15} /> : active ? <Check size={15} /> : <ShieldCheck size={15} />}
              <div>
                <span className="publication-safety-title"><b>{failed || rolledBack ? "当前模型继续正常服务" : awaitingAuthorization ? "当前模型继续正常服务" : active ? publication.data.publication_kind === "model_remove" ? "模型已从 Turnstile 移除" : "发布完成" : "现有模型继续使用当前 Revision"}</b>{!failed && <FieldHelp>{rolledBack ? "当前模型继续正常服务" : awaitingAuthorization ? "发布时使用 APIM Managed Identity。若尚未授权，发布会停在等待授权，不会切换当前 Revision。" : active ? publication.data.publication_kind === "model_remove" ? "历史用量与上游模型保留。" : "模型已发布，分配人员后即可使用" : "发布过程不会改变当前 Revision，验证通过后才切换。"}</FieldHelp>}</span>
                {failed && <span className="publication-safety-error">{publication.data.error_message ?? "发布未完成，请使用发布 ID 查看服务日志。"}</span>}
              </div>
            </div>}
          </>}

          {awaitingAuthorization && publication.data?.authorization && <div className="simple-model-section simple-connection-section">
            <div className="simple-section-title"><b>授权 APIM 访问 Foundry 资源</b><FieldHelp>请在该 Foundry 资源的 Access control (IAM) 中，将以下 APIM Managed Identity 添加为 Cognitive Services User。Turnstile 不需要 Foundry API Key。</FieldHelp></div>
            <dl className="simple-authorization-list">
              <div><dt>Principal ID</dt><dd>{publication.data.authorization.principal_id}</dd></div>
              <div><dt>Role</dt><dd>{publication.data.authorization.role_name} · {publication.data.authorization.role_id}</dd></div>
              <div><dt>Foundry 资源</dt><dd>{publication.data.authorization.resource_endpoint}</dd></div>
            </dl>
            <Button type="button" variant="outline" onClick={() => void copyAuthorization()}><Copy size={14} />复制授权信息</Button>
          </div>}

          {failed && publication.data?.retry_requires_credential && <div className="simple-model-section simple-connection-section">
            <div className="simple-section-title"><b>重新发布</b></div>
            <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="retry-api-key">Provider API Key</label><FieldHelp>请输入新的 API Key 后重新发布同一模型。</FieldHelp></span><div className="login-password"><Input id="retry-api-key" type={keyRevealed ? "text" : "password"} autoComplete="off" value={bedrockApiKey} onChange={(event) => setBedrockApiKey(event.target.value)} /><button type="button" className="login-reveal" onClick={() => setKeyRevealed((value) => !value)} aria-label={keyRevealed ? "隐藏 API Key" : "显示 API Key"} aria-pressed={keyRevealed}>{keyRevealed ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></div>
          </div>}

          {!publicationId && <>
          <div className="simple-model-section">
            <div className="registry-field">
              <span className="registry-field-label">提供方</span>
              <Select value={providerChoiceId} onValueChange={chooseProvider} disabled={Boolean(status)}>
                <SelectTrigger className="registry-select-trigger" aria-label="提供方">
                  <SelectValue>{selectedChoice ? <span className="registry-option"><ProviderBrandLogo brand={choiceBrand(selectedChoice)} size={15} /><span>{selectedChoice.name}</span></span> : "选择提供方"}</SelectValue>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {choices.map((choice) => <SelectItem key={choice.id} value={choice.id}><span className="registry-option"><ProviderBrandLogo brand={choiceBrand(choice)} size={15} /><span>{choice.name}</span></span></SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          {canCreateRuntime && <div className="simple-model-section">
            <div className="registry-field">
              <span className="registry-field-label">运行时</span>
              <Select value={runtimeId} onValueChange={(value) => value && setRuntimeId(value)} disabled={Boolean(status)}>
                <SelectTrigger className="registry-select-trigger" aria-label="运行时">
                  <SelectValue>{creatingRuntime ? creatingFoundryConnection ? "连接新的 Foundry Project" : "新建区域 Runtime（填写 URL 与 API Key）" : selectedRuntime ? selectedChoice?.brandKey === "microsoft_foundry" ? foundryRuntimeChoiceLabel(selectedRuntime) : runtimeChoiceLabel(selectedRuntime) : "选择运行时"}</SelectValue>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  <SelectItem value={NEW_RUNTIME}>{selectedChoice?.brandKey === "microsoft_foundry" ? "连接新的 Foundry Project" : "新建区域 Runtime（填写 URL 与 API Key）"}</SelectItem>
                  {providerRuntimes.map((runtime) => <SelectItem key={runtime.id} value={runtime.id}>{selectedChoice?.brandKey === "microsoft_foundry" ? foundryRuntimeChoiceLabel(runtime) : runtimeChoiceLabel(runtime)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>}

          {creatingBedrockConnection && <div className="simple-model-section simple-connection-section">
            <div className="simple-section-title"><b>Bedrock 模型</b></div>
            <label className="registry-field"><span className="registry-field-label">Bedrock Runtime URL</span><Input type="url" value={bedrockRuntimeUrl} onChange={(event) => setBedrockRuntimeUrl(event.target.value)} disabled={Boolean(status)} placeholder="https://bedrock-runtime.ap-southeast-2.amazonaws.com" /></label>
            <label className="registry-field"><span className="registry-field-label">Model / Inference Profile ID</span><Input value={bedrockModelId} onChange={(event) => setBedrockModelId(event.target.value)} disabled={Boolean(status)} placeholder="au.anthropic.claude-sonnet-4-6" /></label>
            <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="bedrock-api-key">Bedrock API Key</label><FieldHelp>短期 API Key 最长有效 12 小时，当前发布不会自动续期。</FieldHelp></span><div className="login-password"><Input id="bedrock-api-key" type={keyRevealed ? "text" : "password"} autoComplete="off" value={bedrockApiKey} onChange={(event) => setBedrockApiKey(event.target.value)} disabled={Boolean(status)} /><button type="button" className="login-reveal" onClick={() => setKeyRevealed((value) => !value)} aria-label={keyRevealed ? "隐藏 API Key" : "显示 API Key"} aria-pressed={keyRevealed} disabled={Boolean(status)}>{keyRevealed ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></div>
            {deriveBedrockModel(bedrockModelId) && <div className="simple-bedrock-preview"><ProviderBrandLogo brand="bedrock" size={17} /><span><b>{deriveBedrockModel(bedrockModelId)?.displayName}</b><small>{deriveBedrockModel(bedrockModelId)?.modelKey}</small></span></div>}
          </div>}

          {creatingFoundryConnection && <div className="simple-model-section simple-connection-section">
            <div className="simple-section-title"><b>连接新的 Foundry Project</b></div>
            <label className="registry-field"><span className="registry-field-label">Project Endpoint</span><Input type="url" value={foundryProjectEndpoint} onChange={(event) => setFoundryProjectEndpoint(event.target.value)} disabled={Boolean(status)} placeholder="https://contoso-ai.services.ai.azure.com/api/projects/finops" /></label>
            <div className="registry-field">
              <span className="registry-field-label">认证方式</span>
              <FoundryAuthModeSwitch value={foundryAuthMode} disabled={Boolean(status)} onChange={chooseFoundryAuthMode} />
            </div>
            {foundryAuthMode === "api_key" && <>
              <label className="registry-field"><span className="registry-field-label">Inference Endpoint</span><Input type="url" value={foundryInferenceEndpoint} onChange={(event) => setFoundryInferenceEndpoint(event.target.value)} disabled={Boolean(status)} placeholder="https://contoso-ai.openai.azure.com/openai/v1" /></label>
              <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="foundry-api-key">Foundry API Key</label><FieldHelp>Key 仅用于创建 APIM Secret Named Value，不会写入模型注册表或发布详情。</FieldHelp></span><div className="login-password"><Input id="foundry-api-key" type={keyRevealed ? "text" : "password"} autoComplete="off" value={foundryApiKey} onChange={(event) => setFoundryApiKey(event.target.value)} disabled={Boolean(status)} /><button type="button" className="login-reveal" onClick={() => setKeyRevealed((value) => !value)} aria-label={keyRevealed ? "隐藏 API Key" : "显示 API Key"} aria-pressed={keyRevealed} disabled={Boolean(status)}>{keyRevealed ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></div>
            </>}
            <label className="registry-field"><span className="registry-field-label">Deployment Name</span><Input value={foundryDeployment} onChange={(event) => setFoundryDeployment(event.target.value)} disabled={Boolean(status)} placeholder="gpt-5-mini" /></label>
          </div>}

          {!creatingRuntime && selectedChoice?.brandKey === "microsoft_foundry" && <div className="simple-model-section simple-connection-section">
            <div className="simple-section-title"><b>复用已有的 Foundry Project</b><FieldHelp>Project Endpoint、认证方式与 APIM 路由来自已有连接；这里只选择该 Project 中已经存在的 Deployment。</FieldHelp></div>
            <label className="registry-field"><span className="registry-field-label">Deployment Name</span><Input value={foundryDeployment} onChange={(event) => setFoundryDeployment(event.target.value)} disabled={Boolean(status)} placeholder="gpt-5.4" /></label>
            {selectedRuntimeNeedsCredential && <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="existing-foundry-api-key">一次性 API Key</label><FieldHelp>仅首次发布需要。Key 写入 APIM Secret Named Value 后即从 Turnstile 临时记录中清除；后续模型无需重复提供。</FieldHelp></span><div className="login-password"><Input id="existing-foundry-api-key" type={keyRevealed ? "text" : "password"} autoComplete="off" value={foundryApiKey} onChange={(event) => setFoundryApiKey(event.target.value)} disabled={Boolean(status)} /><button type="button" className="login-reveal" onClick={() => setKeyRevealed((value) => !value)} aria-label={keyRevealed ? "隐藏 API Key" : "显示 API Key"} aria-pressed={keyRevealed} disabled={Boolean(status)}>{keyRevealed ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></div>}
          </div>}

          {selectedRuntimeNeedsCredential && selectedChoice?.brandKey !== "microsoft_foundry" && <div className="simple-model-section simple-connection-section">
            <div className="registry-field"><span className="registry-field-label-row"><label className="registry-field-label" htmlFor="existing-provider-api-key">一次性 API Key</label><FieldHelp>仅首次发布需要。Key 写入 APIM Secret Named Value 后即从 Turnstile 临时记录中清除；后续模型无需重复提供。</FieldHelp></span><div className="login-password"><Input id="existing-provider-api-key" type={keyRevealed ? "text" : "password"} autoComplete="off" value={bedrockApiKey} onChange={(event) => setBedrockApiKey(event.target.value)} disabled={Boolean(status)} /><button type="button" className="login-reveal" onClick={() => setKeyRevealed((value) => !value)} aria-label={keyRevealed ? "隐藏 API Key" : "显示 API Key"} aria-pressed={keyRevealed} disabled={Boolean(status)}>{keyRevealed ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></div>
          </div>}

          {!creatingBedrockConnection && selectedChoice?.brandKey !== "microsoft_foundry" && <div className="simple-model-section">
            <div className="simple-section-title"><b>模型</b></div>
            <div className="form-grid"><label className="registry-field"><span className="registry-field-label">模型 Key</span><Input value={modelKey} onChange={(event) => setModelKey(event.target.value)} disabled={Boolean(status)} placeholder="claude-sonnet-4-6-bedrock" /></label><label className="registry-field"><span className="registry-field-label">显示名称</span><Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={Boolean(status)} placeholder="Claude Sonnet 4.6" /></label></div>
            <label className="registry-field"><span className="registry-field-label">上游模型 ID</span><Input value={upstreamModelId} onChange={(event) => setUpstreamModelId(event.target.value)} disabled={Boolean(status)} placeholder="au.anthropic.claude-sonnet-4-6" /></label>
          </div>}

          {!creatingBedrockConnection && <details className="simple-pricing">
            <summary>价格与上下文</summary>
            <div className="simple-pricing-fields"><div className="form-grid three"><label className="registry-field"><span className="registry-field-label">上下文窗口</span><Input type="number" min="1" value={contextWindow} onChange={(event) => setContextWindow(event.target.value)} disabled={Boolean(status)} /></label><label className="registry-field"><span className="registry-field-label">输入 $/M</span><Input type="number" min="0" step="0.000001" value={inputPrice} onChange={(event) => setInputPrice(event.target.value)} disabled={Boolean(status)} /></label><label className="registry-field"><span className="registry-field-label">输出 $/M</span><Input type="number" min="0" step="0.000001" value={outputPrice} onChange={(event) => setOutputPrice(event.target.value)} disabled={Boolean(status)} /></label></div><div className="form-grid"><label className="registry-field"><span className="registry-field-label">缓存读取 $/M</span><Input type="number" min="0" step="0.000001" value={cacheReadPrice} onChange={(event) => setCacheReadPrice(event.target.value)} disabled={Boolean(status)} /></label><label className="registry-field"><span className="registry-field-label">缓存写入 $/M</span><Input type="number" min="0" step="0.000001" value={cacheWritePrice} onChange={(event) => setCacheWritePrice(event.target.value)} disabled={Boolean(status)} /></label></div></div>
          </details>}

          <div className="simple-model-section simple-target-section">
            <div className="simple-section-title"><b>发布目标</b></div>
            <div className={`simple-model-grid ${canCreateRuntime ? "single" : ""}`}>
              <div className="registry-field">
                <span className="registry-field-label">APIM 网关</span>
                <Select value={gatewayId} onValueChange={chooseGateway} disabled={Boolean(status)}>
                  <SelectTrigger className="registry-select-trigger" aria-label="APIM 网关">
                    <SelectValue>{selectedGateway ? <span className="registry-option"><GatewayBrandLogo brand={gatewayBrandFromIdentity(`${selectedGateway.name} ${selectedGateway.implementation}`)} size={15} /><span>{selectedGateway.name}</span></span> : "选择 APIM 网关"}</SelectValue>
                  </SelectTrigger>
                  <SelectContent align="start" alignItemWithTrigger={false}>
                    {apimGateways.map((gateway) => <SelectItem key={gateway.id} value={gateway.id}><span className="registry-option"><GatewayBrandLogo brand={gatewayBrandFromIdentity(`${gateway.name} ${gateway.implementation}`)} size={15} /><span>{gateway.name}</span></span></SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {!canCreateRuntime && <div className="registry-field">
                <span className="registry-field-label">运行时</span>
                <Select value={runtimeId} onValueChange={(value) => value && setRuntimeId(value)} disabled={Boolean(status)}>
                  <SelectTrigger className="registry-select-trigger" aria-label="运行时">
                    <SelectValue>{creatingRuntime ? "新建区域 Runtime（填写 URL 与 API Key）" : selectedRuntime ? runtimeChoiceLabel(selectedRuntime) : "选择运行时"}</SelectValue>
                  </SelectTrigger>
                  <SelectContent align="start" alignItemWithTrigger={false}>
                    {providerRuntimes.map((runtime) => <SelectItem key={runtime.id} value={runtime.id}>{runtimeChoiceLabel(runtime)}</SelectItem>)}
                    {canCreateRuntime && <SelectItem value={NEW_RUNTIME}>新建区域 Runtime（填写 URL 与 API Key）</SelectItem>}
                  </SelectContent>
                </Select>
              </div>}
            </div>
          </div>
          </>}

          {(formError || publishError) && <div className="registry-error">{formError ?? publishError}</div>}
        </div>

        <DialogFooter className="registry-editor-footer">
          <Button type="button" variant="outline" onClick={onClose}>{publicationId ? "关闭" : "取消"}</Button>
          {failed && <Button type="button" onClick={() => void retry()} disabled={publishing || Boolean(publication.data?.retry_requires_credential && !bedrockApiKey.trim())}><Rocket size={14} />重新发布</Button>}
          {awaitingAuthorization && <Button type="button" onClick={() => void resumeAuthorization()} disabled={publishing}><ShieldCheck size={14} />我已授权，重新验证</Button>}
          {!publicationId && <Button type="button" onClick={() => void submit()} disabled={publishing || requiredCredentialMissing}><Rocket size={14} />{publishing ? "正在提交" : "发布模型"}</Button>}
        </DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
}
