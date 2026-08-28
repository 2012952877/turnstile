import { Switch as SwitchPrimitive } from "@base-ui/react/switch"

import { cn } from "../../lib/utils"

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root data-slot="switch" className={cn(className)} {...props}>
      <SwitchPrimitive.Thumb data-slot="switch-thumb" />
    </SwitchPrimitive.Root>
  )
}

export { Switch }