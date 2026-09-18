import { useQuery } from "@tanstack/react-query"
import { AlertTriangleIcon } from "lucide-react"
import { Link } from "react-router-dom"
import { buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { SettingsResponse } from "@/pages/settings/types"

export function ApiAccessBanner() {
  const { data } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<SettingsResponse>("/api/settings"),
  })

  const apiAccess = data?.api_access
  if (!apiAccess || (apiAccess.has_api_key && !apiAccess.api_key_reentry_required)) {
    return null
  }

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-warning/30 bg-warning/10 px-6 py-2.5 text-sm md:px-8"
    >
      <div className="flex min-w-0 flex-1 items-start gap-2">
        <AlertTriangleIcon
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-warning"
        />
        <p className="text-foreground">
          {apiAccess.api_key_reentry_required
            ? "LLM API access needs your API key again. Criteria extraction and scoring are unavailable."
            : "LLM API access is not configured. Criteria extraction and scoring are unavailable."}
        </p>
      </div>
      <Link
        to="/settings"
        className={cn(buttonVariants({ variant: "outline", size: "sm" }), "bg-background")}
      >
        Configure API access
      </Link>
    </div>
  )
}
