import { useState } from "react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { ChevronUpIcon, ChevronDownIcon, GripVerticalIcon } from "lucide-react"

/**
 * D-03: dimension ranking is drag-to-order. The user drags the four scoring
 * dimensions into priority order; position maps directly to weight
 * (top -> 4, bottom -> 1). Native HTML5 drag-and-drop — four items do not
 * justify a dependency — with an up/down button fallback so keyboard users
 * get the exact same one-action ordering.
 */

export const DIMENSION_KEYS = [
  "role_fit",
  "seniority_fit",
  "employer_fit",
  "trajectory",
] as const

export type DimensionKey = (typeof DIMENSION_KEYS)[number]

export const DIMENSION_LABELS: Record<DimensionKey, string> = {
  role_fit: "Role fit",
  seniority_fit: "Seniority fit",
  employer_fit: "Employer fit",
  trajectory: "Trajectory",
}

export interface DimensionWeights {
  role_fit: number
  seniority_fit: number
  employer_fit: number
  trajectory: number
}

/** Position -> weight, top to bottom. The mapping D-03 promises the user. */
export const POSITION_WEIGHTS = [4, 3, 2, 1] as const

export function orderToWeights(order: readonly DimensionKey[]): DimensionWeights {
  const weights: DimensionWeights = {
    role_fit: 0,
    seniority_fit: 0,
    employer_fit: 0,
    trajectory: 0,
  }
  order.forEach((key, index) => {
    weights[key] = POSITION_WEIGHTS[index] ?? 1
  })
  return weights
}

export function weightsToOrder(weights: DimensionWeights): DimensionKey[] {
  return [...DIMENSION_KEYS].sort((a, b) => weights[b] - weights[a])
}

function move(order: DimensionKey[], from: number, to: number): DimensionKey[] {
  if (from === to || from < 0 || to < 0 || from >= order.length || to >= order.length) {
    return order
  }
  const next = [...order]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

interface DimensionRankerProps {
  value: DimensionKey[]
  onChange: (order: DimensionKey[]) => void
}

export function DimensionRanker({ value, onChange }: DimensionRankerProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)

  return (
    <div className="grid gap-3">
      <div>
        <p className="text-sm font-medium">Priority order</p>
        <p className="text-sm text-muted-foreground">
          Drag the dimensions into the order that matters to you. Position sets
          the weight — most important first.
        </p>
      </div>

      <ol className="grid gap-2" data-testid="dimension-ranker">
        {value.map((key, index) => (
          <li
            key={key}
            draggable
            data-dimension={key}
            onDragStart={() => setDragIndex(index)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              if (dragIndex !== null) onChange(move(value, dragIndex, index))
              setDragIndex(null)
            }}
            onDragEnd={() => setDragIndex(null)}
            className={cn(
              "flex cursor-grab items-center gap-2 rounded-xl border border-border bg-card px-3 py-2.5 shadow-xs transition-colors hover:border-foreground/20",
              dragIndex === index && "opacity-60 ring-2 ring-ring/40"
            )}
          >
            <GripVerticalIcon
              aria-hidden="true"
              className="size-4 text-muted-foreground"
            />
            <span className="flex-1 text-sm">{DIMENSION_LABELS[key]}</span>
            <span
              className="rounded-md bg-muted px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground"
              data-weight={POSITION_WEIGHTS[index]}
            >
              {POSITION_WEIGHTS[index]}
            </span>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Move ${DIMENSION_LABELS[key]} up`}
                disabled={index === 0}
                onClick={() => onChange(move(value, index, index - 1))}
              >
                <ChevronUpIcon />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Move ${DIMENSION_LABELS[key]} down`}
                disabled={index === value.length - 1}
                onClick={() => onChange(move(value, index, index + 1))}
              >
                <ChevronDownIcon />
              </Button>
            </div>
          </li>
        ))}
      </ol>

      <p className="text-sm text-muted-foreground" data-testid="weights-preview">
        What this means:{" "}
        {value
          .map((key, index) => `${DIMENSION_LABELS[key]} weight ${POSITION_WEIGHTS[index]}`)
          .join(" · ")}
      </p>
    </div>
  )
}
