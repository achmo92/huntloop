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
        <dl className="grid gap-2.5">
          {STAGES.map(({ key, label }) => {
            const value = funnel[key]
            const width = `${Math.round((value / peak) * 100)}%`
            return (
              <div
                key={key}
                data-testid="funnel-stage"
                data-stage={key}
                className="grid grid-cols-[10.5rem_1fr_3rem] items-center gap-3"
              >
                <dt className="text-sm text-muted-foreground">{label}</dt>
                <div
                  className="h-2 overflow-hidden rounded-full bg-muted"
                  role="img"
                  aria-label={`${label}: ${value}`}
                >
                  <div
                    className="h-full rounded-full bg-primary/70"
                    style={{ width }}
                  />
                </div>
                <dd className="text-right text-sm tabular-nums">{value}</dd>
              </div>
            )
          })}
        </dl>
      </CardContent>
    </Card>
  )
}
