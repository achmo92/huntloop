import { useId, useMemo, useState } from "react"
import {
  useForm,
  useWatch,
  type FieldErrors,
  type Resolver,
} from "react-hook-form"
import { z } from "zod"
import { XIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  DimensionRanker,
  orderToWeights,
  weightsToOrder,
  type DimensionKey,
  type DimensionWeights,
} from "./DimensionRanker"
import {
  COMP_PERIODS,
  SENIORITY_LABELS,
  SENIORITY_LADDER,
  isAlpha2Country,
  isAlpha3Currency,
  isRegion,
  type CompensationPeriod,
  type Seniority,
} from "./iso"

/**
 * The one correction surface for criteria (D-02): the intake setup's review
 * step and the criteria page's edit flow both render this exact component, so
 * the model never edits criteria behind the user's back and re-edits always
 * land in the same form. Validation mirrors the server's `CriteriaPayload` so
 * the user sees inline errors before the request; the server stays the
 * authority.
 */

export interface CriteriaPayload {
  profile_summary: string
  seniority_min: string | null
  seniority_max: string | null
  posting_age_days: number
  locations: {
    eligible_countries: string[]
    eligible_regions: string[]
    preferred_cities: string[]
  }
  compensation_floor: {
    amount: number | null
    currency: string | null
    period: CompensationPeriod
  }
  exclusions: {
    title_keywords: string[]
    employers: string[]
  }
  work_authorization: {
    countries_authorized: string[]
    requires_sponsorship: boolean
  }
  dimension_weights: DimensionWeights
}

export interface CriteriaFormValues {
  profile_summary: string
  seniority_min: string
  seniority_max: string
  posting_age_days: number
  locations: {
    eligible_countries: string[]
    eligible_regions: string[]
    preferred_cities: string[]
  }
  compensation_floor: {
    amount: number | null
    currency: string
    period: CompensationPeriod
  }
  exclusions: {
    title_keywords: string[]
    employers: string[]
  }
  work_authorization: {
    countries_authorized: string[]
    requires_sponsorship: boolean
  }
  dimension_weights: DimensionWeights
}

const DEFAULT_ORDER: DimensionKey[] = [
  "role_fit",
  "seniority_fit",
  "employer_fit",
  "trajectory",
]

export const EMPTY_CRITERIA_VALUES: CriteriaFormValues = {
  profile_summary: "",
  seniority_min: "",
  seniority_max: "",
  posting_age_days: 30,
  locations: {
    eligible_countries: [],
    eligible_regions: [],
    preferred_cities: [],
  },
  compensation_floor: { amount: null, currency: "", period: "annual" },
  exclusions: { title_keywords: [], employers: [] },
  work_authorization: { countries_authorized: [], requires_sponsorship: false },
  dimension_weights: orderToWeights(DEFAULT_ORDER),
}

// ---------------------------------------------------------------------------
// Helpers: server payload / describe draft -> form values, and back
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {}
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : []
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback
}

function asSeniority(value: unknown): string {
  return typeof value === "string" &&
    (SENIORITY_LADDER as readonly string[]).includes(value)
    ? value
    : ""
}

function asPeriod(value: unknown): CompensationPeriod {
  return typeof value === "string" &&
    (COMP_PERIODS as readonly string[]).includes(value)
    ? (value as CompensationPeriod)
    : "annual"
}

/** Normalize a server payload or a describe draft into form-ready values. */
export function toFormValues(raw: unknown): CriteriaFormValues {
  const source = asRecord(raw)
  const locations = asRecord(source.locations)
  const compensation = asRecord(source.compensation_floor)
  const exclusions = asRecord(source.exclusions)
  const authorization = asRecord(source.work_authorization)
  const weights = asRecord(source.dimension_weights)

  const weightValues: DimensionWeights = {
    role_fit: asNullableNumber(weights.role_fit) ?? 0,
    seniority_fit: asNullableNumber(weights.seniority_fit) ?? 0,
    employer_fit: asNullableNumber(weights.employer_fit) ?? 0,
    trajectory: asNullableNumber(weights.trajectory) ?? 0,
  }
  const hasWeights =
    weightValues.role_fit +
      weightValues.seniority_fit +
      weightValues.employer_fit +
      weightValues.trajectory >
    0
  const normalizedWeights = hasWeights
    ? orderToWeights(weightsToOrder(weightValues))
    : EMPTY_CRITERIA_VALUES.dimension_weights

  return {
    profile_summary: asString(source.profile_summary, ""),
    seniority_min: asSeniority(source.seniority_min),
    seniority_max: asSeniority(source.seniority_max),
    posting_age_days:
      asNullableNumber(source.posting_age_days) ??
      EMPTY_CRITERIA_VALUES.posting_age_days,
    locations: {
      eligible_countries: asStringArray(locations.eligible_countries).map((c) =>
        c.toUpperCase()
      ),
      eligible_regions: asStringArray(locations.eligible_regions).map((r) =>
        r.toUpperCase()
      ),
      preferred_cities: asStringArray(locations.preferred_cities),
    },
    compensation_floor: {
      amount: asNullableNumber(compensation.amount),
      currency: asString(compensation.currency, "").toUpperCase(),
      period: asPeriod(compensation.period),
    },
    exclusions: {
      title_keywords: asStringArray(exclusions.title_keywords),
      employers: asStringArray(exclusions.employers),
    },
    work_authorization: {
      countries_authorized: asStringArray(
        authorization.countries_authorized
      ).map((c) => c.toUpperCase()),
      requires_sponsorship: asBoolean(authorization.requires_sponsorship, false),
    },
    dimension_weights: normalizedWeights,
  }
}

