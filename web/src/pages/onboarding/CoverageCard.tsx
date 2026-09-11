import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { AlertTriangleIcon } from "lucide-react"
import { api } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { CompanyOut, CoverageOut } from "./types"

/**
 * D-06: coverage disclosure is a headline number, not a footnote. The honest
 * count of what is actually watchable is the card's title line; employers that
 * could not be resolved for automatic watching are listed right beneath for
 * manual attention. The server computes the numbers — no client-side math.
 */
export function CoverageCard() {
  const coverageQuery = useQuery({
    queryKey: ["coverage"],
    queryFn: () => api<CoverageOut>("/api/companies/coverage"),
  })
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<CompanyOut[]>("/api/companies"),
  })

  if (coverageQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-muted-foreground">
            Checking coverage…
          </CardTitle>
        </CardHeader>
      </Card>
    )
  }

  const coverage = coverageQuery.data ?? {
    added: 0,
    watchable: 0,
    needs_attention: 0,
    resolved: 0,
  }
  const needingAttention = (companiesQuery.data ?? []).filter(
    (company) => !company.resolved
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          We're watching {coverage.watchable} of your {coverage.added} added
          employers
        </CardTitle>
        <CardDescription>
          That's the honest number: the rest we can't watch automatically yet.
        </CardDescription>
      </CardHeader>
      {coverage.needs_attention > 0 ? (
        <CardContent className="grid gap-3">
          <p className="flex items-center gap-2 text-sm text-foreground">
            <AlertTriangleIcon className="size-4 text-destructive" />
            We can't automatically watch {coverage.needs_attention} yet — they
            need attention.
          </p>
          {needingAttention.length > 0 ? (
            <ul className="grid gap-1">
              {needingAttention.map((company) => (
                <li
                  key={company.id}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span>{company.name}</span>
                  {company.resolution_detail ? (
                    <Badge variant="outline" className="font-normal">
                      {company.resolution_detail}
                    </Badge>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          <Link
            to="/employers"
            className="text-sm text-primary underline-offset-4 hover:underline"
          >
            Review employers and retry
          </Link>
        </CardContent>
      ) : null}
    </Card>
  )
}
