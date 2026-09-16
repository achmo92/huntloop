import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import {
  describePredictedEffect,
  formatFieldLabel,
  formatRejectionDate,
} from "./predictedEffect"
import type { ProposalOut } from "./types"

/**
 * One proposal, rendered for review (UI-03 / D-11).
 *
 * The card states the four things a reviewer needs before consenting: what
 * would change (field label + before → after), why (the rationale), which
 * listings prove it (LOOP-05 evidence), and what it would do to the backlog
 * (LOOP-06, in prose — never a chart). A same-signature rejection shows inline
 * with its date and stated reason (LOOP-09), so a repeatedly-rejected
 * suggestion is visibly a pattern rather than a silent veto or a silent
 * resurface.
 *
 * `Approve change` is the only brand-toned control; `Reject change` is the
 * neutral outline variant and opens no confirmation dialog — rejecting a
 * suggestion is not data loss (05-UI-SPEC Color section).
 *
 * This component performs no I/O. The page (plan 05-07) owns the mutation, so
 * the card stays trivially testable and the only state it owns is the optional
 * one-line rejection reason.
 */

export interface ProposalCardProps {
  proposal: ProposalOut
  busy?: boolean
  error?: boolean
  onApprove: (id: string) => void
  onReject: (id: string, reason: string | null) => void
}

/** Readable text for a criteria value; objects are compacted, not dumped raw. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "none"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return JSON.stringify(value)
}

export function ProposalCard({
  proposal,
  busy = false,
  error = false,
  onApprove,
  onReject,
}: ProposalCardProps) {
  const [reason, setReason] = useState("")
  const change = proposal.proposed_changes
  const evidence = proposal.evidence
  // The API returns prior_rejections newest-first.
  const latestRejection = proposal.prior_rejections[0] ?? null

  function approve() {
    onApprove(proposal.id)
  }

  function reject() {
    const trimmed = reason.trim()
    onReject(proposal.id, trimmed.length > 0 ? trimmed : null)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base leading-snug font-semibold">
          {formatFieldLabel(change.field)}
        </CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-2">
          <span className="line-through">
            {formatValue(change.current_value)}
          </span>
          <span aria-hidden="true">→</span>
          <span className="text-foreground">
            {formatValue(change.proposed_value)}
          </span>
        </CardDescription>
      </CardHeader>

      {latestRejection ? (
        <div
          data-testid="rejection-history"
          className="grid gap-2 px-(--card-spacing)"
        >
          <Badge
            variant="warning"
            className="h-auto max-w-full rounded-lg py-1 text-left text-sm leading-relaxed font-normal whitespace-normal"
          >
            You rejected a similar proposal on{" "}
            {formatRejectionDate(latestRejection.decided_at)}
          </Badge>
          {latestRejection.rejection_reason ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              {latestRejection.rejection_reason}
            </p>
          ) : null}
        </div>
      ) : null}

      <CardContent className="grid gap-4">
        <p className="text-sm leading-relaxed">{proposal.rationale}</p>

        {evidence ? (
          <ul className="bg-muted/40 rounded-lg p-2 text-sm grid gap-2">
            {evidence.listings.map((listing) => (
              <li key={listing.job_id} className="grid gap-1">
                <span className="font-semibold">{listing.title}</span>
                <span className="text-muted-foreground">
                  {listing.company}
                </span>
                {listing.attributed_to ? (
                  <span className="text-muted-foreground">
                    {listing.attributed_to}
                  </span>
                ) : null}
              </li>
            ))}
            {evidence.feedback_quotes.map((quote) => (
              <li
                key={quote.note_id}
                className="text-muted-foreground italic"
              >
                {quote.quote}
              </li>
            ))}
            {evidence.truncated ? (
              <li className="text-muted-foreground">
                More listings matched than are shown here.
              </li>
            ) : null}
          </ul>
        ) : null}

        <p className="text-sm leading-relaxed">
          {describePredictedEffect(proposal.predicted_effect)}
        </p>
      </CardContent>

      {proposal.status === "pending" ? (
        <CardFooter className="flex flex-wrap items-center gap-4">
          <Textarea
            aria-label="Reason (optional)"
            className="min-w-0 flex-1"
            rows={1}
            placeholder="Optional: why not?"
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="flex items-center gap-2">
            <Button variant="outline" disabled={busy} onClick={reject}>Reject change</Button>
            <Button disabled={busy} onClick={approve}>Approve change</Button>
          </div>
          {error ? (
            <p role="alert" className="basis-full text-sm text-destructive">
              That didn't save. Try again.
            </p>
          ) : null}
        </CardFooter>
      ) : null}
    </Card>
  )
}
