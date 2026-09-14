import * as React from "react"
import { cn } from "cn"

/**
 * GAP-17.2: the shared loading placeholder. A pulsing muted surface shaped like
 * the content it stands in for; hidden from assistive tech by default so the
 * surrounding `role="status"` label is the single announcement. No dependency.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

export { Skeleton }
