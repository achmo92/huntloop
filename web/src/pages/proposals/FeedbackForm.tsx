import { useId, useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

/**
 * D-07: the general (not per-listing) freeform feedback box. It lives on the
 * proposal review surface so one page owns both "tell HuntLoop what it's
 * getting wrong" and "review what came of it". Text submitted here becomes a
 * FeedbackNote with job_id NULL — it is never attached to a listing, which is
 * why there is deliberately no listing selector anywhere in this component.
 *
 * The component owns only its own display state (text, submitting, confirmed,
 * failed). It performs no network call: the page (plan 05-07) owns the
 * mutation, so this stays trivially testable and there is exactly one place
 * that knows the feedback endpoint.
 */

export interface FeedbackFormProps {
  onSubmit: (text: string) => Promise<void>
}

/** Mirrors MAX_FEEDBACK_CHARS in src/huntloop/api/routers/feedback.py. */
const MAX_FEEDBACK_CHARS = 4000

export function FeedbackForm({ onSubmit }: FeedbackFormProps) {
  const fieldId = useId()
  const [text, setText] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [failed, setFailed] = useState(false)

  async function send() {
    if (submitting || text.trim().length === 0) return
    setSubmitting(true)
    setConfirmed(false)
    setFailed(false)
    try {
      await onSubmit(text.trim())
      setText("")
      setConfirmed(true)
    } catch {
      setFailed(true)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        void send()
      }}
    >
      <div className="grid gap-2">
        <Label htmlFor={fieldId}>Feedback</Label>
        <Textarea
          id={fieldId}
          rows={3}
          maxLength={4000}
          placeholder="Tell HuntLoop what it's getting wrong about the listings you're seeing."
          value={text}
          disabled={submitting}
          onChange={(event) => {
            setText(event.target.value)
            // New input supersedes the previous outcome, either way.
            if (confirmed) setConfirmed(false)
            if (failed) setFailed(false)
          }}
        />
      </div>

      <div className="flex items-center gap-4">
        <Button
          type="submit"
          disabled={submitting || text.trim().length === 0}
        >Send feedback</Button>
        <span className="text-sm text-muted-foreground">
          {submitting
            ? "Sending…"
            : `Up to ${MAX_FEEDBACK_CHARS.toLocaleString()} characters.`}
        </span>
      </div>

      {confirmed ? (
        <p
          role="status"
          className="text-sm leading-relaxed text-muted-foreground"
        >
          Thanks — this will be considered next time proposals are generated.
        </p>
      ) : null}

      {failed ? (
        <p role="alert" className="text-sm text-destructive">
          That didn't save. Try again.
        </p>
      ) : null}
    </form>
  )
}
