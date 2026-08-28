import { useEffect, useState } from "react"
import { Command } from "cmdk"
import { Search as SearchIcon, type LucideIcon } from "lucide-react"

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog"

export interface SearchPageItem {
  id: string
  label: string
  icon: LucideIcon
}

export function SearchCommand({ open, onOpenChange, items, onSelect }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: SearchPageItem[]
  onSelect: (id: string) => void
}) {
  const [query, setQuery] = useState("")

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k" || (!event.metaKey && !event.ctrlKey)) return
      event.preventDefault()
      onOpenChange(!open)
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [onOpenChange, open])

  useEffect(() => {
    if (!open) setQuery("")
  }, [open])

  const selectPage = (id: string) => {
    onOpenChange(false)
    onSelect(id)
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="search-command-dialog" finalFocus={false}>
      <DialogTitle className="sr-only">搜索页面</DialogTitle>
      <DialogDescription className="sr-only">搜索并打开 Turnstile 页面</DialogDescription>
      <Command className="search-command" label="搜索页面">
        <div className="search-command-input-row">
          <SearchIcon size={20} />
          <Command.Input autoFocus value={query} onValueChange={setQuery} placeholder="搜索页面..." />
          <kbd>ESC</kbd>
        </div>
        <Command.List className="search-command-list">
          <Command.Empty>没有匹配的页面</Command.Empty>
          <Command.Group heading="页面">
            {items.map((item) => <Command.Item key={item.id} value={item.label} keywords={[item.id]} onSelect={() => selectPage(item.id)}>
              <item.icon size={16} />
              <span>{item.label}</span>
            </Command.Item>)}
          </Command.Group>
        </Command.List>
      </Command>
    </DialogContent>
  </Dialog>
}