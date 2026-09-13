import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { Funnel } from "./types"

/**
 * D-14: the funnel shape is the explanation of a quiet week. Every one of the
 * seven stages ALWAYS renders — zeros included — because "nothing survived
 * filtering" and "nothing was fetched" are different answers and the user must
 * be able to tell them apart at a glance. A proportional stage list (no chart
 * library) is the calmer reading here: no axes, no legend, just the pipeline
 * narrowing as you read down.
 */

const STAGES: { key: keyof Funnel; label: string }[] = [
  { key: "companies_checked", label: "Employers checked" },
  { key: "listings_fetched", label: "Listings fetched" },
  { key: "after_dedup", label: "After dedup" },
  { key: "after_deterministic", label: "After filtering" },
  { key: "after_triage", label: "After triage" },
  { key: "scored", label: "Scored" },
  { key: "new_jobs_written", label: "New jobs written" },
]

export function FunnelPanel({ funnel }: { funnel: Funnel }) {
  const peak = Math.max(1, ...STAGES.map(({ key }) => funnel[key]))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Last run's funnel</CardTitle>
        <CardDescription>
          How this run narrowed, stage by stage — the answer to a quiet week.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3">
          {STAGES.map(({ key, label }) => {
            const value = funnel[key]
            const width = `${Math.round((value / peak) * 100)}%`
            return (
              <div
                key={key}
                data-testid="funnel-stage"
                data-stage={key}
                className="grid grid-cols-[minmax(7rem,11rem)_1fr_3.5rem] items-center gap-3"
              >
                <dt className="truncate text-sm text-muted-foreground">
                  {label}
                </dt>
                <div
                  className="h-2.5 overflow-hidden rounded-full bg-muted ring-1 ring-inset ring-border/60"
                  role="img"
                  aria-label={`${label}: ${value}`}
                >
                  <div
                    className="h-full rounded-full bg-primary/75"
                    style={{ width }}
                  />
                </div>
                <dd className="text-right font-heading text-sm font-medium tabular-nums">
                  {value}
                </dd>
              </div>
            )
          })}
        </dl>
      </CardContent>
    </Card>
  )
}
