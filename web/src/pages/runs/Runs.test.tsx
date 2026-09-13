import type { ReactElement } from "react"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
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

const RUNNING: RunOut = {
  ...SUCCESS,
  id: "run-running",
  status: "running",
  finished_at: null,
  new_jobs_written: 0,
}

const RUNNING_DETAIL: RunDetailData = {
  ...RUNNING,
  after_dedup: 4,
  errors: [],
}

const STOPPED: RunOut = {
  ...SUCCESS,
  id: "run-stopped",
  status: "stopped",
  new_jobs_written: 2,
  error_summary: "stopped by user request",
}

const STOPPED_DETAIL: RunDetailData = {
  ...STOPPED,
  after_dedup: 12,
  errors: [],
}

const FAILED_INTERRUPTED: RunOut = {
  ...SUCCESS,
  id: "run-interrupted",
  status: "failed",
  finished_at: "2026-09-11T08:02:00Z",
  new_jobs_written: 0,
  error_summary:
    "run interrupted: the process ended before the run completed (last seen 2026-09-11T01:35:13+00:00)",
}

const FAILED_INTERRUPTED_DETAIL: RunDetailData = {
  ...FAILED_INTERRUPTED,
  after_dedup: 0,
  errors: [],
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
    vi.mocked(apiPost).mockReset()
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

  it("renders a Stop control only for an in-progress row (GAP-4)", async () => {
    mockApi([RUNNING, SUCCESS])
    renderWithProviders(<Runs />)

    // The running row offers Stop; the finished row must not.
    expect(await screen.findByText("Running")).toBeInTheDocument()
    expect(screen.getByText("Success")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Stop" })).toHaveLength(1)
  })

  it("posts to the stop endpoint and disables while the stop is honored (GAP-4)", async () => {
    const user = userEvent.setup()
    mockApi([RUNNING])
    let resolvePost!: (value: unknown) => void
    vi.mocked(apiPost).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePost = resolve
        })
    )
    renderWithProviders(<Runs />)

    await screen.findByText("Running")
    await user.click(screen.getByRole("button", { name: "Stop" }))

    expect(apiPost).toHaveBeenCalledWith("/api/runs/run-running/stop", {})
    const pending = screen.getByRole("button", { name: /stopping/i })
    expect(pending).toBeDisabled()
    // Clicking Stop must not also open the detail sheet.
    expect(screen.queryByText("Run detail")).not.toBeInTheDocument()

    resolvePost({ status: "stop requested" })
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument()
    )
  })

  it("offers Stop in the run detail only while the run is running (GAP-4)", async () => {
    const user = userEvent.setup()
    mockApi([RUNNING], RUNNING_DETAIL)
    renderWithProviders(<Runs />)

    await screen.findByText("Running")
    await user.click(screen.getAllByRole("row")[1])

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByRole("button", { name: "Stop" })).toBeInTheDocument()
  })

  it("shows a stopped run as an attention-tone fact with the stop reason (GAP-4)", async () => {
    const user = userEvent.setup()
    mockApi([STOPPED], STOPPED_DETAIL)
    renderWithProviders(<Runs />)

    // Human intervention is a fact, not a failure — amber, never destructive.
    expect(await screen.findByText("Stopped")).toBeInTheDocument()
    const badge = screen.getByTestId("run-status-stopped")
    expect(badge).toHaveAttribute("data-tone", "attention")
    expect(badge.className).not.toContain("bg-destructive")

    await user.click(screen.getAllByRole("row")[1])
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("stopped by user request")).toBeInTheDocument()
    // A terminal run offers no Stop control in the detail.
    expect(
      within(dialog).queryByRole("button", { name: /stop/i })
    ).not.toBeInTheDocument()
  })

  it("states that stopping is cooperative while a run is in progress (GAP-15)", async () => {
    mockApi([RUNNING, SUCCESS])
    renderWithProviders(<Runs />)

    await screen.findByText("Running")
    expect(screen.getByText(/cooperative/i)).toBeInTheDocument()
    // The caption is informational — it does not open the detail sheet.
    expect(screen.queryByText("Run detail")).not.toBeInTheDocument()
  })

  it("explains cooperative stopping on the Stop control (GAP-15)", async () => {
    mockApi([RUNNING])
    renderWithProviders(<Runs />)

    await screen.findByText("Running")
    const stop = screen.getByRole("button", { name: "Stop" })
    expect(stop).toHaveAttribute("title", /cooperative/i)
  })

  it("renders a reconciled interrupted run as an error fact with its reason (GAP-15)", async () => {
    const user = userEvent.setup()
    mockApi([FAILED_INTERRUPTED], FAILED_INTERRUPTED_DETAIL)
    renderWithProviders(<Runs />)

    expect(await screen.findByText("Failed")).toBeInTheDocument()
    const badge = screen.getByTestId("run-status-failed")
    expect(badge).toHaveAttribute("data-tone", "error")
    // A terminal run offers no Stop control.
    expect(
      screen.queryByRole("button", { name: "Stop" })
    ).not.toBeInTheDocument()

    await user.click(screen.getAllByRole("row")[1])
    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/run interrupted: the process ended before the run completed/)
    ).toBeInTheDocument()
  })
})
