import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { routes } from "./App"

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(<RouterProvider router={router} />)
  return router
}

describe("App", () => {
  it("renders nav with all seven section labels", () => {
    renderAt("/")
    for (const label of [
      "Dashboard",
      "Listings",
      "Employers",
      "Criteria",
      "Runs",
      "Settings",
      "Get started",
    ]) {
      // The nav renders twice (mobile top bar + desktop sidebar); either
      // instance is a valid link.
      expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0)
    }
  })

  it("navigates to the Settings stub when clicking Settings", async () => {
    const user = userEvent.setup()
    renderAt("/")
    await user.click(screen.getAllByRole("link", { name: "Settings" })[0])
    expect(
      await screen.findByRole("heading", { name: "Settings" })
    ).toBeInTheDocument()
  })
})