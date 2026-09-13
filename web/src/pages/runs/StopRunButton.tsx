import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CircleStop } from "lucide-react"
import { apiPost } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/**
 * GAP-4: stop a live run. The POST returns 202 the moment the stop is
 * recorded and the control disables while it is honored; the page's existing
 * 3s poll carries the row to its terminal STOPPED status. Stopping is a
 * request, never a kill — the run stops at its next safe boundary.
 */

interface StopRunButtonProps {
  runId: string
  className?: string
}

export function StopRunButton({ runId, className }: StopRunButtonProps) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () =>
      apiPost<{ status: string }>(`/api/runs/${runId}/stop`, {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] })
      await queryClient.invalidateQueries({ queryKey: ["run", runId] })
    },
  })

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={mutation.isPending}
      onClick={(event) => {
        // A row click opens the detail sheet; stopping must not also do that.
        event.stopPropagation()
        mutation.mutate()
      }}
      className={cn(
        "text-destructive hover:bg-destructive/10 hover:text-destructive",
        className
      )}
    >
      <CircleStop aria-hidden="true" />
      {mutation.isPending ? "Stopping…" : "Stop"}
    </Button>
  )
}
