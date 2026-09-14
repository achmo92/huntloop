import { useQuery } from "@tanstack/react-query"
import { CheckIcon, XIcon } from "lucide-react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { JOB_STATUSES, STATUS_LABELS } from "./StatusSelect"
import type { CompanyOut } from "@/pages/criteria/types"

/**
 * D-12 / TRAK-05: the filter bar composes status multi-select, employer, score
 * threshold and a date window into ONE query, and persists the composed set to
 * localStorage so a return visit lands on the same view. "Clear filters" is
 * the only reset — it empties the state and the stored copy together.
 */

export const FILTERS_STORAGE_KEY = "huntloop.listings.filters"

export interface ListingFilters {
  status: string[]
  employer_id: string | null
  score_min: number | null
  posted_after: string | null
  posted_before: string | null
}

export const EMPTY_FILTERS: ListingFilters = {
  status: [],
  employer_id: null,
  score_min: null,
  posted_after: null,
  posted_before: null,
}

export function filtersActive(filters: ListingFilters): boolean {
  return (
    filters.status.length > 0 ||
    filters.employer_id !== null ||
    filters.score_min !== null ||
    filters.posted_after !== null ||
    filters.posted_before !== null
  )
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null
}

export function loadStoredFilters(): ListingFilters {
  if (typeof localStorage === "undefined") return EMPTY_FILTERS
  try {
    const raw = localStorage.getItem(FILTERS_STORAGE_KEY)
    if (!raw) return EMPTY_FILTERS
    const parsed = JSON.parse(raw) as Partial<ListingFilters>
    return {
      status: Array.isArray(parsed.status)
        ? parsed.status.filter((value): value is string => typeof value === "string")
        : [],
      employer_id: asString(parsed.employer_id),
      score_min: typeof parsed.score_min === "number" ? parsed.score_min : null,
      posted_after: asString(parsed.posted_after),
      posted_before: asString(parsed.posted_before),
    }
  } catch {
    return EMPTY_FILTERS
  }
}

export function storeFilters(filters: ListingFilters): void {
  try {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(filters))
  } catch {
    // Private mode or a full quota: persisting is a convenience, never a crash.
  }
}

export function clearStoredFilters(): void {
  try {
    localStorage.removeItem(FILTERS_STORAGE_KEY)
  } catch {
    // Nothing to do — clearing is best-effort.
  }
}

interface FilterBarProps {
  value: ListingFilters
  onChange: (next: ListingFilters) => void
  onClear: () => void
}

export function FilterBar({ value, onChange, onClear }: FilterBarProps) {
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
  })
  const companies = companiesQuery.data ?? []
  const active = filtersActive(value)

  function update(patch: Partial<ListingFilters>) {
    onChange({ ...value, ...patch })
  }

  function toggleStatus(stage: string) {
    const next = value.status.includes(stage)
      ? value.status.filter((candidate) => candidate !== stage)
      : [...value.status, stage]
    update({ status: next })
  }

  return (
    <div className="flex flex-wrap items-end gap-x-5 gap-y-3 rounded-xl border border-border bg-card p-4">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">Status</span>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
          {JOB_STATUSES.map((stage) => {
            const on = value.status.includes(stage)
            return (
              <button
                key={stage}
                type="button"
                aria-pressed={on}
                onClick={() => toggleStatus(stage)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  on
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background text-muted-foreground hover:border-foreground/20 hover:text-foreground"
                )}
              >
                {on ? <CheckIcon aria-hidden="true" className="size-3" /> : null}
                {STATUS_LABELS[stage]}
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="filter-employer"
          className="text-xs font-medium text-muted-foreground"
        >
          Employer
        </label>
        <Select
          value={value.employer_id}
          onValueChange={(next) =>
            update({ employer_id: typeof next === "string" ? next : null })
          }
        >
          <SelectTrigger id="filter-employer" size="sm" className="min-w-40">
            <SelectValue placeholder="All employers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={null}>All employers</SelectItem>
            {companies.map((company) => (
              <SelectItem key={company.id} value={company.id}>
                {company.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="filter-score"
          className="text-xs font-medium text-muted-foreground"
        >
          Minimum score
        </label>
        <Input
          id="filter-score"
          type="number"
          min={0}
          step={0.1}
          className="w-24"
          value={value.score_min ?? ""}
          onChange={(event) => {
            const raw = event.target.value
            update({ score_min: raw === "" ? null : Number(raw) })
          }}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="filter-after"
          className="text-xs font-medium text-muted-foreground"
        >
          Posted after
        </label>
        <Input
          id="filter-after"
          type="date"
          className="w-40"
          value={value.posted_after ?? ""}
          onChange={(event) =>
            update({ posted_after: event.target.value || null })
          }
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="filter-before"
          className="text-xs font-medium text-muted-foreground"
        >
          Posted before
        </label>
        <Input
          id="filter-before"
          type="date"
          className="w-40"
          value={value.posted_before ?? ""}
          onChange={(event) =>
            update({ posted_before: event.target.value || null })
          }
        />
      </div>

      {active ? (
        <button
          type="button"
          onClick={onClear}
          className="ml-auto inline-flex items-center gap-1.5 self-end rounded-lg px-2 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <XIcon aria-hidden="true" className="size-3.5" />
          Clear filters
        </button>
      ) : null}
    </div>
  )
}
