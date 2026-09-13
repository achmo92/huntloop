import { useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api, apiPut } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DiagnosticsPanel } from "./settings/DiagnosticsPanel"
import { ModelSelect } from "./settings/ModelSelect"
import { SettingsSection } from "./settings/SettingsSection"

/**
 * D-15 / UI-04: settings is one page with four independently-saveable sections
 * — API access, models per stage, schedule, spend cap — each with inline
 * validation. Env vars remain boot-time defaults the UI overrides; nothing here
 * requires editing a file or touching a terminal.
 */

interface ApiAccess {
  base_url: string
  has_api_key: boolean
}
interface Models {
  triage: string
  scoring: string
  extraction: string
}
interface Schedule {
  run_at: string
  timezone: string
}
interface SpendCap {
  cap_usd: number | null
}
interface AvailableModels {
  models: string[]
  source: "provider" | "fallback"
}
interface SettingsResponse {
  api_access: ApiAccess
  models: Models
  schedule: Schedule
  spend_cap: SpendCap
}

interface ApiAccessForm {
  base_url: string
  api_key: string
}
interface ModelsForm {
  triage: string
  scoring: string
  extraction: string
}
interface ScheduleForm {
  run_at: string
  timezone: string
}
interface SpendCapForm {
  cap_usd: number | null
}

function FieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string
  children: string
}) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
      {children}
    </label>
  )
}

