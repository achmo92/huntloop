import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, apiPost } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { PageHeader } from "@/components/PageHeader"
import { SkeletonTable } from "@/components/ui/skeleton"
import { FeedbackForm } from "./FeedbackForm"
import { ProposalCard } from "./ProposalCard"
import type { ProposalOut } from "./types"

/**
 * UI-03: the proposal review surface (D-11) and D-07's general feedback box,
 * on one page — one surface owns both "tell HuntLoop what it's getting wrong"
 * and "review what came of it".
 *
 * All four endpoint wirings live here, deliberately and only here:
 *   GET  /api/proposals?status=pending&limit=50
 *   POST /api/proposals/{id}/accept
 *   POST /api/proposals/{id}/reject
 *   POST /api/feedback
 * ProposalCard and FeedbackForm take callbacks, not network clients, so the
 * composition is asserted end to end in ProposalsPage.test.tsx.
 *
 * The query deliberately does not poll: proposals only change on approval,
 * rejection, or the next discovery run — polling a review surface is noise.
 */

const PROPOSALS_KEY = ["proposals", "pending"] as const

export default function ProposalsPage() {
  const queryClient = useQueryClient()
  const [actingId, setActingId] = useState<string | null>(null)
  const [failedId, setFailedId] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: PROPOSALS_KEY,
    queryFn: () => api<ProposalOut[]>("/api/proposals?status=pending&limit=50"),
  })

  const accept = useMutation({
    mutationFn: (id: string) =>
      apiPost<{ version: number }>(`/api/proposals/${id}/accept`, {}),
    onMutate: (id: string) => {
      setActingId(id)
      setFailedId(null)
    },
    onError: (_error: unknown, id: string) => {
      setFailedId(id)
    },
    onSettled: () => {
      setActingId(null)
      // Settled, not success: a 409 means someone else decided it, so the
      // list is stale either way and must resync.
      void queryClient.invalidateQueries({ queryKey: PROPOSALS_KEY })
    },
  })

  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string | null }) =>
      apiPost<{ status: string }>(`/api/proposals/${id}/reject`, { reason }),
    onMutate: ({ id }: { id: string; reason: string | null }) => {
      setActingId(id)
      setFailedId(null)
    },
    onError: (
      _error: unknown,
      { id }: { id: string; reason: string | null }
    ) => {
      setFailedId(id)
    },
    onSettled: () => {
      setActingId(null)
      void queryClient.invalidateQueries({ queryKey: PROPOSALS_KEY })
    },
  })

  // Written feedback is consumed at the next generation pass (D-08), not
  // immediately — so it deliberately does not invalidate the proposals query.
  const feedback = useMutation({
    mutationFn: (text: string) =>
      apiPost<{ id: string }>("/api/feedback", { text }),
  })

  const proposals = data ?? []

  return (
    <div className="grid gap-8">
      <PageHeader
        title="Proposals"
        description="Evidence-backed criteria changes drawn from what actually happened to your listings. Nothing changes until you approve it."
      />

      {isLoading ? (
        <div className="grid gap-4">
          <span role="status" className="sr-only">
            Loading your proposals…
          </span>
          <SkeletonTable rows={3} />
        </div>
      ) : isError ? (
        <p role="alert" className="text-sm text-destructive">
          We couldn't load your proposals. Refresh to try again.
        </p>
      ) : proposals.length === 0 ? (
        <EmptyState
          title="No proposals yet"
          description="Once discovery has run a few times and listings have moved through your pipeline, HuntLoop will suggest evidence-backed changes here — nothing changes until you approve it."
        />
      ) : (
        <div className="grid gap-4">
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              busy={actingId === proposal.id}
              error={failedId === proposal.id}
              onApprove={(id) => accept.mutate(id)}
              onReject={(id, reason) => reject.mutate({ id, reason })}
            />
          ))}
        </div>
      )}

      <FeedbackForm
        onSubmit={(text) => feedback.mutateAsync(text).then(() => undefined)}
      />
    </div>
  )
}
