import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Building2, Check, Copy, Edit3, Plus, RefreshCw, RotateCcw, X } from "lucide-react"

import { Button } from "../components/ui/button"
import { Input } from "../components/ui/input"
import { ResizableGridTable } from "../components/ui/resizable-table"
import { dataSource } from "../data-sources/apim/api"
import { finopsKeys, finopsQueries } from "../data-sources/apim/queries"
import type { OrganizationDirectory, OrgUnit } from "../data-sources/apim/types"
import { useAuth } from "../providers/auth-provider"

// Mirrors `suggested_unit_id` on the server: an id is proposed where the name allows one and
// withheld where it does not. A department named in Chinese has no slug to derive, and an
// invented `department-1` would be a permanent identifier that says nothing about what it
// identifies -- so the field is simply left for the administrator to fill.
function suggestedId(displayName: string): string {
  const slug = displayName.trim().toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  return slug ? `department-${slug}`.slice(0, 63).replace(/-$/, "") : ""
}

function useDirectoryMutation(onDone?: () => void) {
  const queryClient = useQueryClient()
  return {
    onSuccess: (value: OrganizationDirectory) => {
      queryClient.setQueryData(finopsKeys.organizationDirectory, value)
      void queryClient.invalidateQueries({ queryKey: finopsKeys.entities })
      void queryClient.invalidateQueries({ queryKey: finopsKeys.gatewayApplications })
      onDone?.()
    },
  }
}

function RenameField({ unit, canManage }: { unit: OrgUnit; canManage: boolean }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(unit.display_name)
  const mutation = useMutation({
    mutationFn: () => dataSource.renameOrgUnit(unit.id, draft.trim()),
    ...useDirectoryMutation(() => setEditing(false)),
  })
  if (!editing) {
    return <span className="org-unit-name">
      <b data-no-localize>{unit.display_name}</b>
      {canManage && <Button type="button" variant="ghost" size="icon-sm" aria-label="重命名" title="重命名"
        onClick={() => { setDraft(unit.display_name); setEditing(true) }}><Edit3 size={13} /></Button>}
    </span>
  }
  return <form className="org-unit-rename" onSubmit={(event) => { event.preventDefault(); mutation.mutate() }}>
    <Input value={draft} autoFocus disabled={mutation.isPending} onChange={(event) => setDraft(event.target.value)} />
    <Button type="submit" size="icon-sm" disabled={mutation.isPending || !draft.trim()} aria-label="保存">
      {mutation.isPending ? <RefreshCw className="spin" size={13} /> : <Check size={13} />}
    </Button>
    <Button type="button" variant="ghost" size="icon-sm" disabled={mutation.isPending}
      onClick={() => setEditing(false)} aria-label="取消"><X size={13} /></Button>
  </form>
}

function DepartmentRow({ unit, canManage }: { unit: OrgUnit; canManage: boolean }) {
  const retired = unit.status === "retired"
  const mutation = useMutation({
    mutationFn: () => dataSource.setOrgUnitStatus(unit.id, retired ? "active" : "retired"),
    ...useDirectoryMutation(),
  })
  const { budgets, usage_records: usage, applications } = unit.references
  return <div className="org-row" role="row" data-retired={retired || undefined}>
    <span role="cell"><RenameField unit={unit} canManage={canManage} /></span>
    <span role="cell"><code data-no-localize>{unit.id}</code></span>
    <span role="cell">{retired ? "已停用" : "使用中"}</span>
    <span role="cell" className="org-unit-references">
      <span title="预算">{budgets}</span>
      <span title="用量记录">{usage}</span>
      <span title="通道">{applications}</span>
    </span>
    <span role="cell">
      {canManage && <Button type="button" variant="outline" size="sm" disabled={mutation.isPending}
        onClick={() => mutation.mutate()}>
        {mutation.isPending ? <RefreshCw className="spin" size={13} /> : retired ? <RotateCcw size={13} /> : <X size={13} />}
        {retired ? "恢复使用" : "停用"}
      </Button>}
    </span>
  </div>
}