/** Form values -> the exact `CriteriaPayload` shape POST /api/criteria takes. */
export function toPayload(values: CriteriaFormValues): CriteriaPayload {
  return {
    profile_summary: values.profile_summary.trim(),
    seniority_min: values.seniority_min || null,
    seniority_max: values.seniority_max || null,
    posting_age_days: values.posting_age_days,
    locations: {
      eligible_countries: values.locations.eligible_countries,
      eligible_regions: values.locations.eligible_regions,
      preferred_cities: values.locations.preferred_cities,
    },
    compensation_floor: {
      amount: values.compensation_floor.amount,
      currency: values.compensation_floor.currency
        ? values.compensation_floor.currency.toUpperCase()
        : null,
      period: values.compensation_floor.period,
    },
    exclusions: {
      title_keywords: values.exclusions.title_keywords,
      employers: values.exclusions.employers,
    },
    work_authorization: {
      countries_authorized: values.work_authorization.countries_authorized,
      requires_sponsorship: values.work_authorization.requires_sponsorship,
    },
    dimension_weights: values.dimension_weights,
  }
}

// ---------------------------------------------------------------------------
// zod schema + resolver (mirrors the server contract for inline errors)
// ---------------------------------------------------------------------------

const isSeniority = (v: string) =>
  v === "" || (SENIORITY_LADDER as readonly string[]).includes(v)

export const criteriaFormSchema = z
  .object({
    profile_summary: z
      .string()
      .trim()
      .min(1, "Describe what you're looking for"),
    seniority_min: z.string().refine(isSeniority, "Choose a level from the list"),
    seniority_max: z.string().refine(isSeniority, "Choose a level from the list"),
    posting_age_days: z
      .number({ message: "Enter how many days back to look" })
      .int("Use a whole number of days")
      .min(1, "Postings must be at least 1 day old to count"),
    locations: z.object({
      eligible_countries: z.array(z.string()),
      eligible_regions: z.array(z.string()),
      preferred_cities: z.array(z.string()),
    }),
    compensation_floor: z.object({
      amount: z
        .number({ message: "Enter a number" })
        .min(0, "The floor cannot be negative")
        .nullable(),
      currency: z
        .string()
        .refine(
          (v) => v === "" || isAlpha3Currency(v),
          "Use a 3-letter code like USD or EUR"
        ),
      period: z.enum(["annual", "monthly", "hourly"]),
    }),
    exclusions: z.object({
      title_keywords: z.array(z.string()),
      employers: z.array(z.string()),
    }),
    work_authorization: z.object({
      countries_authorized: z.array(z.string()),
      requires_sponsorship: z.boolean(),
    }),
    dimension_weights: z
      .object({
        role_fit: z.number().min(0),
        seniority_fit: z.number().min(0),
        employer_fit: z.number().min(0),
        trajectory: z.number().min(0),
      })
      .refine(
        (w) => w.role_fit + w.seniority_fit + w.employer_fit + w.trajectory > 0,
        "At least one dimension must carry weight"
      ),
  })
  .superRefine((values, ctx) => {
    if (values.seniority_min && values.seniority_max) {
      const low = (SENIORITY_LADDER as readonly string[]).indexOf(
        values.seniority_min
      )
      const high = (SENIORITY_LADDER as readonly string[]).indexOf(
        values.seniority_max
      )
      if (low > high) {
        ctx.addIssue({
          code: "custom",
          path: ["seniority_max"],
          message: "The maximum cannot rank below the minimum",
        })
      }
    }
  })

function toFieldErrors(
  issues: readonly { path: readonly PropertyKey[]; message: string }[]
): Record<string, unknown> {
  const root: Record<string, unknown> = {}
  for (const issue of issues) {
    let node = root
    issue.path.forEach((segment, index) => {
      const key = String(segment)
      if (index === issue.path.length - 1) {
        node[key] = { type: "validation", message: issue.message }
      } else {
        if (typeof node[key] !== "object" || node[key] === null) node[key] = {}
        node = node[key] as Record<string, unknown>
      }
    })
  }
  return root
}

