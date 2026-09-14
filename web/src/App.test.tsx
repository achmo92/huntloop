import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { routes } from "./App"

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0
    detail = ""
  },
  api: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiPut: vi.fn(),
}))

const mockedApi = vi.mocked(api)

const EMPTY_DASHBOARD = {
  last_run: null,
  funnel: null,
  next_scheduled_run: "2026-09-12T08:00:00Z",
  listings_by_status: {
    new: 0,
    shortlisted: 0,
    applied: 0,
    interviewing: 0,
    offer: 0,
    rejected: 0,
    withdrawn: 0,
  },
  total_listings: 0,
}

const SETTINGS = {
  api_access: { base_url: "https://api.openai.com/v1", has_api_key: false },
  models: { triage: "t", scoring: "s", extraction: "e" },
  schedule: { run_at: "08:00", timezone: "UTC" },
  spend_cap: { cap_usd: null },
}

function renderAt(path: string) {
  mockedApi.mockImplementation(async (requestPath) => {
    if (requestPath === "/api/dashboard") return EMPTY_DASHBOARD
    if (requestPath === "/api/settings") return SETTINGS
    if (requestPath === "/api/companies") return []
    if (requestPath === "/api/criteria")
      return { current: null, total_versions: 0 }
    return []
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
  return router
}

describe("App", () => {
  it("renders nav with all six section labels", () => {
    renderAt("/")
    for (const label of [
      "Dashboard",
      "Listings",
      "Employers",
      "Criteria",
      "Runs",
      "Settings",
    ]) {
      // The shell renders the sidebar nav; every section stays a real link.
      expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0)
    }
    // GAP-3: Get Started is gone as a nav destination.
    expect(screen.queryByRole("link", { name: "Get started" })).toBeNull()
    // The header carries the theme control for every route.
    expect(
      screen.getByRole("button", { name: /switch to (dark|light) theme/i })
    ).toBeInTheDocument()
  })

  it("redirects /onboarding to the merged Criteria page", async () => {
    const router = renderAt("/onboarding")

    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/criteria")
    )
    expect(
      await screen.findByRole("heading", {
        name: "Describe what you're looking for",
      })
    ).toBeInTheDocument()
  })

  it("navigates to Settings when clicking the Settings nav link", async () => {
    const user = userEvent.setup()
    renderAt("/")
    await user.click(screen.getAllByRole("link", { name: "Settings" })[0])
    expect(
      await screen.findByRole("heading", { name: "Settings" })
    ).toBeInTheDocument()
  })
})
