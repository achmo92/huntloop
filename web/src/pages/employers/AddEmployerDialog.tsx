import { useEffect, useState, type FormEvent } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2Icon, PlusIcon } from "lucide-react"
import { api, apiPost, ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import type { CompanyOut } from "@/pages/criteria/types"

/**
 * GAP-8/D-8a/D-8b: the Employers page can add an employer by hand. Adding is
 * add-and-resolve: POST /api/companies, immediately queue resolution with
 * POST /api/companies/{id}/resolve, then watch the shared ["companies"] cache
 * until the new row's status lands — or a bounded timeout releases the dialog.
 * A resolve failure still added the employer, so it must never block; the row
 * appears "Needs attention" with its own Retry.
 */
export const RESOLVE_TIMEOUT_MS = 30_000

type Phase = "idle" | "saving" | "resolving"

export function AddEmployerDialog() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [careersUrl, setCareersUrl] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>("idle")
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [baselineCheckedAt, setBaselineCheckedAt] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
    enabled: pendingId !== null,
    refetchInterval: pendingId ? 2000 : false,
  })

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  // The probe is done once the row resolves or its last_checked_at changes
  // (a repeated failure is still a completed probe — polling must terminate).
  useEffect(() => {
    if (!pendingId) return
    const company = companiesQuery.data?.find(
      (candidate) => candidate.id === pendingId
    )
    if (!company) return
    if (company.resolved || company.last_checked_at !== baselineCheckedAt) {
      const message = company.resolved
        ? `Added ${company.name}.`
        : `Added ${company.name} — we couldn't find their job board automatically.`
      finish(message)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companiesQuery.data, pendingId, baselineCheckedAt])

  // Bounded timeout: never leave the dialog waiting on a probe that may run long.
  useEffect(() => {
    if (!pendingId) return
    const timer = setTimeout(() => {
      finish(
        `Added ${name.trim()} — still finding their job board; it'll update on the page.`
      )
    }, RESOLVE_TIMEOUT_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingId])

  function finish(message: string) {
    void queryClient.invalidateQueries({ queryKey: ["companies"] })
    void queryClient.invalidateQueries({ queryKey: ["coverage"] })
    setOpen(false)
    setName("")
    setCareersUrl("")
    setError(null)
    setPhase("idle")
    setPendingId(null)
    setBaselineCheckedAt(null)
    setToast(message)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (phase !== "idle") return
    const trimmed = name.trim()
    if (!trimmed) {
      setError("Enter a name")
      return
    }
    setError(null)
    setPhase("saving")

    let created: CompanyOut
    try {
      created = await apiPost("/api/companies", {
        name: trimmed,
        careers_url: careersUrl.trim() || null,
      })
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Couldn't add that employer."
      )
      setPhase("idle")
      return
    }

    setPendingId(created.id)
    setBaselineCheckedAt(created.last_checked_at)
    setPhase("resolving")

    try {
      await apiPost(`/api/companies/${created.id}/resolve`, {})
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
      await queryClient.invalidateQueries({ queryKey: ["coverage"] })
    } catch (err) {
      // The employer EXISTS — never block. It lands "Needs attention" with its
      // own Retry; report why resolution didn't start.
      finish(
        err instanceof ApiError
          ? err.detail
          : "Added them, but couldn't start finding their job board."
      )
    }
  }

  const busy = phase !== "idle"

  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger render={<Button type="button" />}>
          <PlusIcon />
          Add employer
        </DialogTrigger>
        <DialogContent>
          <form onSubmit={submit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Add employer</DialogTitle>
              <DialogDescription>
                Add a company by name — we'll start finding their job board
                automatically.
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-2">
              <Label htmlFor="add-employer-name">Name</Label>
              <Input
                id="add-employer-name"
                value={name}
                disabled={busy}
                placeholder="Hooli"
                autoComplete="off"
                onChange={(event) => setName(event.target.value)}
              />
            </div>

            <div className="grid gap-2">
              <Label htmlFor="add-employer-careers">
                Careers URL{" "}
                <span className="font-normal text-muted-foreground">
                  (optional)
                </span>
              </Label>
              <Input
                id="add-employer-careers"
                type="url"
                value={careersUrl}
                disabled={busy}
                placeholder="https://…"
                autoComplete="off"
                onChange={(event) => setCareersUrl(event.target.value)}
              />
            </div>

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}

            <DialogFooter>
              <DialogClose
                render={
                  <Button type="button" variant="outline" disabled={busy} />
                }
              >
                Cancel
              </DialogClose>
              <Button type="submit" disabled={busy}>
                {busy ? <Loader2Icon className="animate-spin" /> : null}
                {phase === "saving"
                  ? "Saving…"
                  : phase === "resolving"
                    ? "Finding their job board…"
                    : "Add"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {toast ? (
        <div
          role="status"
          className="fixed right-4 bottom-4 z-50 rounded-lg border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md"
        >
          {toast}
        </div>
      ) : null}
    </>
  )
}
