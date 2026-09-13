import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, ApiError, apiPost } from "@/lib/api"
import Employers from "../Employers"
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

const HOOLI_CREATED: CompanyOut = {
  id: "9",
  name: "Hooli",
  ats: null,
  ats_identifier: null,
  careers_url: null,
  enabled: true,
  resolved: false,
  resolution_state: "added",
  resolution_detail: null,
  possibly_stale: false,
  staleness_message: null,
  last_job_count: null,
  last_checked_at: null,
  consecutive_empty_runs: 0,
}

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Add employer" })
  )
  return screen.findByRole("dialog")
}

describe("Add employer dialog (GAP-8/GAP-11)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("opens a dialog with a name field and an optional careers URL field", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    renderWithProviders(<Employers />)

    expect(await openDialog(user)).toBeInTheDocument()
    expect(screen.getByLabelText("Name")).toBeInTheDocument()
    expect(screen.getByLabelText(/careers url/i)).toBeInTheDocument()
  })

  it("creates, queues resolution, and closes the dialog immediately", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    mockedApiPost.mockImplementation((path) => {
      if (path === "/api/companies") return Promise.resolve(HOOLI_CREATED)
      // Resolution is in the background: never awaited by the dialog.
      if (path === "/api/companies/9/resolve") return new Promise(() => {})
      throw new Error(`unexpected POST ${path}`)
    })
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(mockedApiPost).toHaveBeenCalledWith("/api/companies", {
        name: "Hooli",
        careers_url: null,
      })
    )
    expect(mockedApiPost).toHaveBeenCalledWith("/api/companies/9/resolve", {})
    // GAP-11: no blocking wait — the dialog closes as soon as the queue fires.
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    )
    expect(
      screen.queryByText("Finding their job board…")
    ).not.toBeInTheDocument()
  })

  it("lands the new employer in the registry, reading Resolving… while it resolves", async () => {
    const user = userEvent.setup()
    let listings: CompanyOut[] = []
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return listings
      if (path === "/api/companies/coverage")
        return {
          added: listings.length,
          watchable: 0,
          needs_attention: listings.length,
          resolved: 0,
        }
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockImplementation((path) => {
      if (path === "/api/companies") {
        listings = [HOOLI_CREATED]
        return Promise.resolve(HOOLI_CREATED)
      }
      if (path === "/api/companies/9/resolve") return new Promise(() => {})
      throw new Error(`unexpected POST ${path}`)
    })
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findAllByText("Hooli")).not.toHaveLength(0)
    // GAP-13: the row is `added` on the wire plus optimistically in flight
    // while the queued trigger is pending, so it reads Resolving…
    expect(await screen.findByText("Resolving…")).toBeInTheDocument()
  })

  it("blocks an empty name inline without calling the API", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "   ")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a name")
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it("keeps the dialog open with the API error and re-enables submit when create fails", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    mockedApiPost.mockRejectedValue(new ApiError(422, "Enter a valid name"))
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid name"
    )
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled()
  })
})
