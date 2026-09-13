import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangleIcon, Loader2Icon, RefreshCwIcon } from "lucide-react"
import { api, apiPost, ApiError } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { CompanyOut, CoverageOut } from "./types"

/**
 * D-06: coverage disclosure is a headline number, not a footnote. The honest
 * count of what is actually watchable is the card's title line; employers that
 * could not be resolved for automatic watching are listed right beneath for
 * manual attention. The server computes the numbers — no client-side math.
 *
 * GAP-7/D-7a: the call to action is a real bulk retry, not a navigation. It
 * queues resolution for every unresolved employer through
 * POST /api/companies/resolve-batch, shows an in-progress state, and lets the
 * shared ["companies"] cache repaint each row as the background probes finish.
 * The action is hidden when nothing is pending (D-7b).
 */
export function CoverageCard() {
  const queryClient = useQueryClient()
  // id -> last_checked_at at the moment the batch was queued. The probe is done
  // once the row resolves or its last_checked_at changes; then polling stops.
  const [retrying, setRetrying] = useState<Record<string, string | null>>({})
  const [toast, setToast] = useState<string | null>(null)

  const coverageQuery = useQuery({
    queryKey: ["coverage"],
    queryFn: () => api<CoverageOut>("/api/companies/coverage"),
  })
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
    refetchInterval: () => (Object.keys(retrying).length > 0 ? 2000 : false),
  })

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  // Stop showing "Retrying…" once each probe finishes, whether it succeeded or
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
        if (company.resolved || company.last_checked_at !== prev[id]) {
          delete next[id]
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [companiesQuery.data])

  if (coverageQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-muted-foreground">
            Checking coverage…
          </CardTitle>
        </CardHeader>
      </Card>
    )
  }

  const coverage = coverageQuery.data ?? {
    added: 0,
    watchable: 0,
    needs_attention: 0,
    resolved: 0,
  }
  const needingAttention = (companiesQuery.data ?? []).filter(
    (company) => !company.resolved
  )
  const isRetryingAll = Object.keys(retrying).length > 0

  async function retryAll() {
    if (needingAttention.length === 0) return
    setRetrying(
      Object.fromEntries(needingAttention.map((c) => [c.id, c.last_checked_at]))
    )
    try {
      await apiPost("/api/companies/resolve-batch", {
        ids: needingAttention.map((c) => c.id),
      })
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
      await queryClient.invalidateQueries({ queryKey: ["coverage"] })
    } catch (error) {
      setRetrying({})
      setToast(
        error instanceof ApiError ? error.detail : "Couldn't start the retry."
      )
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          We're watching {coverage.watchable} of your {coverage.added} added
          employers
        </CardTitle>
        <CardDescription>
          That's the honest number: the rest we can't watch automatically yet.
        </CardDescription>
      </CardHeader>
      {coverage.needs_attention > 0 ? (
        <CardContent>
          <div className="grid gap-3 rounded-xl border border-destructive/25 bg-destructive/5 p-3.5">
            <p className="flex items-center gap-2 text-sm font-medium text-foreground">
              <AlertTriangleIcon
                aria-hidden="true"
                className="size-4 shrink-0 text-destructive"
              />
              We can't automatically watch {coverage.needs_attention} yet — they
              need attention.
            </p>
            {needingAttention.length > 0 ? (
              <ul className="grid gap-1.5">
                {needingAttention.map((company) => (
                  <li
                    key={company.id}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span>{company.name}</span>
                    {company.resolution_detail ? (
                      <Badge variant="outline" className="font-normal">
                        {company.resolution_detail}
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
            {needingAttention.length > 0 ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                disabled={isRetryingAll}
                onClick={retryAll}
              >
                {isRetryingAll ? (
                  <Loader2Icon className="animate-spin" />
                ) : (
                  <RefreshCwIcon />
                )}
                {isRetryingAll
                  ? "Retrying…"
                  : `Retry all ${needingAttention.length} pending employer${
                      needingAttention.length === 1 ? "" : "s"
                    }`}
              </Button>
            ) : null}
          </div>
        </CardContent>
      ) : null}
      {toast ? (
        <div
          role="status"
          className="fixed right-4 bottom-4 z-50 rounded-lg border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md"
        >
          {toast}
        </div>
      ) : null}
    </Card>
  )
}
