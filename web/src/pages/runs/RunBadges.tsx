import { Badge } from "@/components/ui/badge"
import { runStatusLabel, runStatusTone, runTriggerLabel } from "./types"

/**
 * SKIPPED and CAPPED are first-class facts, not failures (Phase 3). They get
 * their own tone so "the overlap guard skipped this fire" and "the spend cap
 * stopped scoring" never read like the run errored.
 */

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
            ? "warning"
            : tone === "ok"
              ? "success"
              : "secondary"
      }
      className="font-normal"
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
