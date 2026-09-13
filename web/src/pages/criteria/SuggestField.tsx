import { useId, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { XIcon } from "lucide-react"
import { api } from "@/lib/api"
import { Input } from "@/components/ui/input"
import type { CompanyOut } from "./types"

/**
 * GAP-2: a free-text chip field with typeahead sourced from the existing
 * employer registry. The registry SUGGESTS — it never constrains: any name can
 * be committed, and a name already in the registry is offered so the user does
 * not have to recall it exactly.
 *
 * Suggestions come from the shared `["companies"]` query cache the Employers
 * page and the listings filter bar already populate — one cache key, so opening
 * this form never issues a second request for a list the app already holds.
 */

interface SuggestFieldProps {
  values: string[]
  onChange: (next: string[]) => void
  placeholder: string
  ariaLabel: string
}

export function SuggestField({
  values,
  onChange,
  placeholder,
  ariaLabel,
}: SuggestFieldProps) {
  const generatedId = useId()
  const [draft, setDraft] = useState("")

  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
  })

  const names = useMemo(() => {
    const data = companiesQuery.data
    if (!Array.isArray(data)) return []
    return data
      .map((company) => company?.name)
      .filter(
        (name): name is string => typeof name === "string" && name.length > 0
      )
  }, [companiesQuery.data])

  const query = draft.trim().toLowerCase()
  const matches = query
    ? names.filter(
        (name) =>
          name.toLowerCase().startsWith(query) &&
          !values.some((entry) => entry.toLowerCase() === name.toLowerCase())
      )
    : []

  function commit(raw: string) {
    const value = raw.trim()
    if (!value) return
    const exists = values.some(
      (entry) => entry.toLowerCase() === value.toLowerCase()
    )
    if (!exists) onChange([...values, value])
    setDraft("")
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
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border/70 bg-secondary px-2 py-1 text-sm">
                {entry}
                <button
                  type="button"
                  onClick={() => remove(entry)}
                  aria-label={`Remove ${entry}`}
                  className="rounded-sm text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
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
        autoComplete="off"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault()
            commit(draft)
          }
        }}
        onBlur={() => commit(draft)}
      />
      {matches.length > 0 ? (
        <div
          role="listbox"
          aria-label="Employer suggestions"
          className="max-h-48 overflow-y-auto rounded-xl border border-border bg-popover p-1 shadow-lg"
        >
          {matches.map((name) => (
            <button
              key={name}
              type="button"
              role="option"
              aria-selected={false}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => commit(name)}
              className="w-full rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
