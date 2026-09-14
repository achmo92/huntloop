import { useEffect, useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ExternalLinkIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react"
import { api, apiPatch, apiPost, ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { SkeletonTable } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { CompanyOut } from "@/pages/criteria/types"
import {
  deriveInFlight,
  useQueuedResolutionRows,
} from "@/pages/criteria/useResolutionQueue"
import { SetBoardDialog } from "@/pages/employers/SetBoardDialog"

/**
 * D-07/D-08: the registry is ONE list. Resolution failure is a status column
 * ("Needs attention" + what was tried) with a per-row retry — never an error.
 * Disabling is an in-place toggle; the row stays, grayed by data
 * (enabled=false), history intact. Hiding disabled rows is an opt-in filter so
 * by default nothing is ever hidden. Staleness (Phase 2's REG-05 signal) shows
 * as a badge with its message.
 */

function formatAts(ats: string | null, identifier: string | null): string | null {
  if (!ats) return null
  const label = ats.charAt(0).toUpperCase() + ats.slice(1)
  return identifier ? `${label} · ${identifier}` : label
}

function formatDate(value: string | null): string {
  if (!value) return "Never checked"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "Never checked"
  return date.toLocaleDateString()
}

interface EnabledToggleProps {
  company: CompanyOut
  onToggle: (company: CompanyOut) => void
}

/** A real switch (role="switch") — the toggle is the whole interaction (D-08). */
function EnabledToggle({ company, onToggle }: EnabledToggleProps) {
  const label = company.enabled
    ? `Disable ${company.name}`
    : `Enable ${company.name}`
  return (
    <button
      type="button"
      role="switch"
      aria-checked={company.enabled}
      aria-label={label}
      onClick={() => onToggle(company)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        company.enabled
          ? "border-primary bg-primary"
          : "border-input bg-muted"
      )}
    >
      <span
        className={cn(
          "block size-4 rounded-full bg-background shadow-sm transition-transform",
          company.enabled ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  )
}

export function RegistryTable() {
  const queryClient = useQueryClient()
  const [hideDisabled, setHideDisabled] = useState(false)
  // id -> last_checked_at at the moment Retry was clicked. The probe is done
  // once the row resolves or its last_checked_at changes; then polling stops.
  const [retrying, setRetrying] = useState<Record<string, string | null>>({})
  const [toast, setToast] = useState<string | null>(null)
  // GAP-14: the employer whose board the user is setting by hand (null = closed).
  const [boardCompany, setBoardCompany] = useState<CompanyOut | null>(null)

  // GAP-9: rows queued by any surface (CoverageCard's bulk retry, the add
  // dialog) — must be read before the query so its interval closure sees them.
  const queuedRows = useQueuedResolutionRows()

  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
    refetchInterval: (query) => {
      // GAP-13: keep polling while any persisted row is Resolving, in addition
      // to a local/shared retry, so the final status lands without a reload.
      const data = query.state.data ?? []
      const anyResolving = data.some(
        (company) => company.resolution_state === "resolving"
      )
      return anyResolving ||
        Object.keys(retrying).length > 0 ||
        Object.keys(deriveInFlight(queuedRows, data)).length > 0
        ? 2000
        : false
    },
  })

  const companies = useMemo(
    () => companiesQuery.data ?? [],
    [companiesQuery.data]
  )

  // GAP-9: pending rows show the per-row retrying affordance while a bulk
  // retry (or the add dialog's queue) is in flight; derived state self-clears.
  const inFlight = deriveInFlight(queuedRows, companies)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  // Stop showing "Retrying…" once the probe finishes, whether it succeeded or
  // failed again (a new last_checked_at is the signal that it ran).
  useEffect(() => {
    const data = companiesQuery.data
    if (!data) return
    setRetrying((prev) => {
      if (Object.keys(prev).length === 0) return prev
      const next = { ...prev }
      let changed = false
      for (const id of Object.keys(prev)) {
        const company = data.find((candidate) => candidate.id === id)
        if (!company) continue
        if (
          company.resolved ||
          company.resolution_state === "resolved" ||
          company.resolution_state === "error" ||
          company.last_checked_at !== prev[id]
        ) {
          delete next[id]
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [companiesQuery.data])

  async function toggleEnabled(company: CompanyOut) {
    try {
      await apiPatch(`/api/companies/${company.id}`, {
        enabled: !company.enabled,
      })
      setToast(
        `${company.name} is now ${company.enabled ? "disabled" : "enabled"}.`
      )
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
    } catch (error) {
      setToast(
        error instanceof ApiError
          ? error.detail
          : "Couldn't update that employer."
      )
    }
  }

  async function retry(company: CompanyOut) {
    setRetrying((prev) => ({ ...prev, [company.id]: company.last_checked_at }))
    try {
      await apiPost(`/api/companies/${company.id}/resolve`, {})
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
    } catch (error) {
      setRetrying((prev) => {
        const next = { ...prev }
        delete next[company.id]
        return next
      })
      setToast(
        error instanceof ApiError
          ? error.detail
          : "Couldn't start the retry."
      )
    }
  }

  if (companiesQuery.isLoading) {
    return (
      <div className="grid gap-3">
        <span role="status" className="sr-only">
          Loading employers…
        </span>
        <SkeletonTable rows={5} />
      </div>
    )
  }

  if (companiesQuery.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        We couldn't load your employers. Refresh to try again.
      </p>
    )
  }

  if (companies.length === 0) {
    return null
  }

  const visible = hideDisabled
    ? companies.filter((company) => company.enabled)
    : companies

  return (
    <div className="grid gap-3">
      <div className="flex w-fit items-center gap-2 text-sm text-muted-foreground">
        <Checkbox
          aria-label="Hide disabled"
          checked={hideDisabled}
          onCheckedChange={(value) => setHideDisabled(value === true)}
        />
        <span>Hide disabled</span>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Employer</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Jobs last seen</TableHead>
            <TableHead>Staleness</TableHead>
            <TableHead className="text-right">Enabled</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((company) => {
            const atsLabel = formatAts(company.ats, company.ats_identifier)
            const isRetrying = company.id in retrying || company.id in inFlight
            // GAP-13: the persisted state is authoritative; the local/shared
            // retry only adds optimistic in-flight feedback for an added row.
            const isResolving =
              company.resolution_state === "resolving" || isRetrying
            return (
              <TableRow
                key={company.id}
                className={cn(!company.enabled && "opacity-60")}
              >
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{company.name}</span>
                    {company.careers_url ? (
                      <a
                        href={company.careers_url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Open ${company.name} careers page`}
                        className="text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <ExternalLinkIcon className="size-3.5" />
                      </a>
                    ) : null}
                  </div>
                </TableCell>

                <TableCell className="whitespace-normal">
                  {company.resolution_state === "resolved" ? (
                    <Badge variant="success" className="font-normal">
                      {atsLabel ?? "Resolved"}
                    </Badge>
                  ) : company.resolution_state === "error" ? (
                    <div className="flex flex-col items-start gap-1">
                      <Badge variant="destructive" className="font-normal">
                        Needs attention
                      </Badge>
                      {company.resolution_detail ? (
                        <span className="text-xs text-muted-foreground">
                          {company.resolution_detail}
                        </span>
                      ) : null}
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Button
                          type="button"
                          size="xs"
                          variant="outline"
                          disabled={isRetrying}
                          aria-label={`Retry resolution for ${company.name}`}
                          onClick={() => retry(company)}
                        >
                          {isRetrying ? (
                            <Loader2Icon className="animate-spin" />
                          ) : (
                            <RefreshCwIcon />
                          )}
                          {isRetrying ? "Retrying…" : "Retry resolution"}
                        </Button>
                        <Button
                          type="button"
                          size="xs"
                          variant="outline"
                          aria-label={`Set board manually for ${company.name}`}
                          onClick={() => setBoardCompany(company)}
                        >
                          Set board manually
                        </Button>
                      </div>
                    </div>
                  ) : isResolving ? (
                    <Badge variant="secondary" className="font-normal">
                      <Loader2Icon
                        className="animate-spin"
                        aria-hidden="true"
                      />
                      Resolving…
                    </Badge>
                  ) : (
                    <Badge
                      variant="outline"
                      className="font-normal text-muted-foreground"
                    >
                      Added
                    </Badge>
                  )}
                </TableCell>

                <TableCell>
                  <div className="flex flex-col">
                    <span className="tabular-nums">
                      {company.last_job_count ?? "—"} jobs
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {formatDate(company.last_checked_at)}
                    </span>
                  </div>
                </TableCell>

                <TableCell className="whitespace-normal">
                  {company.possibly_stale ? (
                    <div className="flex flex-col items-start gap-1">
                      <Badge variant="outline" className="font-normal">
                        Possibly stale
                      </Badge>
                      {company.staleness_message ? (
                        <span className="text-xs text-muted-foreground">
                          {company.staleness_message}
                        </span>
                      ) : null}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>

                <TableCell className="text-right">
                  <EnabledToggle company={company} onToggle={toggleEnabled} />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>

      {toast ? (
        <div
          role="status"
          className="fixed right-4 bottom-4 z-50 rounded-lg border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-elevation-2"
        >
          {toast}
        </div>
      ) : null}

      <SetBoardDialog
        company={boardCompany}
        onClose={() => setBoardCompany(null)}
      />
    </div>
  )
}
