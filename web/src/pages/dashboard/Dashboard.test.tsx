import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost, ApiError } from "@/lib/api"
import Dashboard from "../Dashboard"

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    ApiError,
    api: vi.fn(),
    apiPost: vi.fn(),
    apiPatch: vi.fn(),
    apiPut: vi.fn(),
  }
})

const mockedApi = vi.mocked(api)
const mockedApiPost = vi.mocked(apiPost)

const RUN = {
  id: "run-1",
  started_at: "2026-09-11T08:00:00Z",
  finished_at: "2026-09-11T08:05:00Z",
  trigger: "scheduled",
  status: "success",
  companies_checked: 5,
  listings_fetched: 42,
  after_deterministic: 30,
  after_triage: 18,
  scored: 12,
  new_jobs_written: 7,
  tokens_in: 1200,
  tokens_out: 480,
  cost_usd: 0.0123,
  error_summary: null,
}

const DASHBOARD = {
  last_run: RUN,
  funnel: {
    companies_checked: 5,
    listings_fetched: 42,
    after_dedup: 38,
    after_deterministic: 30,
    after_triage: 18,
    scored: 12,
    new_jobs_written: 7,
  },
  next_scheduled_run: "2026-09-12T08:00:00Z",
  listings_by_status: {
    new: 0,
    shortlisted: 2,
    applied: 1,
    interviewing: 0,
    offer: 0,
    rejected: 3,
    withdrawn: 0,
  },
  total_listings: 6,
}

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

const QUIET_DASHBOARD = {
  ...DASHBOARD,
  last_run: { ...RUN, new_jobs_written: 0, scored: 0, status: "capped" },
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
  api_access: { base_url: "https://api.openai.com/v1", has_api_key: true },
  models: { triage: "t", scoring: "s", extraction: "e" },
  schedule: { run_at: "08:00", timezone: "UTC" },
  spend_cap: { cap_usd: null },
}

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

function mockApi(dashboard: unknown) {
  mockedApi.mockImplementation(async (path) => {
    if (path === "/api/dashboard") return dashboard
    if (path === "/api/settings") return SETTINGS
    throw new Error(`unexpected GET ${path}`)
  })
}

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders last run, all seven funnel stages, next run and shortcuts (UI-02, D-14)", async () => {
    mockApi(DASHBOARD)
    renderWithProviders(<Dashboard />)

    expect(await screen.findByText("Last run")).toBeInTheDocument()
    expect(screen.getByText(/Cost \$0\.0123/)).toBeInTheDocument()

    const stages = screen.getAllByTestId("funnel-stage")
    expect(stages).toHaveLength(7)
    expect(stages[0]).toHaveTextContent("Employers checked")
    expect(stages[0]).toHaveTextContent("5")
    expect(stages[1]).toHaveTextContent("42")

    expect(screen.getByText(/Next run:/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Listings" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Employers" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Criteria" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument()
  })

  it("renders all seven status counts with zero values visible", async () => {
    mockApi(QUIET_DASHBOARD)
    renderWithProviders(<Dashboard />)

    await screen.findByText("Listings by status")
    const counts = screen.getAllByTestId("listing-status-count")
    expect(counts).toHaveLength(7)
    counts.forEach((count) => expect(count).toHaveTextContent("0"))

    for (const label of [
      "New",
      "Shortlisted",
      "Applied",
      "Interviewing",
      "Offer",
      "Rejected",
      "Withdrawn",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it("posts a run and shows the started toast (RUN-02, D-17)", async () => {
    const user = userEvent.setup()
    mockApi(DASHBOARD)
    mockedApiPost.mockResolvedValue({ status: "accepted" })
    renderWithProviders(<Dashboard />)

    await screen.findByText("Last run")
    await user.click(screen.getByRole("button", { name: "Run now" }))

    expect(mockedApiPost).toHaveBeenCalledWith("/api/runs", {})
    expect(await screen.findByText("Run started")).toBeInTheDocument()
  })

  it("turns a 409 into the in-progress sentence, not a crash (D-17)", async () => {
    const user = userEvent.setup()
    mockApi(DASHBOARD)
    mockedApiPost.mockRejectedValue(
      new ApiError(
        409,
        "a run is already in progress (started 2026-09-11T08:00:00Z)"
      )
    )
    renderWithProviders(<Dashboard />)

    await screen.findByText("Last run")
    await user.click(screen.getByRole("button", { name: "Run now" }))

    expect(
      await screen.findByText(/already in progress/)
    ).toBeInTheDocument()
    // The page is still fully usable — no error wall.
    expect(screen.getByText("Last run")).toBeInTheDocument()
  })

  it("disables Run now while a run is live (D-17)", async () => {
    mockApi({ ...DASHBOARD, last_run: { ...RUN, status: "running" } })
    renderWithProviders(<Dashboard />)

    const button = await screen.findByRole("button", { name: "Running…" })
    expect(button).toBeDisabled()
  })

  it("shows the first-time empty state for a new user (D-18, UI-07)", async () => {
    mockApi(EMPTY_DASHBOARD)
    renderWithProviders(<Dashboard />)

    expect(await screen.findByText("Welcome to HuntLoop")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Describe your search" })
    ).toBeInTheDocument()
    expect(screen.queryByText("Last run")).not.toBeInTheDocument()
  })
})
