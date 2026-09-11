import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { runStatusLabel, runStatusTone, runTriggerLabel } from "./types"

/**
 * SKIPPED and CAPPED are first-class facts, not failures (Phase 3). They get
 * their own tone so "the overlap guard skipped this fire" and "the spend cap
 * stopped scoring" never read like the run errored.
 */

const TONE_CLASS: Record<string, string> = {
  attention: "border-amber-500/40 text-amber-700 dark:text-amber-400",
}

export function RunStatusBadge({ status }: { status: string }) {
  const tone = runStatusTone(status)
  return (
    <Badge
      data-tone={tone}
      data-testid={`run-status-${status}`}
      variant={
        tone === "error"
          ? "destructive"
          : tone === "attention"
            ? "outline"
            : "secondary"
      }
      className={cn("font-normal", TONE_CLASS[tone])}
    >
      {runStatusLabel(status)}
    </Badge>
  )
}

export function RunTriggerBadge({ trigger }: { trigger: string }) {
  return (
    <Badge variant="outline" className="font-normal">
      {runTriggerLabel(trigger)}
    </Badge>
  )
}
