import { LoaderCircle, RefreshCw, Search } from "lucide-react"
import { useEffect, useState } from "react"

import { dataSource } from "../../data-sources/apim/api"
import type { ManagedModel, PriceCatalogEntry } from "../../data-sources/apim/types"
import { Button } from "../ui/button"
import { Input } from "../ui/input"
import { FieldHelp } from "./field-help"
import {
  applyCatalogEntry,
  discountedRate,
  resolveDiscount,
  type ModelEditDraft,
} from "./model-edit-form"

const SOURCE_LABEL: Record<string, string> = {
  manual: "手工填写",
  azure_retail: "Azure 零售价",
  anthropic: "Anthropic 列表价",
}

const SYNC_STATUS_LABEL: Record<string, string> = {
  ok: "已同步",
  unmapped: "未映射",
  stale: "来源读取失败，保留上次单价",
  review_needed: "官方价变动较大，待复核",
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return `$${value.toFixed(value < 1 ? 4 : 2)}`
}

/**
 * Lets a person point a model at a published price and see what that turns into.
 *
 * The arithmetic is shown rather than just its result. A rate that appears without explanation
 * is a number the reader has to trust; `list x discount = charged`, spelled out, is one they can
 * check. That matters more here than saving a line, because a wrong rate is invisible until an
 * invoice disagrees weeks later.
 */
export function ModelPriceSourceFields({ model, draft, setDraft, busy, connectionDiscount }: {
  model: ManagedModel
  draft: ModelEditDraft
  setDraft: (updater: (current: ModelEditDraft) => ModelEditDraft) => void
  busy: boolean
  connectionDiscount: number | null
}) {
  // The display name usually reads "gpt-4.1-mini · Microsoft Foundry"; only the part before the
  // separator resembles anything a vendor's price list calls a model, so that is what the box
  // opens with. Searching the whole string finds nothing and makes the feature look broken.
  const [query, setQuery] = useState(() => model.display_name.split("·")[0].trim())
  const [results, setResults] = useState<PriceCatalogEntry[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const following = draft.priceSource !== "manual"
  const discount = resolveDiscount(draft, connectionDiscount)

  // Re-pricing on a discount change keeps the four rates honest while the dialog is open;
  // without it the box would say 90% while the rates below still showed the old figures.
  useEffect(() => {
    if (!following || !results) return
    const chosen = results.find((entry) => entry.reference === draft.priceReference)
    if (chosen) setDraft((current) => applyCatalogEntry(current, chosen, discount.percent))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.discountPercent])

  const search = async () => {
    setSearching(true)
    setSearchError(null)
    try {
      const response = await dataSource.priceCatalog(query)
      setResults(response.entries)
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "价目表读取失败")
    } finally {
      setSearching(false)
    }
  }

  return <div className="model-price-source">
    <div className="registry-field-label-row">
      <span className="registry-field-label">价格来源</span>
      <FieldHelp>
        「跟随官方价」按 官方价 × 折扣 自动维护单价；折扣由你维护，不会被任何来源覆盖。
      </FieldHelp>
    </div>
    <div className="model-price-source-choice" role="radiogroup" aria-label="价格来源">
      {(["manual", "azure_retail", "anthropic"] as const).map((source) => (
        <label key={source} className="model-editor-checkbox">
          <input type="radio" name="price-source" value={source} disabled={busy}
            checked={draft.priceSource === source}
            onChange={() => setDraft((current) => ({ ...current, priceSource: source }))} />
          <span>{SOURCE_LABEL[source]}</span>
        </label>
      ))}
    </div>

    {following && <div className="model-price-follow">
      <div className="registry-field">
        <div className="registry-field-label-row">
          <span className="registry-field-label">官方价基准</span>
          <FieldHelp>
            由你确认一次，之后同步按这个基准取值。自动按名称匹配会错，而且错了看不出来。
          </FieldHelp>
        </div>
        {draft.priceReference
          ? <div className="model-price-chosen">
              <code data-no-localize>{draft.priceReference}</code>
            </div>
          : <p className="publication-form-note">尚未选择基准，下方搜索后点选一条。</p>}
        <div className="model-price-search">
          <Input value={query} disabled={busy} placeholder="搜索官方价目，例如 gpt-4.1 或 opus"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void search() } }} />
          <Button type="button" variant="secondary" size="sm" disabled={busy || searching}
            onClick={() => void search()}>
            {searching ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}
            搜索
          </Button>
        </div>
        {searchError && <p className="publication-form-note" role="alert">{searchError}</p>}
        {results && results.length === 0 && <p className="publication-form-note">没有匹配的官方价目。</p>}
        {results && results.length > 0 && <ul className="model-price-results">
          {results.slice(0, 8).map((entry) => (
            <li key={entry.reference}>
              <button type="button" disabled={busy}
                className={entry.reference === draft.priceReference ? "active" : ""}
                onClick={() => setDraft((current) => applyCatalogEntry(current, entry, discount.percent))}>
                <span className="model-price-result-label" data-no-localize>{entry.label}</span>
                <span className="model-price-result-detail">{entry.detail}</span>
                <span className="model-price-result-rate" data-no-localize>
                  入 {money(entry.input_per_million)} / 出 {money(entry.output_per_million)}
                </span>
              </button>
            </li>
          ))}
        </ul>}
      </div>

      <div className="registry-field">
        <div className="registry-field-label-row">
          <label htmlFor="model-discount" className="registry-field-label">折扣</label>
          <span className="model-editor-unit" data-no-localize>%</span>
          <FieldHelp>
            官方价乘以这个百分比得到实际单价。90 表示九折。留空则继承连接的折扣。
          </FieldHelp>
        </div>
        <Input id="model-discount" type="number" inputMode="decimal" min={0} max={100} step="any"
          disabled={busy} value={draft.discountPercent}
          placeholder={connectionDiscount === null ? "继承连接（未设折扣）" : `继承连接（${connectionDiscount}%）`}
          onChange={(event) => setDraft((current) => ({ ...current, discountPercent: event.target.value }))} />
        <p className="publication-form-note">
          {discount.percent === null
            ? "当前按官方价原价计费。"
            : discount.inherited
              ? `当前继承连接折扣 ${discount.percent}%。`
              : `当前使用本模型单独设置的 ${discount.percent}%。`}
        </p>
      </div>

      <PriceArithmetic model={model} draft={draft} percent={discount.percent} />
    </div>}
  </div>
}