function CreateDepartment() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [id, setId] = useState("")
  const [touchedId, setTouchedId] = useState(false)
  const effectiveId = touchedId ? id : suggestedId(name)
  const mutation = useMutation({
    mutationFn: () => dataSource.createDepartment({ id: effectiveId.trim(), display_name: name.trim() }),
    ...useDirectoryMutation(() => { setOpen(false); setName(""); setId(""); setTouchedId(false) }),
  })
  if (!open) {
    return <Button type="button" onClick={() => setOpen(true)}><Plus size={14} />新建部门</Button>
  }
  return <form className="org-create-form" onSubmit={(event) => { event.preventDefault(); mutation.mutate() }}>
    <label><span>部门名称</span>
      <Input value={name} autoFocus disabled={mutation.isPending} onChange={(event) => setName(event.target.value)} />
    </label>
    <label><span>部门标识</span>
      <Input value={effectiveId} disabled={mutation.isPending} placeholder="department-xxx"
        onChange={(event) => { setTouchedId(true); setId(event.target.value) }} />
      <small>标识一旦创建就不能再改：预算、用量、通道和 Entra 的角色名都靠它对上。名称随时可改。</small>
    </label>
    {mutation.error && <div className="registry-error">{String(mutation.error)}</div>}
    <div className="org-create-actions">
      <Button type="button" variant="outline" disabled={mutation.isPending} onClick={() => setOpen(false)}>取消</Button>
      <Button type="submit" disabled={mutation.isPending || !name.trim() || !effectiveId.trim()}>
        {mutation.isPending ? <RefreshCw className="spin" size={14} /> : null}创建
      </Button>
    </div>
  </form>
}

function EntraMirror({ directory }: { directory: OrganizationDirectory }) {
  const [copied, setCopied] = useState<string | null>(null)
  const map = JSON.stringify(directory.employee_department_map, null, 2)
  const copy = (label: string, text: string) => {
    void navigator.clipboard?.writeText(text)
    setCopied(label)
    window.setTimeout(() => setCopied(null), 1500)
  }
  return <section className="org-card">
    <header><h2>同步到 Entra</h2></header>
    <p className="org-card-note">部门在这里定义，Entra 里要有同名的应用角色，网关才能从令牌里读出一个人属于哪个部门。角色的值必须等于部门标识，不是名称。</p>
    <ResizableGridTable className="org-table org-role-table" role="table" aria-label="Entra 应用角色"
      headerSelector=".org-table-head" minWidths={[220, 160]} columnGap={12}>
      <div className="org-table-head" role="row">
        {["应用角色的值", "显示名"].map((label) => <span className="org-table-heading" role="columnheader" key={label}><span>{label}</span></span>)}
      </div>
      <div role="rowgroup">
        {directory.entra_app_roles.map((role) => <div className="org-row" role="row" key={role.value}>
          <span role="cell"><code data-no-localize>{role.value}</code></span>
          <span role="cell" data-no-localize>{role.display_name}</span>
        </div>)}
      </div>
    </ResizableGridTable>
    <div className="org-card-actions">
      <Button type="button" variant="outline" size="sm"
        onClick={() => copy("roles", directory.entra_app_roles.map((role) => role.value).join("\n"))}>
        <Copy size={13} />{copied === "roles" ? "已复制" : "复制角色清单"}
      </Button>
      <Button type="button" variant="outline" size="sm" onClick={() => copy("map", map)}>
        <Copy size={13} />{copied === "map" ? "已复制" : "复制部门映射表"}
      </Button>
    </div>
    <p className="org-card-note">部门映射表是部署参数 employeeDepartmentMap 的值，网关用它把标识显示成名称。</p>
  </section>
}

export function OrganizationPage() {
  const { user } = useAuth()
  const canManage = user?.role === "owner"
  const directory = useQuery(finopsQueries.organizationDirectory())
  if (directory.isLoading) {
    return <main className="org-page"><div className="org-loading"><RefreshCw className="spin" size={18} /></div></main>
  }
  const data = directory.data
  if (!data) {
    return <main className="org-page"><div className="org-loading">读取组织结构失败</div></main>
  }
  return <main className="org-page">
    <section className="org-card">
      <header><h2><Building2 size={15} />组织</h2></header>
      {data.organization
        ? <div className="org-organization">
            <RenameField unit={data.organization} canManage={canManage} />
            <code data-no-localize>{data.organization.id}</code>
          </div>
        : <p className="org-card-note">这个部署还没有组织。</p>}
      <p className="org-card-note">改名不影响任何已有数据：预算、用量和通道记录的都是标识，不是名称。</p>
    </section>

    <section className="org-card">
      <header>
        <h2>部门</h2>
        {canManage && <CreateDepartment />}
      </header>
      <ResizableGridTable className="org-table org-department-table" role="table" aria-label="部门列表"
        headerSelector=".org-table-head" minWidths={[200, 220, 90, 140, 120]} columnGap={12}>
        <div className="org-table-head" role="row">
          {["名称", "标识", "状态", "预算 / 用量 / 通道", ""].map((label, index) =>
            <span className="org-table-heading" role="columnheader" key={label || index}><span>{label}</span></span>)}
        </div>
        <div role="rowgroup">
          {data.departments.map((unit) => <DepartmentRow key={unit.id} unit={unit} canManage={canManage} />)}
        </div>
      </ResizableGridTable>
      <p className="org-card-note">部门不能删除，只能停用。停用后不再出现在任何可选项里，但已经记在它名下的预算和用量原样保留。</p>
    </section>

    <EntraMirror directory={data} />
  </main>
}
