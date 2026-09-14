import { cn } from "@/lib/utils"

interface BrandMarkProps {
  className?: string
  /** Hide the wordmark and show the glyph alone (collapsed rail). */
  markOnly?: boolean
}

/**
 * The authored brand mark (GAP-17.3, D-24e): crisp inline SVG only — no raster
 * asset, no emoji, no injected HTML. The badge is filled with the brand token
 * and the inner loop is drawn in the brand foreground, so both themes compose.
 * The wordmark is the existing heading face, with "Loop" carrying the accent.
 */
export function BrandMark({ className, markOnly = false }: BrandMarkProps) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg
        viewBox="0 0 28 28"
        aria-hidden="true"
        className="size-8 shrink-0 text-brand"
      >
        <rect x="0.5" y="0.5" width="27" height="27" rx="8" fill="currentColor" />
        {/* An unclosed loop: continuous discovery, always one more pass. */}
        <circle
          cx="14"
          cy="14"
          r="7"
          fill="none"
          stroke="var(--brand-foreground)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeDasharray="36.1 7.9"
          strokeDashoffset="2"
        />
        {/* The role at the centre of the loop. */}
        <circle cx="14" cy="14" r="2.4" fill="var(--brand-foreground)" />
      </svg>
      {!markOnly && (
        <span className="font-heading text-[0.95rem] font-semibold tracking-[-0.02em] text-foreground">
          Hunt<span className="text-brand">Loop</span>
        </span>
      )}
    </span>
  )
}
