import { Button } from "@/components/ui/button"

interface EmptyStateProps {
  title: string
  description: string
  actionLabel?: string
  onAction?: () => void
}

/**
 * The shared UI-07/D-18 primitive: an empty state tells the user what to do
 * next — it never leaves them facing a blank table. Calm, centered, one
 * primary action when there is something worth doing.
 */
export function EmptyState({
  title,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-24 text-center">
      <h2 className="font-heading text-xl font-semibold tracking-tight text-balance">
        {title}
      </h2>
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground text-pretty">
        {description}
      </p>
      {actionLabel ? (
        <Button className="mt-2" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  )
}