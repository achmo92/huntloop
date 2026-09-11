import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/EmptyState"
import { CoverageCard } from "@/pages/onboarding/CoverageCard"
import type { CompanyOut } from "@/pages/onboarding/types"
import { RegistryTable } from "./employers/RegistryTable"

/**
 * The employers surface: the honest coverage headline (D-06) stays visible
 * here after onboarding ends, above the one-list registry (D-07/D-08). Zero
 * employers is an empty state that points back at onboarding (D-18/UI-07),
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
      <header>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">
          Employers
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The employers we watch — and how much of your target we can honestly
          cover automatically.
        </p>
      </header>

      {companiesQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading employers…</p>
      ) : isEmpty ? (
        <EmptyState
          title="No employers yet"
          description="Add employers during onboarding and we'll watch their job boards for you."
          actionLabel="Start onboarding"
          onAction={() => navigate("/onboarding")}
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
