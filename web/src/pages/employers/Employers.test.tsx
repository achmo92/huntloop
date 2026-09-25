import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPatch, apiPost } from "@/lib/api"
import Employers from "../Employers"
import { RegistryTable } from "./RegistryTable"
import type { CompanyOut } from "../criteria/types"

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
const mockedApiPatch = vi.mocked(apiPatch)

const RESOLVED: CompanyOut = {
  id: "1",
  name: "Acme",
  ats: "greenhouse",
  ats_identifier: "acme",
  careers_url: "https://boards.greenhouse.io/acme",
  enabled: true,
  resolved: true,
  resolution_state: "resolved",
  resolution_detail: null,
  possibly_stale: false,
  staleness_message: null,
  last_job_count: 12,
  last_checked_at: "2026-09-10T08:00:00Z",
  consecutive_empty_runs: 0,
}

const NEEDS_ATTENTION: CompanyOut = {
  id: "2",
  name: "Globex",
  ats: null,
  ats_identifier: null,
  careers_url: null,
  enabled: true,
  resolved: false,
  resolution_state: "error",
  resolution_detail: "No job board found at the guessed URL",
  possibly_stale: false,
  staleness_message: null,
  last_job_count: null,
  last_checked_at: null,
  consecutive_empty_runs: 0,
}

const DISABLED: CompanyOut = {
  id: "3",
  name: "Initech",
  ats: "lever",
  ats_identifier: "initech",
  careers_url: null,
  enabled: false,
  resolved: true,
  resolution_state: "resolved",
  resolution_detail: null,
  possibly_stale: false,
  staleness_message: null,
  last_job_count: 3,
  last_checked_at: "2026-09-09T08:00:00Z",
  consecutive_empty_runs: 0,
}

const STALE: CompanyOut = {
  id: "4",
  name: "Umbrella",
  ats: "ashby",
  ats_identifier: "umbrella",
  careers_url: null,
  enabled: true,
  resolved: true,
  resolution_state: "resolved",
  resolution_detail: null,
  possibly_stale: true,
  staleness_message: "No jobs found in the last 5 runs",
  last_job_count: 0,
  last_checked_at: "2026-08-01T08:00:00Z",
  consecutive_empty_runs: 5,
}

describe("Employers page", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows a recoverable error instead of dependent registry panels", async () => {
    mockedApi.mockRejectedValue(new Error("offline"))

    renderWithProviders(<Employers />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't load your employers"
    )
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
    expect(screen.queryByText("Coverage")).not.toBeInTheDocument()
  })
})

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

describe("RegistryTable", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders a resolved row with platform + board id and a needs-attention row with its detail", async () => {
    mockedApi.mockResolvedValue([RESOLVED, NEEDS_ATTENTION])
    renderWithProviders(<RegistryTable />)

    expect(await screen.findByText("Greenhouse · acme")).toBeInTheDocument()
    expect(
      screen.getByText("No job board found at the guessed URL")
    ).toBeInTheDocument()
  })

  it("disables in place: the PATCH fires and the row stays in the list", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([RESOLVED])
    mockedApiPatch.mockResolvedValue({ ...RESOLVED, enabled: false })
    renderWithProviders(<RegistryTable />)

    const toggle = await screen.findByRole("switch", { name: "Disable Acme" })
    await user.click(toggle)

    expect(mockedApiPatch).toHaveBeenCalledWith("/api/companies/1", {
      enabled: false,
    })
    // REG-06: disable never removes the row.
    expect(screen.getByText("Acme")).toBeInTheDocument()
  })

  it("retries a failed resolution and shows a retrying state", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([NEEDS_ATTENTION])
    mockedApiPost.mockReturnValue(new Promise(() => {}))
    renderWithProviders(<RegistryTable />)

    const retry = await screen.findByRole("button", {
      name: "Retry resolution for Globex",
    })
    await user.click(retry)

    expect(mockedApiPost).toHaveBeenCalledWith("/api/companies/2/resolve", {})
    expect(await screen.findByText("Retrying…")).toBeInTheDocument()
  })

  it("hides disabled rows only while the opt-in filter is checked", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([RESOLVED, DISABLED])
    renderWithProviders(<RegistryTable />)

    expect(await screen.findByText("Initech")).toBeInTheDocument()
    await user.click(screen.getByRole("checkbox", { name: "Hide disabled" }))

    expect(screen.queryByText("Initech")).not.toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
  })

  it("renders the staleness badge with its message", async () => {
    mockedApi.mockResolvedValue([STALE])
    renderWithProviders(<RegistryTable />)

    expect(await screen.findByText("Possibly stale")).toBeInTheDocument()
    expect(
      screen.getByText("No jobs found in the last 5 runs")
    ).toBeInTheDocument()
  })
})

describe("Employers page", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders the empty state with the describe action when there are no companies", async () => {
    mockedApi.mockResolvedValue([])
    renderWithProviders(<Employers />)

    expect(await screen.findByText("No employers yet")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Describe your search" })
    ).toBeInTheDocument()
  })

  it("keeps the honest coverage headline visible above the registry (D-06)", async () => {
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return [RESOLVED, NEEDS_ATTENTION]
      if (path === "/api/companies/coverage")
        return { added: 3, watchable: 2, needs_attention: 1, resolved: 2 }
      throw new Error(`unexpected GET ${path}`)
    })
    renderWithProviders(<Employers />)

    expect(
      await screen.findByText(/watching 2 of your 3 added employers/)
    ).toBeInTheDocument()
    expect(screen.getByText("Acme")).toBeInTheDocument()
  })
})
