import type { ReactNode } from "react"

interface PageHeaderProps {
  /** Rendered as the page's single <h1>, at the shared display step. */
  title: string
  /** The page's own description sentence, set muted beneath the title. */
  description?: string
  /** Primary action(s) aligned to the trailing edge. */
  actions?: ReactNode
  /** Optional secondary row (e.g. Dashboard's shortcut chips). */
  children?: ReactNode
}

/**
 * GAP-17.2: the shared page-header primitive. Every surface opens on this so
 * the reading rhythm is identical page to page — one display-scale h1, the
 * page's existing description sentence beneath it, an action slot on the right
 * and an optional secondary row below. There is deliberately no kicker or
 * eyebrow: the heading carries its own weight.
 */
export function PageHeader({
  title,
  description,
  actions,
  children,
}: PageHeaderProps) {
  return (
    <header className="grid gap-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <h1 className="font-heading text-3xl leading-tight font-semibold tracking-tight text-balance">
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground text-pretty">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
      {children ? <div className="min-w-0">{children}</div> : null}
    </header>
  )
}
