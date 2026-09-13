import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { DiffIcon } from "lucide-react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import type { CriteriaVersionWire } from "./types"

/**
 * D-04: current + peekable history. Every version is listed with its source
 * and date; choosing any two renders a field-level diff computed client-side
 * (no diff library). Restoring is deliberately NOT offered — the way to bring
 * back an older state is to edit the form, which creates a new version
 * (INTK-07's shape).
 */

export interface DiffEntry {
  path: string
  before: unknown
  after: unknown
  kind: "added" | "removed" | "changed"
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function diffObjects(a: unknown, b: unknown, path = ""): DiffEntry[] {
  if (!isPlainObject(a) || !isPlainObject(b)) {
    if (JSON.stringify(a) !== JSON.stringify(b)) {
      return [{ path, before: a, after: b, kind: "changed" }]
    }
    return []
  }

  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  const entries: DiffEntry[] = []
  for (const key of keys) {
    const childPath = path ? `${path}.${key}` : key
    const before = a[key]
    const after = b[key]
    if (!(key in a)) {
      entries.push({ path: childPath, before: undefined, after, kind: "added" })
    } else if (!(key in b)) {
      entries.push({ path: childPath, before, after: undefined, kind: "removed" })
    } else if (isPlainObject(before) && isPlainObject(after)) {
      entries.push(...diffObjects(before, after, childPath))
    } else if (JSON.stringify(before) !== JSON.stringify(after)) {
      entries.push({ path: childPath, before, after, kind: "changed" })
    }
  }
  return entries
}

function formatValue(value: unknown): string {
  if (value === undefined) return "—"
  if (value === null) return "none"
  if (typeof value === "string") return value
  return JSON.stringify(value)
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function HistoryView() {
  const query = useQuery({
    queryKey: ["criteria", "versions"],
    queryFn: () => api<CriteriaVersionWire[]>("/api/criteria/versions"),
  })
  const versions = useMemo(() => query.data ?? [], [query.data])
  const [selected, setSelected] = useState<number[]>([])

  useEffect(() => {
    if (versions.length === 0 || selected.length > 0) return
    const latest = versions.slice(-2).map((version) => version.version)
    setSelected(latest)
  }, [versions, selected.length])

  function toggle(version: number) {
    setSelected((prev) => {
      if (prev.includes(version)) return prev.filter((v) => v !== version)
      if (prev.length >= 2) return [prev[1], version]
      return [...prev, version]
    })
  }

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading history…</p>
  }

  if (versions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No saved versions yet.
      </p>
    )
  }

  const [leftVersion, rightVersion] = [...selected].sort((a, b) => a - b)
  const left = versions.find((version) => version.version === leftVersion)
  const right = versions.find((version) => version.version === rightVersion)
  const entries =
    left && right && left.version !== right.version
      ? diffObjects(left.payload, right.payload)
      : []

  return (
    <div className="grid gap-5">
      <ol className="grid gap-2">
        {[...versions].reverse().map((version) => (
          <li
            key={version.version}
            className={cn(
              "flex items-center gap-3 rounded-xl border p-3 shadow-xs transition-colors",
              selected.includes(version.version)
                ? "border-primary/40 bg-primary/5"
                : "border-border bg-card hover:border-foreground/20"
            )}
          >
            <Checkbox
              aria-label={`Version ${version.version}`}
              checked={selected.includes(version.version)}
              onCheckedChange={() => toggle(version.version)}
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium tabular-nums">
                v{version.version}
              </p>
              <p className="text-sm text-muted-foreground">
                {formatDate(version.created_at)}
                {version.source ? ` · ${version.source}` : ""}
              </p>
            </div>
          </li>
        ))}
      </ol>

      <div className="grid gap-2">
        <p className="text-sm font-medium">
          {left && right && left.version !== right.version
            ? `Changes from v${left.version} to v${right.version}`
            : "Select two versions to compare"}
        </p>
        {left && right && left.version !== right.version ? (
          entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No differences between these versions.
            </p>
          ) : (
            <ul className="grid gap-1.5">
              {entries.map((entry) => (
                <li
                  key={`${entry.kind}:${entry.path}`}
                  className="grid gap-1 rounded-lg border border-border/60 bg-muted/40 p-2.5 text-sm"
                >
                  <span className="flex items-center gap-1.5 font-medium">
                    <DiffIcon
                      aria-hidden="true"
                      className="size-3.5 text-muted-foreground"
                    />
                    {entry.path}
                  </span>
                  <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
                    <span className="line-through">
                      {formatValue(entry.before)}
                    </span>
                    <span aria-hidden="true">→</span>
                    <span className="text-foreground">
                      {formatValue(entry.after)}
                    </span>
                    <Badge
                      variant={
                        entry.kind === "added"
                          ? "success"
                          : entry.kind === "removed"
                            ? "destructive"
                            : "warning"
                      }
                      className="font-normal"
                    >
                      {entry.kind}
                    </Badge>
                  </span>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>

      <p className="text-sm text-muted-foreground">
        To restore an older version, edit the form and save — that creates a new
        version rather than erasing history.
      </p>
    </div>
  )
}
