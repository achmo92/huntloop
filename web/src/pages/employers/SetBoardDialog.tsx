import { useEffect, useState, type FormEvent } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import { apiPatch, ApiError } from "@/lib/api"
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
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { CompanyOut } from "@/pages/criteria/types"

/**
 * GAP-14: when automatic resolution cannot crack an employer, the Error row
 * offers "Set board manually". This dialog collects a supported platform (the
 * resolver's own adapter set), the board id/slug, and an optional careers URL,
 * then PATCHes /api/companies/{id}; the server records the resolution as
 * manual and marks the row Resolved. A 4xx (unsupported platform, missing
 * identifier) is shown inline and the dialog stays open.
 */

const PLATFORM_OPTIONS = [
  { value: "greenhouse", label: "Greenhouse" },
  { value: "lever", label: "Lever" },
  { value: "ashby", label: "Ashby" },
]

const PLATFORM_ITEMS = Object.fromEntries(
  PLATFORM_OPTIONS.map((option) => [option.value, option.label])
)

interface SetBoardDialogProps {
  company: CompanyOut | null
  onClose: () => void
}

export function SetBoardDialog({ company, onClose }: SetBoardDialogProps) {
  const queryClient = useQueryClient()
  const [platform, setPlatform] = useState("greenhouse")
  const [boardId, setBoardId] = useState("")
  const [careersUrl, setCareersUrl] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // A new target employer starts from a clean form; an existing careers URL is
  // carried over so Save never drops it.
  useEffect(() => {
    if (!company) return
    setPlatform("greenhouse")
    setBoardId("")
    setCareersUrl(company.careers_url ?? "")
    setError(null)
    setSaving(false)
  }, [company])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!company || saving) return
    const identifier = boardId.trim()
    if (!identifier) {
      setError("Enter the board id or slug.")
      return
    }
    setError(null)
    setSaving(true)
    try {
      await apiPatch(`/api/companies/${company.id}`, {
        ats: platform,
        ats_identifier: identifier,
        careers_url: careersUrl.trim() || null,
      })
      await queryClient.invalidateQueries({ queryKey: ["companies"] })
      await queryClient.invalidateQueries({ queryKey: ["coverage"] })
      onClose()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Couldn't save that job board."
      )
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={company !== null}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Set job board</DialogTitle>
            <DialogDescription>
              We'll watch this board instead of guessing.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-2">
            <Label htmlFor="set-board-platform">Platform</Label>
            <Select
              value={platform}
              items={PLATFORM_ITEMS}
              onValueChange={(next) => {
                if (typeof next === "string" && next) setPlatform(next)
              }}
            >
              <SelectTrigger
                id="set-board-platform"
                aria-label="Platform"
                className="w-full"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PLATFORM_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="set-board-identifier">Board id or slug</Label>
            <Input
              id="set-board-identifier"
              value={boardId}
              disabled={saving}
              placeholder="acme"
              autoComplete="off"
              onChange={(event) => setBoardId(event.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="set-board-careers-url">
              Careers URL{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </Label>
            <Input
              id="set-board-careers-url"
              type="url"
              value={careersUrl}
              disabled={saving}
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
                <Button type="button" variant="outline" disabled={saving} />
              }
            >
              Cancel
            </DialogClose>
            <Button type="submit" disabled={saving}>
              {saving ? <Loader2Icon className="animate-spin" /> : null}
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
