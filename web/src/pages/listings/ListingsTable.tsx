import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronsUpDownIcon,
} from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { StatusSelect } from "./StatusSelect"
import type { ListingFilters } from "./FilterBar"

/**
 * D-09 / TRAK-05: one dense row per listing. The client table owns header-click
 * UX and row rendering only — sorting and pagination are SERVER-side (the API
 * does the work; TRAK-06 writes every scored listing, so the volume can be
 * large). Clicking a row opens the detail drawer; the row's status select is
 * the one-click transition surface and does not trigger the row click.
 */

export interface JobRow {
  id: string
  company_id: string
  company_name: string
  title: string
  location: string | null
  is_remote: boolean | null
  comp_min: number | null
  comp_max: number | null
  comp_currency: string | null
  comp_period: string | null
  score_overall: number | null
  score_flags: Record<string, unknown> | null
  status: string
  posted_at: string | null
  first_seen_at: string
  url: string
}

export interface JobListResponse {
  items: JobRow[]
  total: number
  page: number
  page_size: number
}

export type SortKey = "score_overall" | "posted_at" | "first_seen_at" | "title"

export interface SortState {
  sort: SortKey
  order: "asc" | "desc"
}

export interface JobsQueryParams {
  filters: ListingFilters
  sort: SortState
  page: number
  pageSize: number
}

export const JOBS_PAGE_SIZE = 50

type SortableMeta = { sortKey?: SortKey }

export function buildJobsPath(params: JobsQueryParams): string {
  const { filters, sort, page, pageSize } = params
  const search = new URLSearchParams()
  if (filters.status.length > 0) search.set("status", filters.status.join(","))
  if (filters.employer_id) search.set("employer_id", filters.employer_id)
  if (filters.score_min !== null) {
    search.set("score_min", String(filters.score_min))
  }
  if (filters.posted_after) search.set("posted_after", filters.posted_after)
  if (filters.posted_before) search.set("posted_before", filters.posted_before)
  search.set("page", String(page))
  search.set("page_size", String(pageSize))
  return `/api/jobs?${search.toString()}&sort=${sort.sort}&order=${sort.order}`
}

/**
 * The single jobs query definition. Both the page (empty-state gate) and the
 * table call this with the same composed state; react-query shares the cache
 * key, so there is one request and no duplicated state (same pattern as
 * RegistryTable/Employers in 04-08).
 */
export function useJobsQuery(params: JobsQueryParams) {
  return useQuery({
    queryKey: ["jobs", params],
    queryFn: () => api<JobListResponse>(buildJobsPath(params)),
  })
}

export function formatComp(job: JobRow): string {
  if (job.comp_min === null && job.comp_max === null) return "—"
  const formatter = new Intl.NumberFormat()
  const at = (value: number | null) =>
    value === null ? "?" : formatter.format(value)
  const range = `${at(job.comp_min)}–${at(job.comp_max)}`
  const suffix = [job.comp_currency, job.comp_period].filter(Boolean).join(" / ")
  return suffix ? `${range} ${suffix}` : range
}

export function formatLocation(job: JobRow): string {
  if (job.location && job.is_remote) return `${job.location} · Remote`
  if (job.location) return job.location
  return job.is_remote ? "Remote" : "—"
}

function formatPosted(value: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString()
}

interface ListingsTableProps {
  filters: ListingFilters
  sort: SortState
  page: number
  pageSize?: number
  onSortChange: (next: SortState) => void
  onPageChange: (page: number) => void
  onOpen: (id: string) => void
  activeId?: string | null
}

export function ListingsTable({
  filters,
  sort,
  page,
  pageSize = JOBS_PAGE_SIZE,
  onSortChange,
  onPageChange,
  onOpen,
  activeId = null,
}: ListingsTableProps) {
  const jobsQuery = useJobsQuery({ filters, sort, page, pageSize })
  const items = jobsQuery.data?.items ?? []
  const total = jobsQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  function toggleSort(key: SortKey) {
    if (key === sort.sort) {
      onSortChange({ sort: key, order: sort.order === "asc" ? "desc" : "asc" })
    } else {
      onSortChange({ sort: key, order: "desc" })
    }
  }

  const columns = useMemo<ColumnDef<JobRow>[]>(
    () => [
      {
        id: "title",
        accessorKey: "title",
        header: "Title",
        meta: { sortKey: "title" } satisfies SortableMeta,
        cell: ({ row }) => (
          <div className="flex min-w-52 flex-col gap-0.5">
            <span className="font-medium text-foreground">
              {row.original.title}
            </span>
            <span className="text-xs text-muted-foreground">
              {row.original.company_name}
            </span>
          </div>
        ),
      },
      {
        id: "location",
        accessorFn: (row) => row.location,
        header: "Location",
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatLocation(row.original)}
          </span>
        ),
      },
      {
        id: "comp",
        header: "Comp",
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatComp(row.original)}
          </span>
        ),
      },
      {
        id: "score",
        accessorKey: "score_overall",
        header: "Score",
        meta: { sortKey: "score_overall" } satisfies SortableMeta,
        // Blank — never 0 — when a listing has not been scored (TRAK-06).
        cell: ({ row }) => (
          <span
            data-testid="score-value"
            className="block text-right font-heading text-sm font-semibold tabular-nums"
          >
            {row.original.score_overall === null
              ? ""
              : row.original.score_overall.toFixed(2)}
          </span>
        ),
      },
      {
        id: "posted",
        accessorKey: "posted_at",
        header: "Posted",
        meta: { sortKey: "posted_at" } satisfies SortableMeta,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatPosted(row.original.posted_at)}
          </span>
        ),
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => (
          <div onClick={(event) => event.stopPropagation()}>
            <StatusSelect
              jobId={row.original.id}
              status={row.original.status}
            />
          </div>
        ),
      },
    ],
    []
  )

  const table = useReactTable({
    data: items,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
  })

  if (jobsQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading listings…</p>
  }

  if (jobsQuery.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        We couldn't load your listings. Refresh to try again.
      </p>
    )
  }

  return (
    <div className="grid gap-3">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sortKey = (
                  header.column.columnDef.meta as SortableMeta | undefined
                )?.sortKey
                const ariaSort = sortKey
                  ? sort.sort === sortKey
                    ? sort.order === "asc"
                      ? ("ascending" as const)
                      : ("descending" as const)
                    : ("none" as const)
                  : undefined
                return (
                  <TableHead key={header.id} aria-sort={ariaSort}>
                    {header.isPlaceholder ? null : sortKey ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(sortKey)}
                        className="-mx-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                        {sort.sort === sortKey ? (
                          sort.order === "asc" ? (
                            <ArrowUpIcon className="size-3.5" />
                          ) : (
                            <ArrowDownIcon className="size-3.5" />
                          )
                        ) : (
                          <ChevronsUpDownIcon className="size-3.5 text-muted-foreground/50" />
                        )}
                      </button>
                    ) : (
                      flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )
                    )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              tabIndex={0}
              aria-label={`Open ${row.original.title}`}
              onClick={() => onOpen(row.original.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter") onOpen(row.original.id)
              }}
              className={
                activeId === row.original.id
                  ? "cursor-pointer bg-accent/60"
                  : "cursor-pointer"
              }
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="tabular-nums">
          {total} {total === 1 ? "listing" : "listings"}
        </span>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            Previous
          </Button>
          <span className="tabular-nums">
            Page {page} of {pageCount}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page >= pageCount}
            onClick={() => onPageChange(page + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  )
}
