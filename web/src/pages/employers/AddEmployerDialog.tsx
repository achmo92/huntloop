import { useEffect, useState, type FormEvent } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2Icon, PlusIcon } from "lucide-react"
import { apiPost, ApiError } from "@/lib/api"
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
import {
  RESOLUTION_QUEUE_KEY,
  type ResolutionQueueVars,
} from "@/pages/criteria/useResolutionQueue"

/**
 * GAP-8/GAP-11: the Employers page can add an employer by hand. Adding is
 * fire-and-close: POST /api/companies, queue resolution with
 * POST /api/companies/{id}/resolve (202), invalidate the shared caches, and
 * close immediately. Resolution continues in the background; the registry's
 * existing conditional poll lands the new row with its true status.
 *
 * A failed create keeps the dialog open with an inline error. A failed resolve
 * queue still leaves the employer added — the registry shows it "Needs
 * attention" with its own Retry.
 */
type Phase = "idle" | "saving"

export function AddEmployerDialog() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [careersUrl, setCareersUrl] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>("idle")
  const [toast, setToast] = useState<string | null>(null)

  const queue = useMutation({
    mutationKey: RESOLUTION_QUEUE_KEY,
    mutationFn: (vars: ResolutionQueueVars) =>
      apiPost(`/api/companies/${vars.rows[0].id}/resolve`, {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
      await queryClient.invalidateQueries({ queryKey: ["coverage"] })
    },
  })

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

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

    // GAP-11: fire-and-close. Queue resolution (202), let the registry's
    // existing conditional poll land the row, and close immediately.
    queue.mutate({
      rows: [{ id: created.id, last_checked_at: created.last_checked_at }],
    })
    await queryClient.invalidateQueries({ queryKey: ["companies"] })
    await queryClient.invalidateQueries({ queryKey: ["coverage"] })
    setOpen(false)
    setName("")
    setCareersUrl("")
    setError(null)
    setPhase("idle")
    setToast(`Added ${created.name}.`)
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
                Add a company by name — we'll start finding their job board in
                the background.
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
                {busy ? "Saving…" : "Add"}
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
