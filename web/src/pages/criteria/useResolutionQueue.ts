import { useMutationState } from "@tanstack/react-query"
import type { CompanyOut } from "./types"

/**
 * GAP-9: every resolution trigger (CoverageCard's bulk retry, the add
 * dialog's single queue) tags its mutation with this key so any surface —
 * notably RegistryTable — can see which employers have a probe in flight.
 */
export const RESOLUTION_QUEUE_KEY = ["resolution-queue"] as const

export interface ResolutionQueueRow {
  id: string
  last_checked_at: string | null
}

export interface ResolutionQueueVars {
  rows: ResolutionQueueRow[]
}

/** Raw queued rows from every non-failed resolution mutation. */
export function useQueuedResolutionRows(): ResolutionQueueRow[] {
  const queues = useMutationState({
    filters: { mutationKey: RESOLUTION_QUEUE_KEY },
    select: (mutation) => {
      if (mutation.state.status === "error") return []
      const vars = mutation.state.variables as ResolutionQueueVars | undefined
      return vars?.rows ?? []
    },
  })
  return queues.flat()
}

/**
 * A queued row is still in flight until the company resolves or its
 * last_checked_at moves past the queued baseline. Derived, never
 * accumulated, so it self-clears when the probe lands.
 */
export function deriveInFlight(
  queued: ResolutionQueueRow[],
  companies: CompanyOut[]
): Record<string, string | null> {
  const inFlight: Record<string, string | null> = {}
  for (const row of queued) {
    const company = companies.find((candidate) => candidate.id === row.id)
    if (!company || company.resolved) continue
    if (company.last_checked_at !== row.last_checked_at) continue
    inFlight[row.id] = row.last_checked_at
  }
  return inFlight
}