const criteriaResolver = async (values: CriteriaFormValues) => {
  const parsed = criteriaFormSchema.safeParse(values)
  if (parsed.success) {
    return { values: parsed.data as CriteriaFormValues, errors: {} }
  }
  return {
    values: {} as CriteriaFormValues,
    errors: toFieldErrors(parsed.error.issues) as unknown as FieldErrors<CriteriaFormValues>,
  }
}

// ---------------------------------------------------------------------------
// Chip editor (locations, exclusions, work authorization)
// ---------------------------------------------------------------------------

type ChipValidation = { value: string } | { error: string }

interface ChipFieldProps {
  values: string[]
  onChange: (next: string[]) => void
  validate: (raw: string) => ChipValidation
  placeholder: string
  ariaLabel: string
}

function ChipField({
  values,
  onChange,
  validate,
  placeholder,
  ariaLabel,
}: ChipFieldProps) {
  const generatedId = useId()
  const [draft, setDraft] = useState("")
  const [error, setError] = useState<string | null>(null)

  function commit() {
    const raw = draft.trim()
    if (!raw) return
    const result = validate(raw)
    if ("error" in result) {
      setError(result.error)
      return
    }
    const exists = values.some(
      (entry) => entry.toLowerCase() === result.value.toLowerCase()
    )
    if (!exists) onChange([...values, result.value])
    setDraft("")
    setError(null)
  }

  function remove(target: string) {
    onChange(values.filter((entry) => entry !== target))
  }

  return (
    <div className="grid gap-2">
      {values.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {values.map((entry) => (
            <li key={entry}>
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-sm">
                {entry}
                <button
                  type="button"
                  onClick={() => remove(entry)}
                  aria-label={`Remove ${entry}`}
                  className="text-muted-foreground transition-colors hover:text-foreground"
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <Input
        id={generatedId}
        aria-label={ariaLabel}
        value={draft}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        onChange={(event) => {
          setDraft(event.target.value)
          if (error) setError(null)
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault()
            commit()
          }
        }}
        onBlur={commit}
      />
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
    </div>
  )
}

function countryValidation(raw: string): ChipValidation {
  const code = raw.toUpperCase()
  return isAlpha2Country(code)
    ? { value: code }
    : { error: "Use a 2-letter country code like US or DE" }
}

function regionValidation(raw: string): ChipValidation {
  const code = raw.toUpperCase()
  return isRegion(code)
    ? { value: code }
    : { error: "Use a region like EMEA, APAC, LATAM, NA, or EU" }
}

function freeText(raw: string): ChipValidation {
  return { value: raw }
}

// ---------------------------------------------------------------------------
// CriteriaForm
// ---------------------------------------------------------------------------

interface CriteriaFormProps {
  defaultValues?: unknown
  onSubmit: (payload: CriteriaPayload) => Promise<void> | void
  submitLabel?: string
}

export function CriteriaForm({
  defaultValues,
  onSubmit,
  submitLabel = "Save criteria",
}: CriteriaFormProps) {
  const initialValues = useMemo(() => toFormValues(defaultValues), [defaultValues])
  const form = useForm<CriteriaFormValues>({
    defaultValues: initialValues,
    resolver: criteriaResolver as unknown as Resolver<CriteriaFormValues>,
  })
  const { control, handleSubmit, setValue, formState } = form
  const weights = useWatch({ control, name: "dimension_weights" })
  const dimensionOrder = weightsToOrder(weights ?? EMPTY_CRITERIA_VALUES.dimension_weights)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const submit = handleSubmit(async (values) => {
    setSubmitError(null)
    try {
      await onSubmit(toPayload(values))
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "Could not save — try again"
      )
    }
  })

  return (
    <Form {...form}>
      <form onSubmit={submit} className="grid max-w-3xl gap-6">
        <FormField
          control={control}
          name="profile_summary"
          render={({ field }) => (
            <FormItem>
              <FormLabel>What you're looking for</FormLabel>
              <FormControl>
                <textarea
                  {...field}
                  rows={3}
                  placeholder="Role, level, places, salary, what to avoid…"
                  className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                />
              </FormControl>
              <FormDescription>
                This is your own summary — edit it if the wording is off.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            Locations
          </legend>
          <FormField
            control={control}
            name="locations.eligible_countries"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Eligible countries</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={countryValidation}
                  ariaLabel="Add an eligible country code"
                  placeholder="Type a country code, press Enter"
                />
                <FormDescription>
                  ISO two-letter codes, e.g. US, DE, GB.
                </FormDescription>
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="locations.eligible_regions"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Eligible regions</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={regionValidation}
                  ariaLabel="Add an eligible region"
                  placeholder="Type a region, press Enter"
                />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="locations.preferred_cities"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Preferred cities</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={freeText}
                  ariaLabel="Add a preferred city"
                  placeholder="Type a city, press Enter"
                />
              </FormItem>
            )}
          />
        </fieldset>

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            Level and timing
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={control}
              name="seniority_min"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Minimum level</FormLabel>
                  <Select
                    value={field.value || null}
                    onValueChange={(v) => field.onChange(typeof v === "string" ? v : "")}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="Any level" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={null}>Any level</SelectItem>
                      {SENIORITY_LADDER.map((level) => (
                        <SelectItem key={level} value={level}>
                          {SENIORITY_LABELS[level as Seniority]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={control}
              name="seniority_max"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Maximum level</FormLabel>
                  <Select
                    value={field.value || null}
                    onValueChange={(v) => field.onChange(typeof v === "string" ? v : "")}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="Any level" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={null}>Any level</SelectItem>
                      {SENIORITY_LADDER.map((level) => (
                        <SelectItem key={level} value={level}>
                          {SENIORITY_LABELS[level as Seniority]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          <FormField
            control={control}
            name="posting_age_days"
            render={({ field }) => (
              <FormItem className="max-w-xs">
                <FormLabel>Postings no older than (days)</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    min={1}
                    value={field.value ?? ""}
                    onChange={(event) =>
                      field.onChange(
                        event.target.value === ""
                          ? null
                          : Number(event.target.value)
                      )
                    }
                    onBlur={field.onBlur}
                    name={field.name}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </fieldset>

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            Compensation floor
          </legend>
          <div className="grid gap-4 sm:grid-cols-3">
            <FormField
              control={control}
              name="compensation_floor.amount"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Amount</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={0}
                      value={field.value ?? ""}
                      onChange={(event) =>
                        field.onChange(
                          event.target.value === ""
                            ? null
                            : Number(event.target.value)
                        )
                      }
                      onBlur={field.onBlur}
                      name={field.name}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={control}
              name="compensation_floor.currency"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Currency</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      maxLength={3}
                      placeholder="USD"
                      className="uppercase"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={control}
              name="compensation_floor.period"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Period</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={(v) =>
                      field.onChange(typeof v === "string" ? v : "annual")
                    }
                  >
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {COMP_PERIODS.map((period) => (
                        <SelectItem key={period} value={period}>
                          {period === "annual"
                            ? "Per year"
                            : period === "monthly"
                              ? "Per month"
                              : "Per hour"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
        </fieldset>

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            Exclusions
          </legend>
          <FormField
            control={control}
            name="exclusions.title_keywords"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Title keywords to avoid</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={freeText}
                  ariaLabel="Add an excluded title keyword"
                  placeholder="Type a keyword, press Enter"
                />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="exclusions.employers"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Employers to exclude</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={freeText}
                  ariaLabel="Add an excluded employer"
                  placeholder="Type an employer, press Enter"
                />
              </FormItem>
            )}
          />
        </fieldset>

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            Work authorization
          </legend>
          <FormField
            control={control}
            name="work_authorization.countries_authorized"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Countries you're authorized to work in</FormLabel>
                <ChipField
                  values={field.value}
                  onChange={field.onChange}
                  validate={countryValidation}
                  ariaLabel="Add an authorized country code"
                  placeholder="Type a country code, press Enter"
                />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="work_authorization.requires_sponsorship"
            render={({ field }) => (
              <FormItem className="flex flex-row items-center gap-2">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={(checked) =>
                      field.onChange(checked === true)
                    }
                  />
                </FormControl>
                <FormLabel>I need sponsorship</FormLabel>
              </FormItem>
            )}
          />
        </fieldset>

        <fieldset className="grid gap-4 rounded-xl border border-border p-4">
          <legend className="px-1 font-heading text-base font-medium">
            What matters most
          </legend>
          <DimensionRanker
            value={dimensionOrder}
            onChange={(next) =>
              setValue("dimension_weights", orderToWeights(next), {
                shouldValidate: true,
              })
            }
          />
        </fieldset>

        {submitError ? (
          <p role="alert" className="text-sm text-destructive">
            {submitError}
          </p>
        ) : null}

        <div className="flex items-center gap-3">
          <Button type="submit" size="lg" disabled={formState.isSubmitting}>
            {formState.isSubmitting ? "Saving…" : submitLabel}
          </Button>
          <span className="text-sm text-muted-foreground">
            Saving creates a new version — nothing is overwritten.
          </span>
        </div>
      </form>
    </Form>
  )
}
