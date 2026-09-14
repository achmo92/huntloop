import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { AppShell } from "@/components/shell/AppShell"

/**
 * The shell is the frame every page is read inside: six icon+label entries, one
 * unmistakable active route, and a header that carries the current section's
 * context without competing with the page's own <h1>.
 */

const SECTIONS = [
  "Dashboard",
  "Listings",
  "Employers",
  "Criteria",
  "Runs",
  "Settings",
]

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
          { path: "runs", element: <div>runs content</div> },
          { path: "settings", element: <div>settings content</div> },
        ],
      },
    ],
    { initialEntries: [path] }
  )
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  )
  return router
}

describe("AppShell sidebar", () => {
  it("exposes all six sections as icon+label links", () => {
    renderShell("/")
    for (const label of SECTIONS) {
      const links = screen.getAllByRole("link", { name: label })
      expect(links.length).toBeGreaterThan(0)
      expect(links[0].querySelector("svg")).not.toBeNull()
    }
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
})

describe("AppShell header", () => {
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
