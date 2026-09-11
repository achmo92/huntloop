import type { ReactElement } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPatch } from "@/lib/api"
import Listings from "../Listings"
import type { JobListResponse, JobRow } from "./ListingsTable"

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
const mockedApiPatch = vi.mocked(apiPatch)

const ROW: JobRow = {
  id: "job-1",
  company_id: "company-1",
  company_name: "Acme",
  title: "Senior Backend Engineer",
  location: "Berlin",
  is_remote: true,
  comp_min: 90000,
  comp_max: 120000,
  comp_currency: "EUR",
  comp_period: "annual",
  score_overall: 4.25,
  score_flags: { stretch_role: true },
  status: "new",
  posted_at: "2026-09-01T08:00:00Z",
  first_seen_at: "2026-09-02T08:00:00Z",
  url: "https://boards.example.com/acme/1",
}

const LIST: JobListResponse = {
  items: [ROW],
  total: 1,
  page: 1,
  page_size: 50,
}

const EMPTY: JobListResponse = { items: [], total: 0, page: 1, page_size: 50 }

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

function mockJobs(list: JobListResponse) {
  mockedApi.mockImplementation(async (path) => {
    if (path === "/api/companies") return []
    if (path.startsWith("/api/jobs?")) return list
    throw new Error(`unexpected GET ${path}`)
  })
}

function calledPaths(): string[] {
  return mockedApi.mock.calls.map((call) => String(call[0]))
}

function seedFilters(overrides: Record<string, unknown>) {
  localStorage.setItem(
    "huntloop.listings.filters",
    JSON.stringify({
      status: [],
      employer_id: null,
      score_min: null,
      posted_after: null,
      posted_before: null,
      ...overrides,
    })
  )
}

describe("Listings workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it("composes status + score threshold into one query (D-12)", async () => {
    const user = userEvent.setup()
    mockJobs(LIST)
    renderWithProviders(<Listings />)
    await screen.findByText(ROW.title)

    await user.click(screen.getByRole("button", { name: "New" }))
    await user.type(screen.getByLabelText("Minimum score"), "3")

    await waitFor(() => {
      expect(
        calledPaths().some(
          (path) =>
            path.includes("status=new") && path.includes("score_min=3")
        )
      ).toBe(true)
    })
  })

  it("rehydrates the composed filters from localStorage (D-12)", async () => {
    seedFilters({ status: ["new"], score_min: 3 })
    mockJobs(LIST)
    renderWithProviders(<Listings />)

    const badge = await screen.findByRole("button", { name: "New" })
    expect(badge).toHaveAttribute("aria-pressed", "true")
    await waitFor(() => {
      expect(
        calledPaths().some(
          (path) =>
            path.includes("status=new") && path.includes("score_min=3")
        )
      ).toBe(true)
    })
  })

  it("moves a listing to a new stage in one action (D-10, TRAK-01)", async () => {
    const user = userEvent.setup()
    mockJobs(LIST)
    mockedApiPatch.mockResolvedValue({
      id: ROW.id,
      status: "shortlisted",
      changed_at: "2026-09-11T10:00:00Z",
    })
    renderWithProviders(<Listings />)
    await screen.findByText(ROW.title)

    await user.click(
      screen.getByRole("combobox", { name: "Pipeline stage" })
    )
    await user.click(await screen.findByRole("option", { name: "Shortlisted" }))

    await waitFor(() => {
      expect(mockedApiPatch).toHaveBeenCalledWith(
        `/api/jobs/${ROW.id}/status`,
        { status: "shortlisted" }
      )
    })
    expect(
      screen.getByRole("combobox", { name: "Pipeline stage" })
    ).toHaveTextContent("Shortlisted")
  })

  it("flips sort order when the Score header is clicked (TRAK-05)", async () => {
    const user = userEvent.setup()
    mockJobs(LIST)
    renderWithProviders(<Listings />)
    await screen.findByText(ROW.title)

    await user.click(screen.getByRole("button", { name: "Score" }))

    await waitFor(() => {
      expect(
        calledPaths().some(
          (path) =>
            path.includes("sort=score_overall") && path.includes("order=asc")
        )
      ).toBe(true)
    })
  })

  it("renders an unscored listing's score blank rather than zero", async () => {
    const unscored: JobRow = {
      ...ROW,
      id: "job-2",
      title: "Unscored Role",
      score_overall: null,
    }
    mockJobs({ items: [unscored], total: 1, page: 1, page_size: 50 })
    renderWithProviders(<Listings />)
    await screen.findByText("Unscored Role")

    expect(screen.getByTestId("score-value").textContent).toBe("")
  })

  it("points at discovery when there are no listings and no filters (UI-07)", async () => {
    mockJobs(EMPTY)
    renderWithProviders(<Listings />)

    expect(await screen.findByText("No listings yet")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Run discovery" })
    ).toBeInTheDocument()
  })

  it("offers to clear filters when filters match nothing (UI-07)", async () => {
    seedFilters({ status: ["new"] })
    mockJobs(EMPTY)
    renderWithProviders(<Listings />)

    expect(
      await screen.findByText("No listings match these filters")
    ).toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "Clear filters" }).length
    ).toBeGreaterThan(0)
  })
})
