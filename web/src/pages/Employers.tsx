import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { PageHeader } from "@/components/PageHeader"
import { SkeletonTable } from "@/components/ui/skeleton"
import { CoverageCard } from "@/pages/criteria/CoverageCard"
import type { CompanyOut } from "@/pages/criteria/types"
import { AddEmployerDialog } from "./employers/AddEmployerDialog"
import { RegistryTable } from "./employers/RegistryTable"

/**
 * The employers surface: the honest coverage headline (D-06) stays visible
 * here after setup ends, above the one-list registry (D-07/D-08). Zero
 * employers is an empty state that points back at criteria (D-18/UI-07),
 * never a blank table.
 */
export default function Employers() {
  const navigate = useNavigate()
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
  })

  const companies = companiesQuery.data ?? []
  const isEmpty = companiesQuery.isSuccess && companies.length === 0

  return (
    <div className="grid gap-8">
      <PageHeader
        title="Employers"
        description="The employers we watch — and how much of your target we can honestly cover automatically."
        actions={<AddEmployerDialog />}
      />

      {companiesQuery.isLoading ? (
        <div className="grid gap-3">
          <span role="status" className="sr-only">
            Loading employers…
          </span>
          <SkeletonTable />
        </div>
      ) : isEmpty ? (
        <EmptyState
          title="No employers yet"
          description="Add employers from your criteria and we'll watch their job boards for you."
          actionLabel="Describe your search"
          onAction={() => navigate("/criteria")}
        />
      ) : (
        <>
          <CoverageCard />
          <RegistryTable />
        </>
      )}
    </div>
  )
}
