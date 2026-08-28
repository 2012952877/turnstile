import { useState } from "react"
import { Search, X } from "lucide-react"

type ExpandableSearchProps = {
  value: string
  placeholder: string
  ariaLabel?: string
  onChange: (value: string) => void
}

export function ExpandableSearch({ value, placeholder, ariaLabel, onChange }: ExpandableSearchProps) {
  const [open, setOpen] = useState(false)
  const expanded = open || value.length > 0
  const close = () => {
    onChange("")
    setOpen(false)
  }

  return <div className={`model-search-control ${expanded ? "expanded" : ""}`}>
    {expanded ? <div className="smh-search model-search" role="search">
      <Search size={14} aria-hidden="true" />
      <input
        autoFocus
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return
          event.preventDefault()
          close()
        }}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
      />
      <button type="button" className="model-search-close" title="关闭搜索" aria-label="关闭搜索" onClick={close}><X size={14} /></button>
    </div> : <button type="button" className="model-search-toggle" title="打开搜索" aria-label="打开搜索" aria-expanded="false" onClick={() => setOpen(true)}><Search size={15} /></button>}
  </div>
}