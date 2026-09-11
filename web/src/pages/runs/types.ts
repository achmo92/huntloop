/**
 * Run shapes shared by the Runs and Dashboard surfaces. These mirror the API's
 * `RunOut` / `RunDetailOut` (04-05), which in turn reshape the CLI's
 * `RUN_HISTORY_FIELDS` — one field list, no drift.
 */

export type RunStatus =
  | "running"
  | "success"
  | "partial"
  | "failed"
  | "skipped"
  | "capped"

export type RunTrigger = "scheduled" | "manual" | "catch_up"

export interface RunOut {
  id: string
  started_at: string
  finished_at: string | null
  trigger: string
  status: string
  companies_checked: number
  listings_fetched: number
  after_deterministic: number
  after_triage: number
  scored: number
  new_jobs_written: number
  tokens_in: number
  tokens_out: number
  cost_usd: number | null
  error_summary: string | null
}

export interface RunError {
  company_name: string | null
  stage: string | null
  message: string | null
}

export interface RunDetail extends RunOut {
  after_dedup: number
  errors: RunError[]
}

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: "Running",
  success: "Success",
  partial: "Partial",
  failed: "Failed",
  skipped: "Skipped",
  capped: "Capped",
}

export const RUN_TRIGGER_LABELS: Record<string, string> = {
  scheduled: "Scheduled",
  manual: "Manual",
  catch_up: "Catch-up",
}

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status
}

export function runTriggerLabel(trigger: string): string {
  return RUN_TRIGGER_LABELS[trigger] ?? trigger
}

/** A quiet week is only unambiguous if SKIPPED/CAPPED read as facts, not errors. */
export type RunStatusTone = "ok" | "attention" | "error" | "neutral"

export function runStatusTone(status: string): RunStatusTone {
  switch (status) {
    case "success":
      return "ok"
    case "capped":
    case "partial":
    case "skipped":
      return "attention"
    case "failed":
      return "error"
    default:
      return "neutral"
  }
}
