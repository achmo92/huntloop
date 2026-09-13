import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { HistoryIcon } from "lucide-react"
import { api, apiPost } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { CriteriaForm, type CriteriaPayload } from "./criteria/CriteriaForm"
import { HistoryView } from "./criteria/HistoryView"
import type { CriteriaCurrentResponse } from "./criteria/types"

/**
 * The criteria page: the current version, editable through the SAME form the
 * intake review step uses (D-02 — the model never edits behind the user's
 * back), with a small "vN of M — view history" affordance (D-04). Day to day
 * the user just sees current; history is a peek, not the default.
 */
export default function Criteria() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [savedVersion, setSavedVersion] = useState<number | null>(null)

  const criteriaQuery = useQuery({
    queryKey: ["criteria"],
    queryFn: () => api<CriteriaCurrentResponse>("/api/criteria"),
  })

  async function handleSave(payload: CriteriaPayload) {
    const result = await apiPost<{ version: number }>("/api/criteria", payload)
    setSavedVersion(result.version)
    await queryClient.invalidateQueries({ queryKey: ["criteria"] })
  }

  if (criteriaQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading your criteria…</p>
  }

  const current = criteriaQuery.data?.current
  const total = criteriaQuery.data?.total_versions ?? 0

  if (!current) {
    return (
      <EmptyState
        title="Describe what you're looking for"
        description="Tell us in a paragraph what you want, and we'll turn it into criteria you can correct. Nothing is saved until you review it."
        actionLabel="Set up your search"
        onAction={() => navigate("/criteria")}
      />
    )
  }

  return (
    <div className="grid max-w-3xl gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
            Your criteria
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            v{current.version} of {total}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setHistoryOpen(true)}
        >
          <HistoryIcon />
          View history
        </Button>
      </header>

      {savedVersion !== null ? (
        <p
          role="status"
          className="rounded-lg bg-muted px-3 py-2 text-sm text-foreground"
        >
          Saved as version {savedVersion}. Earlier versions stay available in
          history.
        </p>
      ) : null}

      <CriteriaForm
        defaultValues={current.payload}
        onSubmit={handleSave}
        submitLabel="Save new version"
      />

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Criteria history</SheetTitle>
            <SheetDescription>
              Pick any two versions to see what changed between them.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-4">
            <HistoryView />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
