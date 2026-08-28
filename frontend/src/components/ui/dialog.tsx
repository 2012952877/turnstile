import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

import { cn } from "../../lib/utils"

function Dialog(props: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogClose(props: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogPortal(props: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogOverlay({ className, ...props }: DialogPrimitive.Backdrop.Props) {
  return <DialogPrimitive.Backdrop data-slot="dialog-overlay" className={cn("search-dialog-overlay", className)} {...props} />
}

function DialogContent({ className, children, ...props }: DialogPrimitive.Popup.Props) {
  return <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Popup data-slot="dialog-content" className={cn("search-dialog-content", className)} {...props}>
      {children}
    </DialogPrimitive.Popup>
  </DialogPortal>
}

function DialogTitle(props: DialogPrimitive.Title.Props) {
  return <DialogPrimitive.Title data-slot="dialog-title" {...props} />
}

function DialogDescription(props: DialogPrimitive.Description.Props) {
  return <DialogPrimitive.Description data-slot="dialog-description" {...props} />
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn(className)} {...props} />
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-footer" className={cn(className)} {...props} />
}

export { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle }