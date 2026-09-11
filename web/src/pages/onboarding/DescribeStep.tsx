import { useState, type FormEvent } from "react"
import { Loader2Icon } from "lucide-react"
import { apiPost, ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"

interface DescribeStepProps {
  onSuggested: (suggested: unknown) => void
}

/**
 * D-01: describe-first. One freeform paragraph in, a structured draft out.
 * There is deliberately no re-describe loop — corrections happen in the form
 * (D-02) — so a failure here offers retry, not a conversation.
 */
export function DescribeStep({ onSuggested }: DescribeStepProps) {
  const [text, setText] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed) {
      setError("Write a sentence or two about what you're looking for.")
      return
    }
    setLoading(true)
    setError(null)
    try {
      const result = await apiPost<{ suggested: unknown }>(
        "/api/criteria/describe",
        { text: trimmed }
      )
      onSuggested(result.suggested)
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "We couldn't read that description — try again."
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={submit} className="grid max-w-2xl gap-4">
      <div>
        <h2 className="font-heading text-lg font-medium">
          Describe what you're looking for
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A paragraph is enough — role, places, level, salary, what to avoid.
          We'll turn it into an editable summary you can correct.
        </p>
      </div>
      <label htmlFor="criteria-description" className="sr-only">
        Your description
      </label>
      <textarea
        id="criteria-description"
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          if (error) setError(null)
        }}
        rows={6}
        placeholder="e.g. I'm looking for a senior backend role, remote in the EU or UK, around €80k, no on-call-heavy startups…"
        className="w-full rounded-xl border border-input bg-transparent px-3 py-2.5 text-sm leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex items-center gap-3">
        <Button type="submit" size="lg" disabled={loading}>
          {loading ? (
            <>
              <Loader2Icon className="animate-spin" />
              Reading your description…
            </>
          ) : error ? (
            "Try again"
          ) : (
            "Continue"
          )}
        </Button>
        <span className="text-sm text-muted-foreground">
          Nothing is saved until you review the form.
        </span>
      </div>
    </form>
  )
}
