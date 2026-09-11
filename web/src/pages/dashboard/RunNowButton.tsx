import { useEffect, useState, type ComponentProps } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiPost, ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/**
 * D-17 / RUN-02: "Run now" is fire-and-observe. The POST returns 202 the
 * moment the run is accepted and the page never waits for the run itself —
 * the caller's queries are nudged and then poll while the RUNNING row is live.
 * A 409 is not an error state: it is the overlap guard answering in a
 * sentence ("a run is already in progress"), which we surface verbatim.
 */

interface RunNowButtonProps {
  /** True when a run is already live (from the caller's run list). */
  isRunning?: boolean
  /** Called after a 202 so the caller can begin polling for the new run. */
  onStarted?: () => void
  className?: string
  size?: ComponentProps<typeof Button>["size"]
  variant?: ComponentProps<typeof Button>["variant"]
}

export function RunNowButton({
  isRunning = false,
  onStarted,
  className,
  size = "default",
  variant = "default",
}: RunNowButtonProps) {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [isFailure, setIsFailure] = useState(false)

  useEffect(() => {
    if (message === null) return
    const timer = setTimeout(() => setMessage(null), 5000)
    return () => clearTimeout(timer)
  }, [message])

  async function startRun() {
    setPending(true)
    setIsFailure(false)
    try {
      await apiPost<{ status: string }>("/api/runs", {})
      setMessage("Run started")
      onStarted?.()
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
      await queryClient.invalidateQueries({ queryKey: ["runs"] })
    } catch (error) {
      setIsFailure(true)
      if (error instanceof ApiError && error.status === 409) {
        // The overlap guard names the run in progress — show its words.
        setMessage(error.detail)
      } else {
        setMessage(
          error instanceof ApiError
            ? error.detail
            : "Couldn't start a run. Try again."
        )
      }
    } finally {
      setPending(false)
    }
  }

  const running = pending || isRunning

  return (
    <>
      <Button
        type="button"
        size={size}
        variant={variant}
        disabled={running}
        onClick={startRun}
        className={className}
      >
        {running ? "Running…" : "Run now"}
      </Button>
      {message !== null ? (
        <div
          role="status"
          data-testid="run-now-toast"
          className={cn(
            "fixed right-4 bottom-4 z-50 max-w-sm rounded-lg border bg-popover px-3 py-2 text-sm shadow-md",
            isFailure
              ? "border-destructive/40 text-destructive"
              : "border-border text-popover-foreground"
          )}
        >
          {message}
        </div>
      ) : null}
    </>
  )
}
