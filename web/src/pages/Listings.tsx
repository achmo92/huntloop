import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { EmptyState } from "@/components/EmptyState"
import { PageHeader } from "@/components/PageHeader"
import { SkeletonTable } from "@/components/ui/skeleton"
import {
  EMPTY_FILTERS,
  FilterBar,
  clearStoredFilters,
  filtersActive,
  loadStoredFilters,
  storeFilters,
  type ListingFilters,
} from "./listings/FilterBar"
import {
  JOBS_PAGE_SIZE,
  ListingsTable,
  useJobsQuery,
  type SortState,
} from "./listings/ListingsTable"
import { DetailDrawer } from "./listings/DetailDrawer"

/**
 * The listings workspace — the screen the target user lives in (D-09..D-13).
 * The composed filter + sort + page state is the single source for one server
 * query; the page gates the empty states (D-18/UI-07) on that same query so an
 * empty result is never a blank table.
 */
export default function Listings() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState<ListingFilters>(() =>
    loadStoredFilters()
  )
  const [sort, setSort] = useState<SortState>({
    sort: "score_overall",
    order: "desc",
  })
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState<string | null>(null)

  const params = useMemo(
    () => ({ filters, sort, page, pageSize: JOBS_PAGE_SIZE }),
    [filters, sort, page]
  )
  const jobsQuery = useJobsQuery(params)
  const total = jobsQuery.data?.total ?? 0
  const isEmpty = jobsQuery.isSuccess && total === 0
  const hasFilters = filtersActive(filters)

  function updateFilters(next: ListingFilters) {
    setFilters(next)
    storeFilters(next)
    setPage(1)
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
    clearStoredFilters()
    setPage(1)
  }

  return (
    <div className="grid gap-8">
      <PageHeader
        title="Listings"
        description="Every role we scored for you — filter, sort, and move each one through your pipeline."
      />

      <FilterBar
        value={filters}
        onChange={updateFilters}
        onClear={clearFilters}
      />

      {jobsQuery.isLoading ? (
        <div className="grid gap-3">
          <span role="status" className="sr-only">
            Loading listings…
          </span>
          <SkeletonTable />
        </div>
      ) : isEmpty ? (
        hasFilters ? (
          <EmptyState
            title="No listings match these filters"
            description="Try widening the score, employer, or date range — or clear the filters to see everything."
            actionLabel="Clear filters"
            onAction={clearFilters}
          />
        ) : (
          <EmptyState
            title="No listings yet"
            description="Run discovery from the dashboard to find your first listings"
            actionLabel="Run discovery"
            onAction={() => navigate("/")}
          />
        )
      ) : (
        <ListingsTable
          filters={filters}
          sort={sort}
          page={page}
          onSortChange={setSort}
          onPageChange={setPage}
          onOpen={setOpenId}
          activeId={openId}
        />
      )}

      <DetailDrawer
        jobId={openId}
        open={openId !== null}
        onOpenChange={(open) => {
          if (!open) setOpenId(null)
        }}
      />
    </div>
  )
}
