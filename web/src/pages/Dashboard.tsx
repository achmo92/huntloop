import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router-dom"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  JOB_STATUSES,
  STATUS_LABELS,
} from "@/pages/listings/StatusSelect"
import { runStatusLabel, runStatusTone } from "@/pages/runs/types"
import { FunnelPanel } from "./dashboard/FunnelPanel"
import { RunNowButton } from "./dashboard/RunNowButton"
import type { DashboardResponse, SettingsTimezoneResponse } from "./dashboard/types"

/**
 * D-14 / UI-02: the landing page IS the pipeline dashboard. One aggregate
 * request answers "what happened, what's next, where do things stand" and a
 * quiet week is explained rather than left ambiguous. Run now is
 * fire-and-observe (D-17): it starts a run and then watches, never blocks.
 */

const SHORTCUTS = [
  { to: "/listings", label: "Listings" },
  { to: "/employers", label: "Employers" },
  { to: "/criteria", label: "Criteria" },
  { to: "/settings", label: "Settings" },
]

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatCost(value: number | null): string {
  if (value === null || value === undefined) return "$0.0000"
  return `$${value.toFixed(4)}`
}

function formatNextRun(iso: string | null, timeZone?: string): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone,
    }).format(date)
  } catch {
    return date.toLocaleString()
  }
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [watching, setWatching] = useState(false)

  const dashboardQuery = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<DashboardResponse>("/api/dashboard"),
    refetchInterval: (query) => {
      const status = query.state.data?.last_run?.status
      return status === "running" || watching ? 3000 : false
    },
  })

  // The next run is formatted in the configured schedule timezone, which lives
  // in the settings section. A failure here must never blank the dashboard.
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<SettingsTimezoneResponse>("/api/settings"),
    staleTime: 60_000,
    retry: false,
  })

  const dashboard = dashboardQuery.data
  const lastStatus = dashboard?.last_run?.status

  // After a manual trigger the new RUNNING row can take a beat to appear, so
  // keep polling for a bounded window even before we see "running".
  useEffect(() => {
    if (!watching) return
    const timer = setTimeout(() => setWatching(false), 60_000)
    return () => clearTimeout(timer)
  }, [watching])

  if (dashboardQuery.isLoading) {
    return (
      <p className="text-sm text-muted-foreground">Loading dashboard…</p>
    )
  }

  if (dashboardQuery.isError || !dashboard) {
    return (
      <p role="alert" className="text-sm text-destructive">
        We couldn't load your dashboard. Refresh to try again.
      </p>
    )
  }

  const isFirstTime = dashboard.last_run === null && dashboard.total_listings === 0
  const nextRun = formatNextRun(
    dashboard.next_scheduled_run,
    settingsQuery.data?.schedule.timezone
  )

  return (
    <div className="grid gap-6">
      <header className="grid gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-heading text-2xl font-semibold tracking-tight">
              Dashboard
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              What happened, what's next, and where things stand.
            </p>
          </div>
          <RunNowButton
            isRunning={lastStatus === "running"}
            onStarted={() => setWatching(true)}
          />
        </div>
        <nav aria-label="Shortcuts" className="flex flex-wrap gap-2">
          {SHORTCUTS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="rounded-lg border border-border px-2.5 py-1 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      {isFirstTime ? (
        <EmptyState
          title="Welcome to HuntLoop"
          description="Describe what you're looking for and we'll start watching employers for you."
          actionLabel="Start onboarding"
          onAction={() => navigate("/onboarding")}
        />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {nextRun
              ? `Next run: ${nextRun}`
              : "No schedule set — configure it in Settings"}
          </p>

          <Card>
            <CardHeader className="flex-row items-start justify-between gap-3">
              <div>
                <CardTitle>Last run</CardTitle>
                <CardDescription>
                  {dashboard.last_run
                    ? formatDateTime(dashboard.last_run.started_at)
                    : "No runs yet"}
                </CardDescription>
              </div>
              {dashboard.last_run ? (
                <Badge
                  data-tone={runStatusTone(dashboard.last_run.status)}
                  variant={
                    runStatusTone(dashboard.last_run.status) === "error"
                      ? "destructive"
                      : "secondary"
                  }
                >
                  {runStatusLabel(dashboard.last_run.status)}
                </Badge>
              ) : null}
            </CardHeader>
            <CardContent className="grid gap-2">
              {dashboard.last_run ? (
                <>
                  <p className="text-sm">
                    {dashboard.last_run.companies_checked} employers ·{" "}
                    {dashboard.last_run.listings_fetched} fetched ·{" "}
                    {dashboard.last_run.scored} scored ·{" "}
                    {dashboard.last_run.new_jobs_written} written
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Cost {formatCost(dashboard.last_run.cost_usd)}
                  </p>
                  {dashboard.last_run.error_summary ? (
                    <p className="text-sm text-muted-foreground">
                      {dashboard.last_run.error_summary}
                    </p>
                  ) : null}
                  <Link
                    to="/runs"
                    className="w-fit text-sm font-medium text-foreground underline-offset-4 hover:underline"
                  >
                    View run history
                  </Link>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No runs yet — start one with Run now.
                </p>
              )}
            </CardContent>
          </Card>

          {dashboard.funnel ? <FunnelPanel funnel={dashboard.funnel} /> : null}

          <Card>
            <CardHeader>
              <CardTitle>Listings by status</CardTitle>
              <CardDescription>
                {dashboard.total_listings} tracked listing
                {dashboard.total_listings === 1 ? "" : "s"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-3">
                {JOB_STATUSES.map((status) => (
                  <div
                    key={status}
                    className="rounded-lg border border-border/60 px-3 py-2"
                  >
                    <div className="text-xs text-muted-foreground">
                      {STATUS_LABELS[status]}
                    </div>
                    <div
                      data-testid="listing-status-count"
                      className="font-heading text-lg font-medium tabular-nums"
                    >
                      {dashboard.listings_by_status[status] ?? 0}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
