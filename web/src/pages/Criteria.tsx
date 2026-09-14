import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2Icon, HistoryIcon } from "lucide-react"
import { api, apiPost } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/PageHeader"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { DescribeStep } from "./criteria/DescribeStep"
import { CriteriaForm, type CriteriaPayload } from "./criteria/CriteriaForm"
import { EmployerProposalsStep } from "./criteria/EmployerProposalsStep"
import { HistoryView } from "./criteria/HistoryView"
import type { CriteriaCurrentResponse } from "./criteria/types"

type Step = 1 | 2 | 3

const STEP_LABELS = ["Describe", "Review & correct", "Employers"] as const

/**
 * The single intake surface (GAP-3): Get Started was merged into Criteria.
 * With no criteria, the describe-first flow (D-01) IS the empty state — there
 * is no separate page to navigate to. With criteria, the current version stays
 * editable in the SAME form (D-02) alongside the peekable versioned history
 * (D-04), and "Start over with describe" re-enters the setup flow for a
 * from-zero re-description.
 */
export default function Criteria() {
  const queryClient = useQueryClient()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [savedVersion, setSavedVersion] = useState<number | null>(null)
  // The setup flow (describe -> review/correct -> employers) lives in local
  // state, mirroring the retired Onboarding page — three steps do not justify
  // a wizard dependency.
  const [settingUp, setSettingUp] = useState(false)
  const [step, setStep] = useState<Step>(1)
  const [suggested, setSuggested] = useState<unknown>(null)

  const criteriaQuery = useQuery({
    queryKey: ["criteria"],
    queryFn: () => api<CriteriaCurrentResponse>("/api/criteria"),
  })

  const current = criteriaQuery.data?.current ?? null
  const total = criteriaQuery.data?.total_versions ?? 0
  // No criteria means the setup flow is the page (D-01); an explicit
  // re-describe keeps it open even after a save lands the first version.
  const inSetup = settingUp || !current

  async function saveVersion(payload: CriteriaPayload) {
    const result = await apiPost<{ version: number }>("/api/criteria", payload)
    setSavedVersion(result.version)
    await queryClient.invalidateQueries({ queryKey: ["criteria"] })
  }

  async function handleSetupSave(payload: CriteriaPayload) {
    await saveVersion(payload)
    setSettingUp(true)
    setStep(3)
  }

  function startOver() {
    setSuggested(null)
    setSavedVersion(null)
    setStep(1)
    setSettingUp(true)
  }

  function finishSetup() {
    setSettingUp(false)
    setStep(1)
  }

  if (criteriaQuery.isLoading) {
    return (
      <div className="mx-auto grid max-w-3xl gap-8">
        <span role="status" className="sr-only">
          Loading your criteria…
        </span>
        <div className="grid gap-2">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-4 w-72" />
        </div>
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    )
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-8">
      <PageHeader
        title={inSetup ? "Set up your search" : "Your criteria"}
        description={
          !inSetup && current ? `v${current.version} of ${total}` : undefined
        }
        actions={
          !inSetup ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setHistoryOpen(true)}
            >
              <HistoryIcon />
              View history
            </Button>
          ) : undefined
        }
      >
        {inSetup ? (
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
                          ? "flex size-6 items-center justify-center rounded-full bg-primary/12 text-xs font-medium text-primary"
                          : "flex size-6 items-center justify-center rounded-full border border-border text-xs text-muted-foreground"
                    }
                  >
                    {number}
                  </span>
                  <span
                    className={
                      state === "upcoming"
                        ? "text-muted-foreground"
                        : "font-medium text-foreground"
                    }
                  >
                    {label}
                  </span>
                  {index < STEP_LABELS.length - 1 ? (
                    <span
                      aria-hidden="true"
                      className="h-px w-6 bg-border"
                    />
                  ) : null}
                </li>
              )
            })}
          </ol>
        ) : null}
      </PageHeader>

      {savedVersion !== null ? (
        <p
          role="status"
          className="flex items-start gap-2 rounded-xl border border-success/30 bg-success/10 px-3.5 py-2.5 text-sm text-foreground"
        >
          <CheckCircle2Icon
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-success"
          />
          Saved as version {savedVersion}. You can edit it any time — each save
          adds a new version.
        </p>
      ) : null}

      {inSetup ? (
        <>
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
                <h2 className="font-heading text-xl font-semibold tracking-tight">
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
                defaultValues={suggested ?? current?.payload}
                onSubmit={handleSetupSave}
                submitLabel="Save and continue"
              />
            </section>
          ) : null}

          {step === 3 ? <EmployerProposalsStep onDone={finishSetup} /> : null}
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              This is your current version. Saving adds a new one — nothing is
              overwritten.
            </p>
            <Button type="button" variant="link" onClick={startOver}>
              Start over with describe
            </Button>
          </div>

          <CriteriaForm
            defaultValues={current?.payload}
            onSubmit={saveVersion}
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
        </>
      )}
    </div>
  )
}
