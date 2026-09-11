import type { RunOut } from "@/pages/runs/types"

/** The seven per-stage counters of the last run (04-05 `FunnelOut`). */
export interface Funnel {
  companies_checked: number
  listings_fetched: number
  after_dedup: number
  after_deterministic: number
  after_triage: number
  scored: number
  new_jobs_written: number
}

/**
 * One-request dashboard aggregate (04-05 `DashboardOut`). A quiet week is
 * nulls and zeros, never an error — the UI must render that honestly.
 */
export interface DashboardResponse {
  last_run: RunOut | null
  funnel: Funnel | null
  next_scheduled_run: string | null
  listings_by_status: Record<string, number>
  total_listings: number
}

/** The slice of `GET /api/settings` the dashboard needs to label the next run. */
export interface SettingsTimezoneResponse {
  schedule: { run_at: string; timezone: string }
}
