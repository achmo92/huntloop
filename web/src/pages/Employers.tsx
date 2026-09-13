import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
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
    <div className="grid gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight text-balance">
            Employers
          </h1>
          <p className="mt-1.5 max-w-prose text-sm text-muted-foreground text-pretty">
            The employers we watch — and how much of your target we can honestly
            cover automatically.
          </p>
        </div>
        <AddEmployerDialog />
      </header>

      {companiesQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading employers…</p>
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
