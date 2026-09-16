import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { AppShell } from "@/components/shell/AppShell"
import { NAV_ITEMS } from "@/components/shell/nav"

/**
 * The shell is the frame every page is read inside: seven icon+label entries,
 * one unmistakable active route, a fold that remembers itself, a mobile drawer,
 * and a header that carries the current section's context without competing
 * with the page's own <h1>.
 */

const SECTIONS = [
  "Dashboard",
  "Listings",
  "Employers",
  "Criteria",
  "Proposals",
  "Runs",
  "Settings",
]

const SIDEBAR_KEY = "huntloop-sidebar"

function renderShell(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <AppShell />,
        children: [
          { index: true, element: <div>dashboard content</div> },
          { path: "listings", element: <div>listings content</div> },
          { path: "employers", element: <div>employers content</div> },
          { path: "criteria", element: <div>criteria content</div> },
          { path: "proposals", element: <div>proposals content</div> },
          { path: "runs", element: <div>runs content</div> },
          { path: "settings", element: <div>settings content</div> },
        ],
      },
    ],
    { initialEntries: [path] }
  )
  const view = render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
  return { router, ...view }
}

const primaryNav = () => screen.getByRole("navigation", { name: "Primary" })

describe("AppShell sidebar", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    document.documentElement.className = ""
  })

  it("exposes all seven sections as icon+label links", () => {
    renderShell("/")
    for (const label of SECTIONS) {
      const links = screen.getAllByRole("link", { name: label })
      expect(links.length).toBeGreaterThan(0)
      expect(links[0].querySelector("svg")).not.toBeNull()
    }
  })

  it("places Proposals between Criteria and Runs in the nav", () => {
    const labels = NAV_ITEMS.map((item) => item.label)
    const proposals = labels.indexOf("Proposals")
    expect(proposals).toBeGreaterThan(-1)
    expect(proposals).toBe(labels.indexOf("Criteria") + 1)
    expect(proposals).toBe(labels.indexOf("Runs") - 1)
    expect(NAV_ITEMS[proposals].to).toBe("/proposals")
  })

  it("marks exactly one active route with aria-current='page'", () => {
    renderShell("/listings")
    const active = screen.getAllByRole("link", { current: "page" })
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveAccessibleName("Listings")
  })

  it("moves the active marker with the route", () => {
    renderShell("/runs")
    const active = screen.getAllByRole("link", { current: "page" })
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveAccessibleName("Runs")
  })

  it("renders a collapsed rail from the stored fold preference", () => {
    window.localStorage.setItem(SIDEBAR_KEY, "collapsed")
    renderShell("/listings")

    expect(primaryNav()).toHaveAttribute("data-collapsed", "true")
    const listings = screen.getAllByRole("link", { name: "Listings" })[0]
    // The label is visually hidden but the accessible name survives.
    expect(listings.querySelector("span.sr-only")).toHaveTextContent("Listings")
    expect(listings).toHaveAttribute("title", "Listings")
    expect(listings).toHaveAttribute("aria-current", "page")
  })

  it("persists the fold choice and reads it back on remount", async () => {
    const user = userEvent.setup()
    const first = renderShell("/")
    expect(primaryNav()).toHaveAttribute("data-collapsed", "false")

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }))
    expect(window.localStorage.getItem(SIDEBAR_KEY)).toBe("collapsed")
    expect(primaryNav()).toHaveAttribute("data-collapsed", "true")

    first.unmount()
    renderShell("/")
    expect(primaryNav()).toHaveAttribute("data-collapsed", "true")
  })
})

describe("AppShell mobile drawer", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    document.documentElement.className = ""
  })

  it("opens the same seven-section nav from the header trigger", async () => {
    const user = userEvent.setup()
    renderShell("/listings")

    const trigger = screen.getByRole("button", { name: "Open navigation" })
    expect(trigger).toHaveAttribute("aria-expanded", "false")

    await user.click(trigger)

    const drawer = await screen.findByRole("dialog")
    for (const label of SECTIONS) {
      expect(
        within(drawer).getAllByRole("link", { name: label }).length
      ).toBeGreaterThan(0)
    }
    const active = within(drawer).getAllByRole("link", { current: "page" })
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveAccessibleName("Listings")
    expect(trigger).toHaveAttribute("aria-expanded", "true")
  })

  it("closes the drawer once a section is chosen", async () => {
    const user = userEvent.setup()
    const { router } = renderShell("/")

    await user.click(screen.getByRole("button", { name: "Open navigation" }))
    const drawer = await screen.findByRole("dialog")

    await user.click(within(drawer).getAllByRole("link", { name: "Runs" })[0])

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(router.state.location.pathname).toBe("/runs")
  })
})

describe("AppShell accessibility", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("puts a skip link first in focus order, targeting main", () => {
    const { container } = renderShell("/")

    const skip = screen.getByRole("link", { name: /skip to content/i })
    expect(skip).toHaveAttribute("href", "#main")
    expect(container.querySelector("main#main")).not.toBeNull()

    const firstFocusable = container.querySelector(
      "a[href], button, [tabindex]:not([tabindex='-1'])"
    )
    expect(firstFocusable).toBe(skip)
  })
})

describe("AppShell header", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("shows the current section context and the theme toggle", () => {
    renderShell("/listings")
    const banner = screen.getByRole("banner")
    expect(within(banner).getByText("Listings")).toBeInTheDocument()
    expect(
      within(banner).getByRole("button", {
        name: /switch to (dark|light) theme/i,
      })
    ).toBeInTheDocument()
  })

  it("does not render a heading (the page owns the only h1)", () => {
    renderShell("/settings")
    const banner = screen.getByRole("banner")
    expect(within(banner).queryByRole("heading")).toBeNull()
  })
})
