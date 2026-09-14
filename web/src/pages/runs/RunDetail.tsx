import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { RunStatusBadge, RunTriggerBadge } from "./RunBadges"
import { StopRunButton } from "./StopRunButton"
import type { RunDetail as RunDetailData } from "./types"

/**
 * RUN-06: a run's detail answers "when did it happen, what did it find, what
 * did it cost, and did anything fail — with names, not codes". Every error
 * row carries the employer and the stage it happened at.
 */

const COUNTERS: { key: keyof RunDetailData; label: string }[] = [
  { key: "companies_checked", label: "Employers checked" },
  { key: "listings_fetched", label: "Listings fetched" },
  { key: "after_dedup", label: "After dedup" },
  { key: "after_deterministic", label: "After filtering" },
  { key: "after_triage", label: "After triage" },
  { key: "scored", label: "Scored" },
  { key: "new_jobs_written", label: "New jobs written" },
]

function formatDateTime(value: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatDuration(started: string, finished: string | null): string {
  if (!finished) return "In progress"
  const start = new Date(started).getTime()
  const end = new Date(finished).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return "—"
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${seconds}s`
}

function formatCost(value: number | null): string {
  return value === null ? "$0.0000" : `$${value.toFixed(4)}`
}

interface RunDetailProps {
  runId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function RunDetail({ runId, open, onOpenChange }: RunDetailProps) {
  const detailQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunDetailData>(`/api/runs/${runId}`),
    enabled: open && runId !== null,
  })
  const detail = detailQuery.data

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Run detail</SheetTitle>
          <SheetDescription>
            {detail
              ? `${formatDateTime(detail.started_at)} · ${formatDuration(
                  detail.started_at,
                  detail.finished_at
                )}`
              : "Loading the run…"}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4 pb-6">
          {detailQuery.isLoading ? (
            <div className="grid gap-5">
              <span role="status" className="sr-only">
                Loading run…
              </span>
              <div className="flex gap-2">
                <Skeleton className="h-5 w-20 rounded-4xl" />
                <Skeleton className="h-5 w-16 rounded-4xl" />
              </div>
              <div className="grid gap-2">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-4/5" />
                <Skeleton className="h-4 w-3/5" />
              </div>
              <div className="grid gap-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
              </div>
            </div>
          ) : detailQuery.isError || !detail ? (
            <p role="alert" className="text-sm text-destructive">
              We couldn't load this run. Close and try again.
            </p>
          ) : (
            <div className="grid gap-6">
              <div className="flex flex-wrap items-center gap-2">
                <RunStatusBadge status={detail.status} />
                <RunTriggerBadge trigger={detail.trigger} />
                {detail.status === "running" ? (
                  <StopRunButton runId={detail.id} />
                ) : null}
              </div>

              <section className="grid gap-2">
                <h3 className="font-heading text-base font-semibold tracking-tight">Timing & cost</h3>
                <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Started</dt>
                  <dd className="tabular-nums">
                    {formatDateTime(detail.started_at)}
                  </dd>
                  <dt className="text-muted-foreground">Finished</dt>
                  <dd className="tabular-nums">
                    {formatDateTime(detail.finished_at)}
                  </dd>
                  <dt className="text-muted-foreground">Duration</dt>
                  <dd data-testid="run-duration" className="tabular-nums">
                    {formatDuration(detail.started_at, detail.finished_at)}
                  </dd>
                  <dt className="text-muted-foreground">Cost</dt>
                  <dd className="font-medium tabular-nums">
                    {formatCost(detail.cost_usd)}
                  </dd>
                  <dt className="text-muted-foreground">Tokens in / out</dt>
                  <dd className="tabular-nums">
                    {detail.tokens_in} / {detail.tokens_out}
                  </dd>
                </dl>
              </section>

              <section className="grid gap-2">
                <h3 className="font-heading text-base font-semibold tracking-tight">Funnel</h3>
                <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 text-sm">
                  {COUNTERS.map(({ key, label }) => (
                    <div key={key} className="contents">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="tabular-nums">{detail[key] as number}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              {detail.error_summary ? (
                <section className="grid gap-1">
                  <h3 className="font-heading text-base font-semibold tracking-tight">Summary</h3>
                  <p className="text-sm text-muted-foreground text-pretty">
                    {detail.error_summary}
                  </p>
                </section>
              ) : null}

              <section className="grid gap-2">
                <h3 className="font-heading text-base font-semibold tracking-tight">
                  Failures ({detail.errors.length})
                </h3>
                {detail.errors.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Nothing failed in this run.
                  </p>
                ) : (
                  <ul className="grid gap-2">
                    {detail.errors.map((error, index) => (
                      <li
                        key={`${error.company_name}-${error.stage}-${index}`}
                        data-testid="run-error"
                        className="rounded-lg border border-border/70 bg-muted/25 px-3 py-2 text-sm"
                      >
                        <span className="font-medium">
                          {error.company_name ?? "Unknown employer"}
                        </span>
                        <span className="text-muted-foreground">
                          {" "}
                          · {error.stage ?? "unknown stage"}:{" "}
                        </span>
                        <span>{error.message ?? "No detail recorded"}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
