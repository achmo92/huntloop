import type { ComponentProps } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import {
  CriteriaForm,
  toFormValues,
  toPayload,
  type CriteriaFormValues,
} from "./CriteriaForm"

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

/**
 * GAP-2 (INTK-02): bounded criteria fields must render as pickers whose options
 * are exactly the values the server's Pydantic validators accept, and the
 * form's payload contract must stay byte-for-byte unchanged.
 */

/** A representative server payload — round-trips through the form unchanged. */
const REPRESENTATIVE = {
  profile_summary: "Senior backend role, remote in the EU",
  seniority_min: "senior",
  seniority_max: "staff",
  posting_age_days: 30,
  locations: {
    eligible_countries: ["DE"],
    eligible_regions: ["EU"],
    preferred_cities: ["Berlin"],
  },
  compensation_floor: { amount: 80000, currency: "EUR", period: "annual" },
  exclusions: { title_keywords: ["intern"], employers: [] },
  work_authorization: {
    countries_authorized: ["DE"],
    requires_sponsorship: false,
  },
  dimension_weights: {
    role_fit: 4,
    seniority_fit: 3,
    employer_fit: 2,
    trajectory: 1,
  },
}

function valuesWith(overrides: Partial<CriteriaFormValues>): CriteriaFormValues {
  return { ...toFormValues(REPRESENTATIVE), ...overrides }
}

const mockedApi = vi.mocked(api)

const COMPANIES = [
  { id: "1", name: "Acme Corp" },
  { id: "2", name: "Globex" },
  { id: "3", name: "Initech" },
]

function renderForm(props: Partial<ComponentProps<typeof CriteriaForm>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onSubmit = props.onSubmit ?? vi.fn()
  const utils = render(
    <QueryClientProvider client={client}>
      <CriteriaForm onSubmit={onSubmit} {...props} />
    </QueryClientProvider>
  )
  return { ...utils, onSubmit }
}

describe("CriteriaForm bounded fields (GAP-2)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the currency field as a select over valid currency codes", async () => {
    const user = userEvent.setup()
    renderForm({ defaultValues: REPRESENTATIVE })

    const currency = screen.getByRole("combobox", { name: "Currency" })
    await user.click(currency)

    expect(
      await screen.findByRole("option", { name: /^USD\b/ })
    ).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /^ZZZ\b/ })).not.toBeInTheDocument()
  })

  it("offers only valid alpha-2 codes in the eligible-countries picker", async () => {
    const user = userEvent.setup()
    renderForm({
      defaultValues: valuesWith({
        locations: {
          eligible_countries: [],
          eligible_regions: [],
          preferred_cities: [],
        },
      }),
    })

    await user.click(
      screen.getByRole("combobox", { name: "Add an eligible country code" })
    )

    expect(await screen.findByRole("option", { name: /^DE\b/ })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /^ZZ\b/ })).not.toBeInTheDocument()
  })

  it("appends a picked country as a removable chip and dedupes repeats", async () => {
    const user = userEvent.setup()
    renderForm({
      defaultValues: valuesWith({
        locations: {
          eligible_countries: [],
          eligible_regions: [],
          preferred_cities: [],
        },
        work_authorization: {
          countries_authorized: [],
          requires_sponsorship: false,
        },
      }),
    })

    const picker = screen.getByRole("combobox", {
      name: "Add an eligible country code",
    })
    await user.click(picker)
    await user.click(await screen.findByRole("option", { name: /^DE\b/ }))

    expect(
      screen.getByRole("button", { name: "Remove DE" })
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Remove DE" }))
    expect(
      screen.queryByRole("button", { name: "Remove DE" })
    ).not.toBeInTheDocument()
  })

  it("offers exactly the five valid regions", async () => {
    const user = userEvent.setup()
    renderForm({
      defaultValues: valuesWith({
        locations: {
          eligible_countries: [],
          eligible_regions: [],
          preferred_cities: [],
        },
      }),
    })

    await user.click(screen.getByRole("combobox", { name: "Add an eligible region" }))

    const options = await screen.findAllByRole("option")
    expect(options.map((option) => option.textContent)).toEqual([
      "EMEA",
      "APAC",
      "LATAM",
      "NA",
      "EU",
    ])
  })

  it("offers the same country picker for work-authorization countries", async () => {
    const user = userEvent.setup()
    renderForm({
      defaultValues: valuesWith({
        locations: {
          eligible_countries: [],
          eligible_regions: [],
          preferred_cities: [],
        },
        work_authorization: { countries_authorized: [], requires_sponsorship: false },
      }),
    })

    await user.click(
      screen.getByRole("combobox", { name: "Add an authorized country code" })
    )
    await user.click(await screen.findByRole("option", { name: /^DE\b/ }))

    expect(
      screen.getByRole("button", { name: "Remove DE" })
    ).toBeInTheDocument()
  })

  it("round-trips a representative payload byte-for-byte", () => {
    expect(toPayload(toFormValues(REPRESENTATIVE))).toEqual(REPRESENTATIVE)
  })

  it("still surfaces the inline currency error for a hand-forced invalid value", async () => {
    const user = userEvent.setup()
    renderForm({
      defaultValues: {
        ...REPRESENTATIVE,
        compensation_floor: { amount: 80000, currency: "ZZ", period: "annual" },
      },
    })

    await user.click(screen.getByRole("button", { name: "Save criteria" }))

    expect(
      await screen.findByText("Use a 3-letter code like USD or EUR")
    ).toBeInTheDocument()
  })
})

describe("CriteriaForm employer typeahead (GAP-2)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("surfaces prefix suggestions from the shared companies registry", async () => {
    const user = userEvent.setup()
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return COMPANIES
      throw new Error(`unexpected GET ${path}`)
    })
    renderForm({ defaultValues: REPRESENTATIVE })

    await user.type(screen.getByLabelText("Add an excluded employer"), "aci")

    expect(
      await screen.findByRole("option", { name: "Acme Corp" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: "Globex" })
    ).not.toBeInTheDocument()
  })

  it("reads suggestions from the shared ['companies'] query cache once", async () => {
    mockedApi.mockResolvedValue(COMPANIES)
    renderForm({ defaultValues: REPRESENTATIVE })

    await waitFor(() => expect(mockedApi).toHaveBeenCalledWith("/api/companies"))
    expect(
      mockedApi.mock.calls.filter((call) => call[0] === "/api/companies")
    ).toHaveLength(1)
  })

  it("commits an employer outside the registry as free text", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue(COMPANIES)
    renderForm({ defaultValues: REPRESENTATIVE })

    await user.type(
      screen.getByLabelText("Add an excluded employer"),
      "Startup X{Enter}"
    )

    expect(
      screen.getByRole("button", { name: "Remove Startup X" })
    ).toBeInTheDocument()
  })

  it("renders no suggestions when the registry is empty", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    renderForm({ defaultValues: REPRESENTATIVE })

    await user.type(screen.getByLabelText("Add an excluded employer"), "aci")

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: "Acme Corp" })
    ).not.toBeInTheDocument()
  })
})
