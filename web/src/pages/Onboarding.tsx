import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api, apiPost } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { DescribeStep } from "./onboarding/DescribeStep"
import { CriteriaForm, type CriteriaPayload } from "./onboarding/CriteriaForm"
import { EmployerProposalsStep } from "./onboarding/EmployerProposalsStep"
import type { CriteriaCurrentResponse } from "./onboarding/types"

type Step = 1 | 2 | 3

const STEP_LABELS = ["Describe", "Review & correct", "Employers"] as const

/**
 * The onboarding flow (D-01..D-05): describe once, correct in the form, then
 * review proposed employers as a batch. Steps are local state — no wizard
 * framework for three steps. If criteria already exist, onboarding starts at
 * the form (re-onboarding is re-correcting, not re-describing from zero, D-02).
 */
export default function Onboarding() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>(1)
  const [suggested, setSuggested] = useState<unknown>(null)
  const [savedVersion, setSavedVersion] = useState<number | null>(null)
  const [hydrated, setHydrated] = useState(false)

  const criteriaQuery = useQuery({
    queryKey: ["criteria"],
    queryFn: () => api<CriteriaCurrentResponse>("/api/criteria"),
  })

  const existing = criteriaQuery.data?.current ?? null

  useEffect(() => {
    if (!hydrated && criteriaQuery.isSuccess && existing) {
      setStep(2)
      setHydrated(true)
    }
  }, [hydrated, criteriaQuery.isSuccess, existing])

  async function handleSave(payload: CriteriaPayload) {
    const result = await apiPost<{ version: number }>("/api/criteria", payload)
    setSavedVersion(result.version)
    await queryClient.invalidateQueries({ queryKey: ["criteria"] })
    setStep(3)
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-8">
      <header className="grid gap-3">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">
          Set up your search
        </h1>
        <ol className="flex flex-wrap items-center gap-2 text-sm">
          {STEP_LABELS.map((label, index) => {
            const number = (index + 1) as Step
            const state =
              number === step ? "current" : number < step ? "done" : "upcoming"
            return (
              <li key={label} className="flex items-center gap-2">
                <span
                  className={
                    state === "current"
                      ? "flex size-6 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground"
                      : state === "done"
                        ? "flex size-6 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground"
                        : "flex size-6 items-center justify-center rounded-full border border-border text-xs text-muted-foreground"
                  }
                >
                  {number}
                </span>
                <span
                  className={
                    state === "upcoming"
                      ? "text-muted-foreground"
                      : "text-foreground"
                  }
                >
                  {label}
                </span>
                {index < STEP_LABELS.length - 1 ? (
                  <span aria-hidden="true" className="text-border">
                    /
                  </span>
                ) : null}
              </li>
            )
          })}
        </ol>
      </header>

      {savedVersion !== null ? (
        <p
          role="status"
          className="rounded-lg bg-muted px-3 py-2 text-sm text-foreground"
        >
          Saved as version {savedVersion}. You can edit it any time — each save
          adds a new version.
        </p>
      ) : null}

      {criteriaQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading your criteria…</p>
      ) : null}

      {step === 1 ? (
        <DescribeStep
          onSuggested={(next) => {
            setSuggested(next)
            setStep(2)
          }}
        />
      ) : null}

      {step === 2 ? (
        <section className="grid gap-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-heading text-lg font-medium">
              Review and correct
            </h2>
            <Button
              type="button"
              variant="link"
              onClick={() => {
                setSuggested(null)
                setStep(1)
              }}
            >
              Start over
            </Button>
          </div>
          <CriteriaForm
            defaultValues={suggested ?? existing?.payload}
            onSubmit={handleSave}
            submitLabel="Save and continue"
          />
        </section>
      ) : null}

      {step === 3 ? (
        <EmployerProposalsStep onDone={() => navigate("/criteria")} />
      ) : null}
    </div>
  )
}
