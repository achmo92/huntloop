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

interface SkeletonTableProps {
  /** Number of placeholder body rows. */
  rows?: number
  className?: string
}

/**
 * A table-shaped loading panel: the framed surface, the muted header band and
 * calm body rows the real table will resolve into. Used by every table surface
 * (Listings, Employers, Runs) so the transition into content is seamless.
 */
function SkeletonTable({ rows = 6, className }: SkeletonTableProps) {
  return (
    <div
      data-slot="skeleton-table"
      aria-hidden="true"
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card",
        className
      )}
    >
      <div className="flex items-center gap-4 border-b border-border bg-muted/40 px-3 py-3">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-20" />
        <Skeleton className="ml-auto h-3 w-12" />
      </div>
      <div className="divide-y divide-border/70">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="flex items-center gap-4 px-3 py-3.5">
            <Skeleton className="h-4 w-[28%] min-w-32" />
            <Skeleton className="h-4 w-[16%]" />
            <Skeleton className="h-4 w-[14%]" />
            <Skeleton className="ml-auto h-4 w-16" />
          </div>
        ))}
      </div>
    </div>
  )
}

export { Skeleton, SkeletonTable }
