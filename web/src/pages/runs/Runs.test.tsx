import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import Runs from "../Runs"
import type { RunDetail as RunDetailData, RunOut } from "./types"

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

const SUCCESS: RunOut = {
  id: "run-success",
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

const CAPPED: RunOut = {
  ...SUCCESS,
  id: "run-capped",
  status: "capped",
  new_jobs_written: 2,
  error_summary: "Spend cap reached mid-run",
}

const SKIPPED: RunOut = {
  ...SUCCESS,
  id: "run-skipped",
  trigger: "scheduled",
  status: "skipped",
  error_summary: "a previous run was still in progress",
}

const DETAIL: RunDetailData = {
  ...SUCCESS,
  after_dedup: 38,
  errors: [
    { company_name: "Acme", stage: "score", message: "model timed out" },
    { company_name: "Globex", stage: "fetch", message: "board returned 503" },
  ],
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

function mockApi(runs: RunOut[], detail?: RunDetailData) {
  mockedApi.mockImplementation(async (path) => {
    if (path === "/api/runs?limit=50") return runs
    if (detail && path === `/api/runs/${detail.id}`) return detail
    throw new Error(`unexpected GET ${path}`)
  })
}

describe("Runs", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the history with per-stage counts and non-error guardrail badges (RUN-09)", async () => {
    mockApi([SUCCESS, CAPPED, SKIPPED])
    renderWithProviders(<Runs />)

    expect(await screen.findByText("Success")).toBeInTheDocument()
    expect(screen.getByText("Capped")).toBeInTheDocument()
    expect(screen.getByText("Skipped")).toBeInTheDocument()

    // A guardrail firing is a fact, not a failure.
    const capped = screen.getByTestId("run-status-capped")
    expect(capped).toHaveAttribute("data-tone", "attention")
    expect(capped.className).not.toContain("bg-destructive")
    const skipped = screen.getByTestId("run-status-skipped")
    expect(skipped).toHaveAttribute("data-tone", "attention")
    expect(skipped.className).not.toContain("bg-destructive")

    // The stage columns are present with the seeded values.
    expect(screen.getAllByText("42").length).toBeGreaterThan(0)
    expect(screen.getAllByText("$0.0123").length).toBeGreaterThan(0)
  })

  it("opens a run's detail and lists every failure with employer and stage (RUN-06)", async () => {
    const user = userEvent.setup()
    mockApi([DETAIL], DETAIL)
    renderWithProviders(<Runs />)

    await screen.findByText("Success")
    const rows = screen.getAllByRole("row")
    await user.click(rows[1])

    expect(await screen.findByText("Run detail")).toBeInTheDocument()
    const errors = await screen.findAllByTestId("run-error")
    expect(errors).toHaveLength(2)
    expect(errors[0]).toHaveTextContent("Acme")
    expect(errors[0]).toHaveTextContent("score")
    expect(errors[0]).toHaveTextContent("model timed out")
    expect(errors[1]).toHaveTextContent("Globex")
    expect(errors[1]).toHaveTextContent("fetch")
  })

  it("shows the empty state with a run CTA when no runs exist (UI-07)", async () => {
    mockApi([])
    renderWithProviders(<Runs />)

    expect(await screen.findByText("No runs yet")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run now" })).toBeInTheDocument()
  })
})
