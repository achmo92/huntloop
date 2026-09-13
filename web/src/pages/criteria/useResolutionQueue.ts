import { useMutationState } from "@tanstack/react-query"
import type { CompanyOut } from "./types"

/**
 * GAP-9/GAP-13: every resolution trigger (CoverageCard's bulk retry, the add
 * dialog's single queue) tags its mutation with this key so any surface —
 * notably RegistryTable — can see which employers have a probe in flight.
 *
 * GAP-13: the persisted `resolution_state` is the in-flight source of truth
 * after the 202; this queue exists only for optimistic feedback while the
 * trigger request itself is pending.
 */
export const RESOLUTION_QUEUE_KEY = ["resolution-queue"] as const

export interface ResolutionQueueRow {
  id: string
  last_checked_at: string | null
}

export interface ResolutionQueueVars {
  rows: ResolutionQueueRow[]
}

/** Raw queued rows from every resolution mutation that is still pending. */
export function useQueuedResolutionRows(): ResolutionQueueRow[] {
  const queues = useMutationState({
    filters: { mutationKey: RESOLUTION_QUEUE_KEY },
    select: (mutation) => {
      // GAP-13: only the optimistic window. Once the trigger request settles,
      // the persisted resolution_state carries the in-flight truth.
      if (mutation.state.status !== "pending") return []
      const vars = mutation.state.variables as ResolutionQueueVars | undefined
      return vars?.rows ?? []
    },
  })
  return queues.flat()
}

/**
 * A queued row is in flight until the employer resolves. Derived, never
 * accumulated: a row whose persisted `resolution_state` is `resolved` drops
 * out immediately, and every remaining queued row is marked in flight.
 */
export function deriveInFlight(
  queued: ResolutionQueueRow[],
  companies: CompanyOut[]
): Record<string, string | null> {
  const inFlight: Record<string, string | null> = {}
  for (const row of queued) {
    const company = companies.find((candidate) => candidate.id === row.id)
    if (!company) continue
    if (company.resolution_state === "resolved") continue
    inFlight[row.id] = row.last_checked_at
  }
  return inFlight
}
