import { useState } from "react"
import {
  CheckCircle2Icon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react"
import { apiPost, ApiError } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

/**
 * D-16 / UI-05: one click runs all three checks at once; each card resolves the
 * moment its own request does (progressive results without server push). A
 * failure is honest and actionable: the API's detail plus its remedy sentence,
 * never a bare error code.
 */

interface DiagResult {
  status: "pass" | "fail"
  detail: string
  remedy: string | null
}

const CHECKS = [
  {
    key: "llm",
    title: "Model access",
    path: "/api/diagnostics/llm",
    description: "Reaches the configured model endpoint.",
  },
  {
    key: "database",
    title: "Database",
    path: "/api/diagnostics/database",
    description: "Main and credentials stores are reachable.",
  },
  {
    key: "employer-fetch",
    title: "Employer fetch",
    path: "/api/diagnostics/employer-fetch",
    description: "Fetches one live employer board.",
  },
] as const

type CheckKey = (typeof CHECKS)[number]["key"]

interface CheckState {
  phase: "idle" | "running" | "done"
  result?: DiagResult
  error?: string
}

function idleChecks(): Record<CheckKey, CheckState> {
  return {
    llm: { phase: "idle" },
    database: { phase: "idle" },
    "employer-fetch": { phase: "idle" },
  }
}

export function DiagnosticsPanel() {
  const [checks, setChecks] = useState<Record<CheckKey, CheckState>>(idleChecks)
  const [running, setRunning] = useState(false)

  function runAll() {
    setRunning(true)
    setChecks({
      llm: { phase: "running" },
      database: { phase: "running" },
      "employer-fetch": { phase: "running" },
    })

    const requests = CHECKS.map((check) =>
      apiPost<DiagResult>(check.path, {})
        .then((result) => {
          setChecks((prev) => ({
            ...prev,
            [check.key]: { phase: "done", result },
          }))
        })
        .catch((error: unknown) => {
          setChecks((prev) => ({
            ...prev,
            [check.key]: {
              phase: "done",
              error:
                error instanceof ApiError
                  ? error.detail
                  : "The check couldn't run. Try again.",
            },
          }))
        })
    )

    // allSettled keeps the button lifecycle honest; each card above already
    // updated on its own resolution.
    void Promise.allSettled(requests).then(() => setRunning(false))
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Diagnostics</CardTitle>
          <CardDescription>
            Run all three checks. Results appear as each one finishes.
          </CardDescription>
        </div>
        <Button type="button" onClick={runAll} disabled={running}>
          {running ? "Running…" : "Run diagnostics"}
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3">
        {CHECKS.map((check) => {
          const state = checks[check.key]
          return (
            <div
              key={check.key}
              data-testid={`diag-${check.key}`}
              data-phase={state.phase}
              className="rounded-xl border border-border/70 bg-muted/25 px-3.5 py-3 transition-colors data-[phase=running]:border-ring/40"
            >
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="text-sm font-medium">{check.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {check.description}
                  </div>
                </div>
                {state.phase === "running" ? (
                  <span
                    data-testid="diag-checking"
                    className="flex items-center gap-1.5 text-xs text-muted-foreground"
                  >
                    <Loader2Icon className="size-3.5 animate-spin" />
                    Checking…
                  </span>
                ) : state.phase === "done" ? (
                  state.result?.status === "fail" || state.error ? (
                    <Badge variant="destructive" className="font-normal">
                      <XCircleIcon /> Fail
                    </Badge>
                  ) : (
                    <Badge variant="success" className="font-normal">
                      <CheckCircle2Icon /> Pass
                    </Badge>
                  )
                ) : (
                  <span className="text-xs text-muted-foreground">Not run</span>
                )}
              </div>

              {state.phase === "done" && state.result ? (
                <p className="mt-2 text-sm text-pretty">{state.result.detail}</p>
              ) : null}
              {state.phase === "done" && state.error ? (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  {state.error}
                </p>
              ) : null}
              {state.phase === "done" &&
              state.result?.status === "fail" &&
              state.result.remedy ? (
                <p className="mt-1.5 text-sm font-medium text-warning text-pretty">
                  What to do: {state.result.remedy}
                </p>
              ) : null}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
