import type { ReactElement } from "react"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost, apiPut, ApiError } from "@/lib/api"
import Settings from "../Settings"
import { DiagnosticsPanel } from "./DiagnosticsPanel"

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
const mockedApiPut = vi.mocked(apiPut)

const SETTINGS = {
  api_access: { base_url: "https://api.openai.com/v1", has_api_key: true },
  models: { triage: "gpt-4o-mini", scoring: "gpt-4o", extraction: "gpt-4o" },
  schedule: { run_at: "08:00", timezone: "UTC" },
  spend_cap: { cap_usd: 5 },
}

interface DiagResult {
  status: "pass" | "fail"
  detail: string
  remedy: string | null
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
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

const AVAILABLE_MODELS = {
  models: ["gpt-4o-mini", "gpt-4o", "gpt-4.1"],
  source: "provider" as const,
}

function mockSettings(modelsSource: "provider" | "fallback" = "provider") {
  mockedApi.mockImplementation(async (path) => {
    if (path === "/api/settings") return SETTINGS
    if (path === "/api/settings/models") {
      return modelsSource === "fallback"
        ? { models: ["gpt-4o-mini"], source: "fallback" as const }
        : AVAILABLE_MODELS
    }
    throw new Error(`unexpected GET ${path}`)
  })
}

describe("Settings", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("gates each section's save on a local change and never echoes the key", async () => {
    const user = userEvent.setup()
    mockSettings()
    renderWithProviders(<Settings />)

    const save = await screen.findByRole("button", {
      name: "Save API access",
    })
    expect(save).toBeDisabled()

    const keyField = screen.getByLabelText("API key")
    expect(keyField).toHaveValue("")
    expect(keyField).toHaveAttribute("placeholder", "•••• stored securely")

    await user.type(screen.getByLabelText("Base URL"), "x")
    expect(save).toBeEnabled()

    // Four sections, four independent saves — never one "Save all".
    expect(
      screen.getByRole("button", { name: "Save Models" })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Save Schedule" })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Save Spend cap" })
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /save all/i })).toBeNull()
  })

  it("puts only base_url when the key field was left untouched", async () => {
    const user = userEvent.setup()
    mockSettings()
    mockedApiPut.mockResolvedValue({
      base_url: "https://example.com/v1",
      has_api_key: true,
    })
    renderWithProviders(<Settings />)

    const baseUrl = await screen.findByLabelText("Base URL")
    await user.clear(baseUrl)
    await user.type(baseUrl, "https://example.com/v1")
    await user.click(screen.getByRole("button", { name: "Save API access" }))

    await waitFor(() =>
      expect(mockedApiPut).toHaveBeenCalledWith(
        "/api/settings/api-access",
        { base_url: "https://example.com/v1" }
      )
    )
    expect(await screen.findByText("Saved")).toBeInTheDocument()
  })

  it("renders a 422 as the API's own inline sentence", async () => {
    const user = userEvent.setup()
    mockSettings()
    mockedApiPut.mockRejectedValue(
      new ApiError(
        422,
        "Base URL must start with http:// or https:// — all model access routes through one OpenAI-compatible endpoint."
      )
    )
    renderWithProviders(<Settings />)

    const baseUrl = await screen.findByLabelText("Base URL")
    await user.clear(baseUrl)
    await user.type(baseUrl, "ftp://nope")
    await user.click(screen.getByRole("button", { name: "Save API access" }))

    expect(
      await screen.findByText(/Base URL must start with http:\/\//)
    ).toBeInTheDocument()
  })

  it("sends run_at and timezone together on schedule save", async () => {
    const user = userEvent.setup()
    mockSettings()
    mockedApiPut.mockResolvedValue({ run_at: "09:30", timezone: "UTC" })
    renderWithProviders(<Settings />)

    const runAt = await screen.findByLabelText("Run at")
    fireEvent.change(runAt, { target: { value: "09:30" } })
    await user.click(screen.getByRole("button", { name: "Save Schedule" }))

    await waitFor(() =>
      expect(mockedApiPut).toHaveBeenCalledWith("/api/settings/schedule", {
        run_at: "09:30",
        timezone: "UTC",
      })
    )
  })

  it("removes the spend cap with a null PUT", async () => {
    const user = userEvent.setup()
    mockSettings()
    mockedApiPut.mockResolvedValue({ cap_usd: null })
    renderWithProviders(<Settings />)

    await user.click(
      await screen.findByRole("button", { name: "Remove cap" })
    )

    expect(mockedApiPut).toHaveBeenCalledWith("/api/settings/spend-cap", {
      cap_usd: null,
    })
  })

  it("renders each stage's model as a dropdown and saves the picked id (GAP-5)", async () => {
    const user = userEvent.setup()
    mockSettings()
    mockedApiPut.mockResolvedValue({
      triage: "gpt-4.1",
      scoring: "gpt-4o",
      extraction: "gpt-4o",
    })
    renderWithProviders(<Settings />)

    // The models section is dropdowns fed by the shared available-models query.
    await user.click(
      await screen.findByRole("combobox", { name: "Triage model" })
    )
    await user.click(await screen.findByRole("option", { name: "gpt-4.1" }))
    await user.click(screen.getByRole("button", { name: "Save Models" }))

    await waitFor(() =>
      expect(mockedApiPut).toHaveBeenCalledWith("/api/settings/models", {
        triage: "gpt-4.1",
        scoring: "gpt-4o",
        extraction: "gpt-4o",
      })
    )
  })

  it("shows the fallback hint when the provider list is unavailable (GAP-5)", async () => {
    mockSettings("fallback")
    renderWithProviders(<Settings />)

    const hints = await screen.findAllByText(/Couldn't reach your provider/)
    expect(hints.length).toBeGreaterThan(0)
  })
})

describe("DiagnosticsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("runs three checks in parallel and resolves each card as it finishes (D-16, UI-05)", async () => {
    const user = userEvent.setup()
    const llm = deferred<DiagResult>()
    const db = deferred<DiagResult>()
    const emp = deferred<DiagResult>()
    mockedApiPost.mockImplementation((path: string) => {
      if (path === "/api/diagnostics/llm") return llm.promise
      if (path === "/api/diagnostics/database") return db.promise
      if (path === "/api/diagnostics/employer-fetch") return emp.promise
      throw new Error(`unexpected POST ${path}`)
    })

    render(<DiagnosticsPanel />)
    await user.click(screen.getByRole("button", { name: "Run diagnostics" }))

    expect(screen.getAllByTestId("diag-checking")).toHaveLength(3)

    await act(async () => {
      llm.resolve({
        status: "pass",
        detail: "reached the model endpoint",
        remedy: null,
      })
    })
    expect(
      await screen.findByText("reached the model endpoint")
    ).toBeInTheDocument()
    // The other two are still in flight — progressive, not all-at-once.
    expect(screen.getAllByTestId("diag-checking")).toHaveLength(2)

    await act(async () => {
      db.resolve({
        status: "fail",
        detail: "database probe failed",
        remedy: "Check that the HuntLoop data volume is mounted.",
      })
    })
    expect(
      await screen.findByText(/Check that the HuntLoop data volume is mounted/)
    ).toBeInTheDocument()
    expect(screen.getByText(/database probe failed/)).toBeInTheDocument()

    await act(async () => {
      emp.resolve({
        status: "pass",
        detail: "fetched 12 listings from Acme",
        remedy: null,
      })
    })
    expect(
      await screen.findByText("fetched 12 listings from Acme")
    ).toBeInTheDocument()
    expect(screen.queryByTestId("diag-checking")).toBeNull()
  })
})
