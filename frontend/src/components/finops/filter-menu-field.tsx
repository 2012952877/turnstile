import type { LucideIcon } from "lucide-react"

import { Button } from "../ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu"

const ALL_OPTION = "__all__"

export function FilterMenuField({
  label,
  icon: Icon,
  value,
  options,
  onChange,
  allowAll = true,
  active,
}: {
  label: string
  icon: LucideIcon
  value?: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string | undefined) => void
  allowAll?: boolean
  active?: boolean
}) {
  const selected = options.find((option) => option.value === value)
  const isActive = active ?? Boolean(value)
  return <div className="finops-filter-setting">
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="finops-filter-menu-trigger" data-active={isActive || undefined}><Icon size={14} /><span>{selected?.label ?? label}</span></Button>} />
      <DropdownMenuContent align="start" className="finops-filter-menu-options">
        <DropdownMenuGroup><DropdownMenuLabel>{label}</DropdownMenuLabel></DropdownMenuGroup>
        <DropdownMenuRadioGroup value={value ?? ALL_OPTION} onValueChange={(next) => onChange(next === ALL_OPTION ? undefined : next)}>
          {allowAll && <DropdownMenuRadioItem value={ALL_OPTION}><Icon size={16} />全部</DropdownMenuRadioItem>}
          {options.map((option) => <DropdownMenuRadioItem key={option.value} value={option.value}><Icon size={16} />{option.label}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  </div>
}