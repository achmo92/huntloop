import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, ApiError, apiPatch } from "@/lib/api"
import { RegistryTable } from "./RegistryTable"
import { SetBoardDialog } from "./SetBoardDialog"
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
const mockedApiPatch = vi.mocked(apiPatch)

const FAILURE_SENTENCE =
  "We couldn't find a supported job board for this employer automatically. Retry, or set the job board manually."

function company(overrides: Partial<CompanyOut>): CompanyOut {
  return {
    id: "2",
    name: "Globex",
    ats: null,
    ats_identifier: null,
    careers_url: null,
    enabled: true,
    resolved: false,
    resolution_state: "error",
    resolution_detail: FAILURE_SENTENCE,
    possibly_stale: false,
    staleness_message: null,
    last_job_count: null,
    last_checked_at: null,
    consecutive_empty_runs: 0,
    ...overrides,
  }
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function renderWithProviders(ui: React.ReactElement, client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe("Set board manually dialog (GAP-14)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders a platform dropdown, a board id field, and an optional careers URL field", async () => {
    const client = newClient()
    renderWithProviders(
      <SetBoardDialog company={company({})} onClose={() => {}} />,
      client
    )

    expect(await screen.findByRole("dialog")).toBeInTheDocument()
    expect(
      screen.getByRole("combobox", { name: "Platform" })
    ).toBeInTheDocument()
    expect(screen.getByLabelText("Board id or slug")).toBeInTheDocument()
    expect(screen.getByLabelText(/careers url/i)).toBeInTheDocument()
  })

  it("saves the chosen board, invalidates companies and coverage, and closes", async () => {
    const user = userEvent.setup()
    const client = newClient()
    const invalidate = vi.spyOn(client, "invalidateQueries")
    const onClose = vi.fn()
    mockedApiPatch.mockResolvedValue(
      company({ resolution_state: "resolved", ats: "lever" })
    )
    renderWithProviders(
      <SetBoardDialog company={company({})} onClose={onClose} />,
      client
    )

    await user.click(
      await screen.findByRole("combobox", { name: "Platform" })
    )
    await user.click(await screen.findByRole("option", { name: "Lever" }))
    await user.type(screen.getByLabelText("Board id or slug"), "acme")
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(mockedApiPatch).toHaveBeenCalledWith("/api/companies/2", {
        ats: "lever",
        ats_identifier: "acme",
        careers_url: null,
      })
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["companies"] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["coverage"] })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it("surfaces a 4xx inline and stays open", async () => {
    const user = userEvent.setup()
    const client = newClient()
    const onClose = vi.fn()
    mockedApiPatch.mockRejectedValue(
      new ApiError(
        422,
        "Unsupported platform 'workday'. Choose one of: ashby, greenhouse, lever."
      )
    )
    renderWithProviders(
      <SetBoardDialog company={company({})} onClose={onClose} />,
      client
    )

    await user.type(await screen.findByLabelText("Board id or slug"), "acme")
    await user.click(screen.getByRole("button", { name: "Save" }))

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Unsupported platform 'workday'")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it("the Error row exposes a Set board manually action", async () => {
    const user = userEvent.setup()
    const client = newClient()
    mockedApi.mockResolvedValue([company({ id: "2", name: "Globex" })])
    renderWithProviders(<RegistryTable />, client)

    await user.click(
      await screen.findByRole("button", {
        name: "Set board manually for Globex",
      })
    )

    expect(await screen.findByRole("dialog")).toBeInTheDocument()
    expect(screen.getByText("Set job board")).toBeInTheDocument()
  })
})
