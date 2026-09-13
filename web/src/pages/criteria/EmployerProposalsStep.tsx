import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Loader2Icon, SearchIcon } from "lucide-react"
import { api, apiPost, ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { CoverageCard } from "./CoverageCard"
import type { Candidate, CompanyOut } from "./types"

type Phase = "proposing" | "review" | "resolving" | "watching" | "error"

interface EmployerProposalsStepProps {
  onDone?: () => void
}

/**
 * D-05: proposals are reviewed as a batch, then resolved. The model proposes
 * candidates, every one checked by default; NOTHING is added until the single
 * "Add N employers" gate. Resolution runs only on accepted employers, and the
 * per-employer status appears as the background probes finish.
 */
export function EmployerProposalsStep({ onDone }: EmployerProposalsStepProps) {
  const [phase, setPhase] = useState<Phase>("proposing")
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [added, setAdded] = useState<CompanyOut[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    apiPost<{ candidates: Candidate[] }>("/api/onboarding/propose-employers", {})
      .then((result) => {
        if (!active) return
        setCandidates(result.candidates)
        setChecked(
          Object.fromEntries(result.candidates.map((c) => [c.name, true]))
        )
        setPhase("review")
      })
      .catch((err) => {
        if (!active) return
        setError(
          err instanceof ApiError
            ? err.detail
            : "We couldn't suggest employers — try again."
        )
        setPhase("error")
      })
    return () => {
      active = false
    }
  }, [])

  const addedIds = added.map((company) => company.id)

  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
    enabled: addedIds.length > 0,
    refetchInterval: (query) => {
      const data = query.state.data
      // Poll only while an added employer is still unresolved; stop once every
      // one has a terminal status.
      const waiting = data?.some(
        (company) => addedIds.includes(company.id) && !company.resolved
      )
      return waiting ? 2000 : false
    },
  })

  const checkedNames = candidates
    .filter((candidate) => checked[candidate.name])
    .map((candidate) => candidate.name)

  async function acceptBatch() {
    setError(null)
    try {
      const result = await apiPost<{ added: CompanyOut[] }>(
        "/api/companies/batch",
        { names: checkedNames }
      )
      setAdded(result.added)
      setPhase("resolving")
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Adding employers failed — try again."
      )
    }
  }

  async function resolveBatch() {
    setError(null)
    try {
      await apiPost("/api/companies/resolve-batch", { ids: addedIds })
      setPhase("watching")
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't start finding job boards — try again."
      )
    }
  }

  return (
    <section className="grid gap-5">
      <div>
        <h2 className="font-heading text-lg font-semibold tracking-tight">
          Employers to watch
        </h2>
        <p className="mt-1.5 max-w-prose text-sm text-muted-foreground text-pretty">
          Based on your criteria, here's who we'd watch. Uncheck anyone you
          don't want — nothing is added until you say so.
        </p>
      </div>

      {phase === "proposing" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" />
          Suggesting employers…
        </p>
      ) : null}

      {phase === "error" && error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {phase === "review" ? (
        <>
          {candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              We couldn't find any employers for these criteria. You can add
              them manually later.
            </p>
          ) : (
            <ul className="grid gap-2">
              {candidates.map((candidate) => (
                <li
                  key={candidate.name}
                  className="flex items-start gap-3 rounded-xl border border-border bg-card p-3 shadow-xs transition-colors hover:border-foreground/20"
                >
                  <Checkbox
                    className="mt-0.5"
                    aria-label={candidate.name}
                    checked={checked[candidate.name] ?? false}
                    onCheckedChange={(value) =>
                      setChecked((prev) => ({
                        ...prev,
                        [candidate.name]: value === true,
                      }))
                    }
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{candidate.name}</p>
                    {candidate.reason ? (
                      <p className="text-sm text-muted-foreground">
                        {candidate.reason}
                      </p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-3">
            <Button
              type="button"
              size="lg"
              disabled={checkedNames.length === 0}
              onClick={acceptBatch}
            >
              Add {checkedNames.length} employer
              {checkedNames.length === 1 ? "" : "s"}
            </Button>
            <span className="text-sm text-muted-foreground">
              Only the checked employers are added.
            </span>
          </div>
        </>
      ) : null}

      {phase === "resolving" || phase === "watching" ? (
        <div className="grid gap-4">
          <ul className="grid gap-2">
            {added.map((company) => {
              const live = companiesQuery.data?.find(
                (candidate) => candidate.id === company.id
              )
              const status = live ?? company
              return (
                <li
                  key={company.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card p-3 shadow-xs"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{company.name}</p>
                    {status.resolution_detail ? (
                      <p className="text-sm text-muted-foreground">
                        {status.resolution_detail}
                      </p>
                    ) : null}
                  </div>
                  <Badge
                    variant={status.resolved ? "success" : "warning"}
                    className="font-normal"
                  >
                    {status.resolved ? "Watching" : "Finding job board…"}
                  </Badge>
                </li>
              )
            })}
          </ul>

          {phase === "resolving" ? (
            <div className="flex items-center gap-3">
              <Button type="button" size="lg" onClick={resolveBatch}>
                <SearchIcon />
                Find their job boards
              </Button>
              <span className="text-sm text-muted-foreground">
                Resolution runs in the background.
              </span>
            </div>
          ) : null}

          {phase === "watching" ? <CoverageCard /> : null}

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          {onDone ? (
            <div>
              <Button type="button" variant="outline" onClick={onDone}>
                Done for now
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