function PriceArithmetic({ model, draft, percent }: {
  model: ManagedModel
  draft: ModelEditDraft
  percent: number | null
}) {
  const list = {
    input: model.list_input_cost_per_million,
    output: model.list_output_cost_per_million,
    cached: model.list_cached_cost_per_million,
    cacheWrite: model.list_cache_write_cost_per_million,
  }
  const charged = {
    input: draft.inputPrice ? Number(draft.inputPrice) : null,
    output: draft.outputPrice ? Number(draft.outputPrice) : null,
    cached: draft.cacheReadPrice ? Number(draft.cacheReadPrice) : null,
    cacheWrite: draft.cacheWritePrice ? Number(draft.cacheWritePrice) : null,
  }
  const rows: Array<[string, number | null, number | null]> = [
    ["输入", list.input, charged.input],
    ["输出", list.output, charged.output],
    ["缓存读取", list.cached, charged.cached],
    ["缓存写入", list.cacheWrite, charged.cacheWrite],
  ]
  return <div className="model-price-arithmetic">
    <div className="registry-field-label-row">
      <span className="registry-field-label">实际单价</span>
      <span className="model-editor-unit" data-no-localize>USD / 1M Tokens</span>
    </div>
    <div className="model-price-grid">
      <div className="model-price-grid-head">
        <span>计费项</span><span>官方价</span><span>折扣</span><span>实际单价</span>
      </div>
      {rows.map(([label, listed, actual]) => (
        <div className="model-price-grid-row" key={label}>
          <span>{label}</span>
          <span data-no-localize>{money(listed)}</span>
          <span data-no-localize>{percent === null ? "—" : `${percent}%`}</span>
          <span data-no-localize className="model-price-grid-actual">
            {actual !== null
              ? money(actual)
              : listed !== null ? money(discountedRate(listed, percent)) : "—"}
          </span>
        </div>
      ))}
    </div>
    {model.price_synced_at && <p className="publication-form-note">
      <RefreshCw size={12} /> 最近同步 {new Date(model.price_synced_at).toLocaleString()}
      {model.price_sync_status && model.price_sync_status !== "ok"
        && ` · ${SYNC_STATUS_LABEL[model.price_sync_status] ?? model.price_sync_status}`}
      {model.price_sync_message && ` · ${model.price_sync_message}`}
    </p>}
    {list.cacheWrite === null && <p className="publication-form-note">
      该来源未单独发布缓存写入价，按缓存读取单价计费。
    </p>}
  </div>
}
