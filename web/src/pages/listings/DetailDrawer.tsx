import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api, apiPost } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { statusLabel, StatusSelect } from "./StatusSelect"
import { formatComp, formatLocation, type JobRow } from "./ListingsTable"

/**
 * D-09: the detail drawer. Total score lives on the row; the drawer carries the
 * breakdown and reasoning (D-11/TRAK-04), the honest open-duration/repost facts
 * (D-13/TRAK-07), freeform notes (TRAK-02) and the timestamped status timeline
 * (TRAK-03). The sanitized description renders as a text node only — never
 * innerHTML (UI-06; the API sanitizer is the first line, this is the second).
 */

export interface JobStatusEvent {
  from_status: string | null
  to_status: string
  changed_at: string
  source: string
}

export interface JobNote {
  id: string
  text: string
  created_at: string
}

export interface DimensionScore {
  score: number | null
  reason: string | null
}

export interface JobDetail extends JobRow {
  description: string
  score_dimensions: Record<string, DimensionScore> | null
  score_summary: string | null
  scored_criteria_version: number | null
  scored_rubric_version: string | null
  scored_with_model: string | null
  filter_tier_reached: string | null
  open_duration_days: number | null
  repost_count: number
  status_events: JobStatusEvent[]
  notes: JobNote[]
}

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "role_fit", label: "Role fit" },
  { key: "seniority_fit", label: "Seniority fit" },
  { key: "employer_fit", label: "Employer fit" },
  { key: "trajectory", label: "Trajectory" },
]

