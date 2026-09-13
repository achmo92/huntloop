import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { apiPatch } from "@/lib/api"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/**
 * D-10 / TRAK-01: one action moves a listing to any fixed pipeline stage.
 * Picking a stage in this dropdown PATCHes immediately and the row reflects it
 * without a dialog or a second click — the zero-friction transition is the
 * feedback loop's only signal source, so it is deliberate, not an oversight
 * (04-CONTEXT "Specific Ideas").
 */

export const JOB_STATUSES = [
  "new",
  "shortlisted",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "withdrawn",
] as const

export type JobStatus = (typeof JOB_STATUSES)[number]

export const STATUS_LABELS: Record<JobStatus, string> = {
  new: "New",
  shortlisted: "Shortlisted",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
}

export function statusLabel(value: string | null | undefined): string {
  if (value && value in STATUS_LABELS) {
    return STATUS_LABELS[value as JobStatus]
  }
  return value ?? "—"
}

interface StatusUpdateOut {
  id: string
  status: string
  changed_at: string
}

interface StatusSelectProps {
  jobId: string
  status: string
  onChanged?: (status: JobStatus) => void
  className?: string
}

export function StatusSelect({
  jobId,
  status,
  onChanged,
  className,
}: StatusSelectProps) {
  const queryClient = useQueryClient()
  // Local mirror so a row shows the move instantly; the jobs + detail queries
  // are invalidated right after the PATCH so the server stays the truth.
  const [current, setCurrent] = useState(status)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setCurrent(status)
  }, [status])

  async function change(next: string) {
    if (!next || next === current) return
    const previous = current
    setCurrent(next)
    setError(null)
    try {
      await apiPatch<StatusUpdateOut>(`/api/jobs/${jobId}/status`, {
        status: next,
      })
      onChanged?.(next as JobStatus)
      await queryClient.invalidateQueries({ queryKey: ["jobs"] })
      await queryClient.invalidateQueries({ queryKey: ["job", jobId] })
    } catch {
      setCurrent(previous)
      setError("Couldn't move that listing. Try again.")
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Select
        value={current}
        items={STATUS_LABELS}
        onValueChange={(value) =>
          change(typeof value === "string" ? value : "")
        }
      >
        <SelectTrigger
          size="sm"
          aria-label="Pipeline stage"
          className={cn("min-w-32 justify-between", className)}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {JOB_STATUSES.map((option) => (
            <SelectItem key={option} value={option}>
              {STATUS_LABELS[option]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  )
}
