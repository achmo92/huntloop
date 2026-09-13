import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { RunNowButton } from "@/pages/dashboard/RunNowButton"
import { RunDetail } from "./runs/RunDetail"
import { RunStatusBadge, RunTriggerBadge } from "./runs/RunBadges"
import { StopRunButton } from "./runs/StopRunButton"
import type { RunOut } from "./runs/types"

/**
 * RUN-09: the whole run history over the browser, newest first, shaped from
 * the same field set the CLI's RUN_HISTORY_FIELDS renders. SKIPPED and CAPPED
 * are first-class statuses, not errors (Phase 3 guardrails), and a quiet week
 * is unambiguous because every run's outcome is here.
 */

function formatDateTime(value: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatCost(value: number | null): string {
  return value === null ? "$0.0000" : `$${value.toFixed(4)}`
}

export default function Runs() {
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [watching, setWatching] = useState(false)

  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => api<RunOut[]>("/api/runs?limit=50"),
    refetchInterval: (query) => {
      // GAP-16: poll while ANY row is running (not only the newest) so a stopped
      // row reaches STOPPED without a manual refresh.
      const hasRunningRow = query.state.data?.some(
        (run) => run.status === "running"
      )
      return hasRunningRow || watching ? 3000 : false
    },
  })

  const runs = runsQuery.data ?? []
  const newestRunning = runs[0]?.status === "running"
  const hasRunning = runs.some((run) => run.status === "running")

  // A manual trigger's row can take a beat to appear; poll briefly either way.
  useEffect(() => {
    if (!watching) return
    const timer = setTimeout(() => setWatching(false), 60_000)
    return () => clearTimeout(timer)
  }, [watching])

  const isEmpty = runsQuery.isSuccess && runs.length === 0

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight text-balance">
            Runs
          </h1>
          <p className="mt-1.5 max-w-prose text-sm text-muted-foreground text-pretty">
            Every discovery run and what it found — a quiet week is explained
            here, never ambiguous.
          </p>
        </div>
        {!isEmpty ? (
          <RunNowButton
            isRunning={newestRunning}
            onStarted={() => setWatching(true)}
          />
        ) : null}
      </header>

      {hasRunning ? (
        <p className="text-sm text-muted-foreground text-pretty">
          Stopping is cooperative — an in-progress run finishes its current step,
          then stops.
        </p>
      ) : null}

      {runsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading runs…</p>
      ) : runsQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          We couldn't load your run history. Refresh to try again.
        </p>
      ) : isEmpty ? (
        <div className="grid justify-items-center gap-1">
          <EmptyState
            title="No runs yet"
            description="Start your first discovery run and it'll appear here with its results."
          />
          <RunNowButton onStarted={() => setWatching(true)} />
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Trigger</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Employers</TableHead>
              <TableHead className="text-right">Fetched</TableHead>
              <TableHead className="text-right">Filtered</TableHead>
              <TableHead className="text-right">Triaged</TableHead>
              <TableHead className="text-right">Scored</TableHead>
              <TableHead className="text-right">Written</TableHead>
              <TableHead className="text-right">Tokens in</TableHead>
              <TableHead className="text-right">Tokens out</TableHead>
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow
                key={run.id}
                onClick={() => setSelectedRun(run.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    setSelectedRun(run.id)
                  }
                }}
                tabIndex={0}
                aria-label={`View run from ${formatDateTime(run.started_at)}`}
                className="cursor-pointer"
              >
                <TableCell className="tabular-nums">
                  {formatDateTime(run.started_at)}
                </TableCell>
                <TableCell>
                  <RunTriggerBadge trigger={run.trigger} />
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <RunStatusBadge status={run.status} />
                    {run.status === "running" ? (
                      <StopRunButton runId={run.id} />
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.companies_checked}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.listings_fetched}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.after_deterministic}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.after_triage}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.scored}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.new_jobs_written}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.tokens_in}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {run.tokens_out}
                </TableCell>
                <TableCell className="text-right font-medium tabular-nums">
                  {formatCost(run.cost_usd)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <RunDetail
        runId={selectedRun}
        open={selectedRun !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedRun(null)
        }}
      />
    </div>
  )
}