const FLAG_LABELS: Record<string, string> = {
  stretch_role: "Stretch role",
  step_down: "Step down",
  language_requirement: "Language requirement",
  location_ambiguity: "Location ambiguity",
  comp_below_floor: "Comp below floor",
  posting_stale: "Posting stale",
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function scoringStamps(detail: JobDetail): string | null {
  const parts: string[] = []
  if (detail.scored_with_model) parts.push(`Scored by ${detail.scored_with_model}`)
  if (detail.scored_criteria_version !== null) {
    parts.push(`criteria v${detail.scored_criteria_version}`)
  }
  if (detail.scored_rubric_version) {
    parts.push(`rubric ${detail.scored_rubric_version}`)
  }
  return parts.length > 0 ? parts.join(" · ") : null
}

interface DetailDrawerProps {
  jobId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function DetailDrawer({ jobId, open, onOpenChange }: DetailDrawerProps) {
  const queryClient = useQueryClient()
  const [noteText, setNoteText] = useState("")
  const [noteError, setNoteError] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)

  useEffect(() => {
    setNoteText("")
    setNoteError(null)
    setPosting(false)
  }, [jobId])

  const detailQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<JobDetail>(`/api/jobs/${jobId}`),
    enabled: jobId !== null,
  })
  const detail = detailQuery.data

  async function submitNote(event: React.FormEvent) {
    event.preventDefault()
    if (!jobId || noteText.trim().length === 0) return
    setPosting(true)
    setNoteError(null)
    try {
      await apiPost<JobNote>(`/api/jobs/${jobId}/notes`, {
        text: noteText.trim(),
      })
      setNoteText("")
      await queryClient.invalidateQueries({ queryKey: ["job", jobId] })
    } catch {
      setNoteError("Couldn't save that note. Try again.")
    } finally {
      setPosting(false)
    }
  }

  const flags = detail
    ? Object.keys(detail.score_flags ?? {}).filter(
        (key) => detail.score_flags?.[key]
      )
    : []
  const stamps = detail ? scoringStamps(detail) : null

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{detail ? detail.title : "Listing detail"}</SheetTitle>
          <SheetDescription>
            {detail
              ? [detail.company_name, formatLocation(detail)]
                  .filter((part) => part && part !== "—")
                  .join(" · ")
              : "Loading the listing…"}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4 pb-6">
          {detailQuery.isLoading ? (
            <div className="grid gap-5">
              <span role="status" className="sr-only">
                Loading listing…
              </span>
              <div className="grid gap-2">
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-4 w-56" />
              </div>
              <div className="grid gap-2.5">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-16 w-full rounded-lg" />
                <Skeleton className="h-16 w-full rounded-lg" />
                <Skeleton className="h-16 w-full rounded-lg" />
                <Skeleton className="h-16 w-full rounded-lg" />
              </div>
              <div className="grid gap-2">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
              </div>
            </div>
          ) : detailQuery.isError || !detail ? (
            <p role="alert" className="text-sm text-destructive">
              We couldn't load this listing. Close and try again.
            </p>
          ) : (
            <div className="grid gap-6">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
                <span>{formatComp(detail)}</span>
                <a
                  href={detail.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-foreground underline-offset-4 hover:underline"
                >
                  View posting
                </a>
              </div>

              <div className="flex items-center gap-3">
                <span className="text-xs font-medium text-muted-foreground">
                  Stage
                </span>
                <StatusSelect jobId={detail.id} status={detail.status} />
              </div>

              <section aria-labelledby="score-heading" className="grid gap-3">
                <h3
                  id="score-heading"
                  className="font-heading text-base font-semibold tracking-tight"
                >
                  Score
                </h3>
                <div className="flex items-baseline gap-3">
                  <span className="font-heading text-3xl font-semibold tabular-nums">
                    {detail.score_overall === null
                      ? "—"
                      : detail.score_overall.toFixed(2)}
                  </span>
                  {detail.score_summary ? (
                    <p className="text-sm text-muted-foreground text-pretty">
                      {detail.score_summary}
                    </p>
                  ) : null}
                </div>

                <div className="grid gap-2.5">
                  {DIMENSIONS.map(({ key, label }) => {
                    const dimension = detail.score_dimensions?.[key]
                    const score = dimension?.score
                    return (
                      <div
                        key={key}
                        data-testid={`dimension-${key}`}
                        className="rounded-lg border border-border/70 bg-muted/25 px-3 py-2.5"
                      >
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="text-sm font-medium">{label}</span>
                          <span className="font-heading text-sm font-semibold tabular-nums">
                            {score === null || score === undefined
                              ? "—"
                              : `${score}/5`}
                          </span>
                        </div>
                        <div
                          className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted"
                          aria-hidden="true"
                        >
                          <div
                            className="h-full rounded-full bg-primary/70"
                            style={{
                              width:
                                score === null || score === undefined
                                  ? 0
                                  : `${Math.max(0, Math.min(100, (score / 5) * 100))}%`,
                            }}
                          />
                        </div>
                        {dimension?.reason ? (
                          <p className="mt-2 text-sm text-muted-foreground text-pretty">
                            {dimension.reason}
                          </p>
                        ) : null}
                      </div>
                    )
                  })}
                </div>

                {flags.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {flags.map((flag) => (
                      <Badge key={flag} variant="outline" className="font-normal">
                        {FLAG_LABELS[flag] ?? flag}
                      </Badge>
                    ))}
                  </div>
                ) : null}

                {stamps ? (
                  <p className="text-xs text-muted-foreground">{stamps}</p>
                ) : null}
              </section>

              <section className="grid gap-1">
                <h3 className="font-heading text-sm font-semibold tracking-tight">
                  Facts
                </h3>
                <p data-testid="open-duration" className="text-sm">
                  {detail.open_duration_days === null
                    ? "Open duration unknown"
                    : `Open for ${detail.open_duration_days} days`}
                </p>
                <p data-testid="repost-count" className="text-sm">
                  Reposted {detail.repost_count}×
                </p>
              </section>

              <section className="grid gap-3">
                <h3 className="font-heading text-sm font-semibold tracking-tight">
                  Notes
                </h3>
                {detail.notes.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No notes yet.</p>
                ) : (
                  <ul className="grid gap-2">
                    {detail.notes.map((note) => (
                      <li
                        key={note.id}
                        className="rounded-lg border border-border/60 px-3 py-2"
                      >
                        <p className="text-sm whitespace-pre-wrap text-pretty">
                          {note.text}
                        </p>
                        <span className="text-xs text-muted-foreground">
                          {formatDateTime(note.created_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={submitNote} className="grid gap-2">
                  <label
                    htmlFor="drawer-note"
                    className="text-xs font-medium text-muted-foreground"
                  >
                    Add a note
                  </label>
                  <textarea
                    id="drawer-note"
                    value={noteText}
                    maxLength={5000}
                    onChange={(event) => setNoteText(event.target.value)}
                    rows={3}
                    className="w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                    placeholder="What stood out about this role?"
                  />
                  {noteError ? (
                    <p role="alert" className="text-xs text-destructive">
                      {noteError}
                    </p>
                  ) : null}
                  <Button
                    type="submit"
                    size="sm"
                    className="w-fit"
                    disabled={posting || noteText.trim().length === 0}
                  >
                    {posting ? "Saving…" : "Add note"}
                  </Button>
                </form>
              </section>

              <section className="grid gap-3">
                <h3 className="font-heading text-sm font-semibold tracking-tight">
                  Status timeline
                </h3>
                {detail.status_events.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No status changes yet.
                  </p>
                ) : (
                  <ol className="relative grid gap-3 border-l border-border/70 pl-4">
                    {detail.status_events.map((event, index) => (
                      <li
                        key={`${event.to_status}-${event.changed_at}-${index}`}
                        data-testid="timeline-event"
                        className="relative flex items-baseline justify-between gap-3 text-sm"
                      >
                        <span
                          aria-hidden="true"
                          className="absolute top-1.5 -left-5 size-2 rounded-full border-2 border-background bg-primary/60"
                        />
                        <span>
                          {event.from_status
                            ? `${statusLabel(event.from_status)} → ${statusLabel(
                                event.to_status
                              )}`
                            : `→ ${statusLabel(event.to_status)}`}
                        </span>
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {formatDateTime(event.changed_at)}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

              <section className="grid gap-2">
                <h3 className="font-heading text-sm font-semibold tracking-tight">
                  Description
                </h3>
                {/* Sanitized plain text rendered as a text node only (UI-06). */}
                <p
                  data-testid="job-description"
                  className="text-sm leading-relaxed whitespace-pre-wrap text-pretty"
                >
                  {detail.description}
                </p>
              </section>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
