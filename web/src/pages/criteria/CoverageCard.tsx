import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
import {
  RESOLUTION_QUEUE_KEY,
  deriveInFlight,
  useQueuedResolutionRows,
  type ResolutionQueueVars,
} from "./useResolutionQueue"

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
 *
 * GAP-9: the queue is tagged with RESOLUTION_QUEUE_KEY so the registry's rows
 * show the same retrying affordance per row; the state is derived, never
 * accumulated, so it clears as rows land.
 */
export function CoverageCard() {
  const queryClient = useQueryClient()
  const [toast, setToast] = useState<string | null>(null)

  const queuedRows = useQueuedResolutionRows()
  const queue = useMutation({
    mutationKey: RESOLUTION_QUEUE_KEY,
    mutationFn: (vars: ResolutionQueueVars) =>
      apiPost("/api/companies/resolve-batch", {
        ids: vars.rows.map((row) => row.id),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
      await queryClient.invalidateQueries({ queryKey: ["coverage"] })
    },
    onError: (error) => {
      setToast(
        error instanceof ApiError ? error.detail : "Couldn't start the retry."
      )
    },
  })

  const coverageQuery = useQuery({
    queryKey: ["coverage"],
    queryFn: () => api<CoverageOut>("/api/companies/coverage"),
  })
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
    refetchInterval: (query) => {
      // GAP-13: keep polling while any persisted row is Resolving, in addition
      // to the optimistic trigger window and the derived in-flight set.
      const data = query.state.data ?? []
      const anyResolving = data.some(
        (company) => company.resolution_state === "resolving"
      )
      return anyResolving ||
        queue.isPending ||
        Object.keys(deriveInFlight(queuedRows, data)).length > 0
        ? 2000
        : false
    },
  })

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

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
    (company) =>
      company.enabled &&
      !company.resolved &&
      company.resolution_state !== "resolving"
  )
  const inFlight = deriveInFlight(queuedRows, companiesQuery.data ?? [])
  const isRetryingAll = queue.isPending || Object.keys(inFlight).length > 0

  function retryAll() {
    if (needingAttention.length === 0) return
    queue.mutate({
      rows: needingAttention.map((c) => ({
        id: c.id,
        last_checked_at: c.last_checked_at,
      })),
    })
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
          className="fixed right-4 bottom-4 z-50 rounded-lg border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-elevation-2"
        >
          {toast}
        </div>
      ) : null}
    </Card>
  )
}