export default function Settings() {
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<SettingsResponse>("/api/settings"),
  })
  const settings = settingsQuery.data

  // GAP-5: one request feeds all three model pickers; the backend proxies the
  // provider's list with the stored key (never exposed to the browser).
  const modelsQuery = useQuery({
    queryKey: ["available-models"],
    queryFn: () => api<AvailableModels>("/api/settings/models"),
  })
  const availableModels = modelsQuery.data?.models ?? []
  const modelsFromFallback = modelsQuery.data?.source === "fallback"

  const apiAccessDefaults = useMemo<ApiAccessForm>(
    () => ({ base_url: settings?.api_access.base_url ?? "", api_key: "" }),
    [settings?.api_access.base_url]
  )
  const modelsDefaults = useMemo<ModelsForm>(
    () => ({
      triage: settings?.models.triage ?? "",
      scoring: settings?.models.scoring ?? "",
      extraction: settings?.models.extraction ?? "",
    }),
    [
      settings?.models.triage,
      settings?.models.scoring,
      settings?.models.extraction,
    ]
  )
  const scheduleDefaults = useMemo<ScheduleForm>(
    () => ({
      run_at: settings?.schedule.run_at ?? "",
      timezone: settings?.schedule.timezone ?? "",
    }),
    [settings?.schedule.run_at, settings?.schedule.timezone]
  )
  const spendCapDefaults = useMemo<SpendCapForm>(
    () => ({ cap_usd: settings?.spend_cap.cap_usd ?? null }),
    [settings?.spend_cap.cap_usd]
  )

  function mergeSettings<K extends keyof SettingsResponse>(
    key: K,
    value: SettingsResponse[K]
  ) {
    queryClient.setQueryData<SettingsResponse>(["settings"], (prev) =>
      prev ? { ...prev, [key]: value } : prev
    )
  }

  async function saveApiAccess(values: ApiAccessForm) {
    // A blank key field means "leave the stored key alone" — never clear it.
    const payload: { base_url: string; api_key?: string } = {
      base_url: values.base_url,
    }
    const key = values.api_key.trim()
    if (key) payload.api_key = key
    const response = await apiPut<ApiAccess>("/api/settings/api-access", payload)
    mergeSettings("api_access", response)
  }

  async function saveModels(values: ModelsForm) {
    const response = await apiPut<Models>("/api/settings/models", {
      triage: values.triage,
      scoring: values.scoring,
      extraction: values.extraction,
    })
    mergeSettings("models", response)
  }

  async function saveSchedule(values: ScheduleForm) {
    const response = await apiPut<Schedule>("/api/settings/schedule", {
      run_at: values.run_at,
      timezone: values.timezone,
    })
    mergeSettings("schedule", response)
  }

  async function saveSpendCap(values: SpendCapForm) {
    const response = await apiPut<SpendCap>("/api/settings/spend-cap", {
      cap_usd: values.cap_usd,
    })
    mergeSettings("spend_cap", response)
  }

  async function removeSpendCap() {
    const response = await apiPut<SpendCap>("/api/settings/spend-cap", {
      cap_usd: null,
    })
    mergeSettings("spend_cap", response)
  }

  if (settingsQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading settings…</p>
  }

  if (settingsQuery.isError || !settings) {
    return (
      <p role="alert" className="text-sm text-destructive">
        We couldn't load your settings. Refresh to try again.
      </p>
    )
  }

  return (
    <div className="grid gap-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold tracking-tight text-balance">
          Settings
        </h1>
        <p className="mt-1.5 max-w-prose text-sm text-muted-foreground text-pretty">
          Everything the pipeline needs — no file editing required.
        </p>
      </header>

      <SettingsSection
        title="API access"
        description="The one OpenAI-compatible endpoint every model call routes through."
        defaultValues={apiAccessDefaults}
        onSave={saveApiAccess}
        note="Changes apply to the next model call."
      >
        {(form) => (
          <>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="api-base-url">Base URL</FieldLabel>
              <Input
                id="api-base-url"
                placeholder="https://api.openai.com/v1"
                {...form.register("base_url")}
              />
            </div>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="api-key">API key</FieldLabel>
              <Input
                id="api-key"
                type="password"
                autoComplete="off"
                placeholder={
                  settings.api_access.has_api_key
                    ? "•••• stored securely"
                    : "Not set"
                }
                {...form.register("api_key")}
              />
              <p className="text-xs text-muted-foreground">
                Stored encrypted. It is never shown again — leave blank to keep
                the current key.
              </p>
            </div>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title="Models"
        description="Choose the model used at each stage of the pipeline."
        defaultValues={modelsDefaults}
        onSave={saveModels}
        note="Changes apply to the next run."
      >
        {(form) => (
          <>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="model-triage">Triage</FieldLabel>
              <ModelSelect
                id="model-triage"
                label="Triage model"
                value={form.watch("triage")}
                onChange={(value) =>
                  form.setValue("triage", value, { shouldDirty: true })
                }
                models={availableModels}
                fallback={modelsFromFallback}
              />
            </div>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="model-scoring">Scoring</FieldLabel>
              <ModelSelect
                id="model-scoring"
                label="Scoring model"
                value={form.watch("scoring")}
                onChange={(value) =>
                  form.setValue("scoring", value, { shouldDirty: true })
                }
                models={availableModels}
                fallback={modelsFromFallback}
              />
            </div>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="model-extraction">Extraction</FieldLabel>
              <ModelSelect
                id="model-extraction"
                label="Extraction model"
                value={form.watch("extraction")}
                onChange={(value) =>
                  form.setValue("extraction", value, { shouldDirty: true })
                }
                models={availableModels}
                fallback={modelsFromFallback}
              />
            </div>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title="Schedule"
        description="When discovery runs, in the timezone you actually live in."
        defaultValues={scheduleDefaults}
        onSave={saveSchedule}
        note="Takes effect immediately — the scheduler reschedules live."
      >
        {(form) => (
          <>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="schedule-run-at">Run at</FieldLabel>
              <Input
                id="schedule-run-at"
                type="time"
                className="w-fit"
                {...form.register("run_at")}
              />
            </div>
            <div className="grid gap-1.5">
              <FieldLabel htmlFor="schedule-timezone">Timezone</FieldLabel>
              <Input
                id="schedule-timezone"
                placeholder="America/New_York"
                {...form.register("timezone")}
              />
            </div>
          </>
        )}
      </SettingsSection>

      <SettingsSection
        title="Spend cap"
        description="The most a single run may spend before scoring stops."
        defaultValues={spendCapDefaults}
        onSave={saveSpendCap}
        note="Applies to the next run. A run that hits the cap is marked Capped."
      >
        {(form) => (
          <div className="grid gap-1.5">
            <FieldLabel htmlFor="spend-cap">Cap (USD)</FieldLabel>
            <Input
              id="spend-cap"
              type="number"
              min="0"
              step="0.01"
              className="w-40"
              {...form.register("cap_usd", {
                setValueAs: (value: string) =>
                  value === "" ? null : Number(value),
              })}
            />
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={removeSpendCap}
              >
                Remove cap
              </Button>
            </div>
          </div>
        )}
      </SettingsSection>

      <DiagnosticsPanel />
    </div>
  )
}
