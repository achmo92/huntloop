import { useState } from "react"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/**
 * GAP-5 / UI-04: picking a model is a dropdown over the provider's own list
 * (fetched through the backend, so the stored key never reaches the browser),
 * not an id typed from memory. Two robustness rules:
 *
 * - A saved model the provider omitted still appears as a selected item — the
 *   control always reflects what is actually saved.
 * - "Custom model…" reveals a free-text input — the escape hatch for custom
 *   endpoints whose ids the list may not cover.
 */

const CUSTOM_VALUE = "__custom__"
const CUSTOM_LABEL = "Custom model…"
const FALLBACK_HINT =
  "Couldn't reach your provider — showing common defaults; type your own if yours is missing."

interface ModelSelectProps {
  id: string
  value: string
  onChange: (value: string) => void
  models: string[]
  fallback?: boolean
  label?: string
}

export function ModelSelect({
  id,
  value,
  onChange,
  models,
  fallback = false,
  label = "Model",
}: ModelSelectProps) {
  const [customMode, setCustomMode] = useState(false)

  // Keep the saved value selectable even when the fetched list omits it.
  const options = value && !models.includes(value) ? [value, ...models] : models

  const items = Object.fromEntries([
    ...options.map((option) => [option, option] as const),
    [CUSTOM_VALUE, CUSTOM_LABEL] as const,
  ])

  if (customMode) {
    return (
      <div className="grid gap-1.5">
        <Input
          id={id}
          aria-label={label}
          placeholder="your-model-id"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          className="w-fit text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setCustomMode(false)}
        >
          Choose from the list instead
        </button>
      </div>
    )
  }

  return (
    <div className="grid gap-1.5">
      <Select
        value={value}
        items={items}
        onValueChange={(next) => {
          const picked = typeof next === "string" ? next : ""
          if (picked === CUSTOM_VALUE) {
            setCustomMode(true)
            return
          }
          if (picked) onChange(picked)
        }}
      >
        <SelectTrigger id={id} aria-label={label} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
          <SelectItem value={CUSTOM_VALUE}>{CUSTOM_LABEL}</SelectItem>
        </SelectContent>
      </Select>
      {fallback ? (
        <p className="text-xs text-muted-foreground">{FALLBACK_HINT}</p>
      ) : null}
    </div>
  )
}
