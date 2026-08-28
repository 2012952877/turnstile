import { ArrowLeft, CheckCircle2, CircleAlert, ExternalLink, Network, Search, Server, X } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"

import type { ManagedModel } from "../../data-sources/apim/types"
import { DeploymentResilienceEditor } from "./deployment-resilience-editor"
import { Button } from "../ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog"

export type NativeRouteModelState = "available" | "active" | "replica" | "ineligible" | "loading" | "error"

export type NativeRouteModelOption = {
  model: ManagedModel
  upstreamDeployment: string
  equivalentRuntimeNames: string[]
  missingDeploymentRuntimeNames: string[]
  replicaOwnerName: string | null
  state: NativeRouteModelState
}

const STATE_ORDER: Record<NativeRouteModelState, number> = {
  available: 0,
  ineligible: 1,
  replica: 2,
  active: 3,
  loading: 4,
  error: 5,
}

function optionStatus(option: NativeRouteModelOption) {
  if (option.state === "available") return <><span>可添加</span><i>·</i><span>{option.equivalentRuntimeNames.length} Runtime</span></>
  if (option.state === "active") return "已添加"
  if (option.state === "replica") return <><span>副本</span><i>·</i><span data-no-localize>{option.replicaOwnerName}</span></>
  if (option.state === "loading") return "正在读取后端池状态"
  if (option.state === "error") return "无法读取后端池状态"
  return option.missingDeploymentRuntimeNames.length ? "缺少 Deployment" : "缺少兼容 Runtime"
}

export function ApimNativeRouteCreateDialog({
  open,
  options,
  onClose,
  onCreated,
  onManageModels,
  onManageRuntimes,
}: {
  open: boolean
  options: NativeRouteModelOption[]
  onClose: () => void
  onCreated: (modelId: string) => void
  onManageModels: () => void
  onManageRuntimes: () => void
}) {
  const [search, setSearch] = useState("")
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null)
  const selectedOption = options.find((option) => option.model.id === selectedModelId) ?? null
  const selectedModel = selectedOption?.state === "available" ? selectedOption.model : null
  const visibleOptions = useMemo(() => {
    const normalized = search.trim().toLocaleLowerCase()
    return [...options]
      .filter(({ model }) => !normalized || `${model.display_name} ${model.model_key} ${model.runtime_name}`.toLocaleLowerCase().includes(normalized))
      .sort((left, right) => STATE_ORDER[left.state] - STATE_ORDER[right.state]
        || left.model.model_key.localeCompare(right.model.model_key))
  }, [options, search])

  useEffect(() => {
    if (open) return
    setSearch("")
    setSelectedModelId(null)
  }, [open])

  useEffect(() => {
    if (!selectedModelId || selectedOption) return
    setSelectedModelId(null)
  }, [selectedModelId, selectedOption])

  const finishCreation = useCallback(() => {
    if (selectedModelId) onCreated(selectedModelId)
  }, [onCreated, selectedModelId])

  return <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose() }}>
    <DialogContent className="registry-editor-dialog apim-native-route-create-dialog" finalFocus={false}>
      <div className="registry-editor">
        <DialogHeader className="registry-editor-header">
          <DialogTitle>添加后端池</DialogTitle>
          <DialogDescription>{selectedOption ? <span data-no-localize>{selectedOption.model.display_name} · {selectedOption.model.model_key}</span> : "从已有模型中选择并配置 APIM 后端池。"}</DialogDescription>
        </DialogHeader>
        <button type="button" className="registry-editor-close" onClick={onClose} aria-label="关闭"><X size={16} /></button>
        <div className="registry-editor-body apim-native-route-create-body">
          {selectedOption ? <>
            <button type="button" className="apim-native-route-create-back" onClick={() => setSelectedModelId(null)}><ArrowLeft size={14} />选择其他模型</button>
            <div className="apim-native-route-create-selection">
              <span><Server size={16} /></span>
              <div><b data-no-localize title={selectedOption.model.display_name}>{selectedOption.model.display_name}</b><code data-no-localize title={selectedOption.model.model_key}>{selectedOption.model.model_key}</code></div>
            </div>
            {selectedModel ? <DeploymentResilienceEditor key={selectedModel.id} model={selectedModel} onActivated={finishCreation} /> : <div className="apim-native-route-readiness">
              <div className="apim-native-route-readiness-state"><CircleAlert size={16} /><span><b>尚不能创建后端池</b><small>{selectedOption.state === "replica" ? "该 Deployment 已作为物理副本使用。" : selectedOption.missingDeploymentRuntimeNames.length ? "需要先在另一个兼容 Runtime 上登记同名上游 Deployment。" : "需要先创建另一个兼容的受管 APIM Runtime。"}</small></span></div>
              <dl className="apim-native-route-readiness-facts">
                <div><dt>上游 Deployment</dt><dd data-no-localize>{selectedOption.upstreamDeployment}</dd></div>
                <div><dt>当前 Runtime</dt><dd data-no-localize>{selectedOption.model.runtime_name}</dd></div>
                <div><dt>已登记部署</dt><dd>{selectedOption.equivalentRuntimeNames.length}</dd></div>
                {selectedOption.state === "replica" && <div><dt>所属后端池</dt><dd data-no-localize>{selectedOption.replicaOwnerName}</dd></div>}
              </dl>
              {selectedOption.missingDeploymentRuntimeNames.length > 0 && <div className="apim-native-route-readiness-targets"><b>兼容目标 Runtime</b>{selectedOption.missingDeploymentRuntimeNames.map((name) => <span data-no-localize key={name}>{name}</span>)}</div>}
              {selectedOption.state !== "replica" && <Button className="router-action-primary apim-native-route-readiness-action" type="button" onClick={selectedOption.missingDeploymentRuntimeNames.length ? onManageModels : onManageRuntimes}><ExternalLink size={14} />{selectedOption.missingDeploymentRuntimeNames.length ? "前往模型管理" : "前往连接管理"}</Button>}
            </div>}
          </> : <>
            <label className="smh-search apim-native-route-create-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索已有模型..." aria-label="搜索可添加后端池的模型" /></label>
            <div className="apim-native-route-create-list" role="list" aria-label="已有模型列表">
              {visibleOptions.map((option) => {
                const selectable = option.state === "available" || option.state === "ineligible" || option.state === "replica"
                const StatusIcon = option.state === "active" || option.state === "replica" ? CheckCircle2 : option.state === "available" ? Network : CircleAlert
                return <div role="listitem" key={option.model.id}>
                  <button type="button" data-state={option.state} disabled={!selectable} onClick={() => setSelectedModelId(option.model.id)}>
                    <span className="apim-native-route-create-icon"><StatusIcon size={15} /></span>
                    <span className="apim-native-route-create-copy"><b data-no-localize title={option.model.display_name}>{option.model.display_name}</b><code data-no-localize title={option.model.model_key}>{option.model.model_key}</code></span>
                    <span className="apim-native-route-create-status">{optionStatus(option)}</span>
                  </button>
                </div>
              })}
              {!visibleOptions.length && <div className="smh-empty"><Network size={26} /><b>没有匹配的模型</b><span>尝试调整搜索条件。</span></div>}
            </div>
          </>}
        </div>
        <DialogFooter className="registry-editor-footer"><Button className="apim-native-route-create-cancel" type="button" variant="outline" onClick={onClose}>取消</Button></DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
}