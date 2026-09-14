import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { applyTheme, resolveTheme, THEME_STORAGE_KEY } from "@/lib/theme"
import { ThemeProvider, useTheme } from "@/components/theme/ThemeProvider"

/**
 * The theme runtime is behavior, not pixels: an explicit choice beats the
 * system, a stored choice survives reloads, and with no choice the app follows
 * `prefers-color-scheme` live. jsdom ships no `matchMedia`, so every test
 * installs a controllable stub and tears it down.
 */

interface MatchMediaController {
  fire: (matches: boolean) => void
}

function installMatchMedia(initialDark: boolean): MatchMediaController {
  let matches = initialDark
  const listeners = new Set<(event: { matches: boolean }) => void>()
  const queryList = {
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    get matches() {
      return matches
    },
    addEventListener: (_type: string, listener: (event: { matches: boolean }) => void) => {
      listeners.add(listener)
    },
    removeEventListener: (_type: string, listener: (event: { matches: boolean }) => void) => {
      listeners.delete(listener)
    },
    addListener: (listener: (event: { matches: boolean }) => void) => {
      listeners.add(listener)
    },
    removeListener: (listener: (event: { matches: boolean }) => void) => {
      listeners.delete(listener)
    },
    dispatchEvent: () => true,
  }
  vi.stubGlobal("matchMedia", vi.fn(() => queryList))
  return {
    fire: (next: boolean) => {
      matches = next
      listeners.forEach((listener) => listener({ matches: next }))
    },
  }
}

function Probe() {
  const { theme, resolvedTheme, setTheme, toggleTheme } = useTheme()
  return (
    <div>
      <span data-testid="theme">{theme ?? "system"}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <button type="button" onClick={toggleTheme}>
        toggle
      </button>
      <button type="button" onClick={() => setTheme("light")}>
        force light
      </button>
    </div>
  )
}

function renderProbe() {
  return render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>
  )
}

const htmlHasDark = () => document.documentElement.classList.contains("dark")

describe("theme runtime", () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.className = ""
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("defaults to dark when the system prefers dark and nothing is stored", () => {
    installMatchMedia(true)
    renderProbe()
    expect(screen.getByTestId("theme")).toHaveTextContent("system")
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark")
    expect(htmlHasDark()).toBe(true)
  })

  it("defaults to light when the system prefers light and nothing is stored", () => {
    installMatchMedia(false)
    renderProbe()
    expect(screen.getByTestId("resolved")).toHaveTextContent("light")
    expect(htmlHasDark()).toBe(false)
  })

  it("lets a stored dark choice win over a light system preference", () => {
    installMatchMedia(false)
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark")
    renderProbe()
    expect(screen.getByTestId("theme")).toHaveTextContent("dark")
    expect(htmlHasDark()).toBe(true)
  })

  it("lets a stored light choice win over a dark system preference", () => {
    installMatchMedia(true)
    window.localStorage.setItem(THEME_STORAGE_KEY, "light")
    renderProbe()
    expect(screen.getByTestId("resolved")).toHaveTextContent("light")
    expect(htmlHasDark()).toBe(false)
  })

  it("ignores an invalid stored value and falls back to the system", () => {
    installMatchMedia(true)
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia")
    renderProbe()
    expect(screen.getByTestId("theme")).toHaveTextContent("system")
    expect(htmlHasDark()).toBe(true)
  })

  it("toggles the class and persists the explicit choice", async () => {
    installMatchMedia(false)
    const user = userEvent.setup()
    renderProbe()

    await user.click(screen.getByRole("button", { name: "toggle" }))
    expect(htmlHasDark()).toBe(true)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark")

    await user.click(screen.getByRole("button", { name: "toggle" }))
    expect(htmlHasDark()).toBe(false)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light")
  })

  it("setTheme removes the class and persists the explicit choice", async () => {
    installMatchMedia(true)
    const user = userEvent.setup()
    renderProbe()

    await user.click(screen.getByRole("button", { name: "force light" }))
    expect(htmlHasDark()).toBe(false)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light")
  })

  it("follows a live system change while no explicit choice is stored", () => {
    const controller = installMatchMedia(false)
    renderProbe()
    expect(htmlHasDark()).toBe(false)

    controller.fire(true)
    expect(htmlHasDark()).toBe(true)
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it("throws a clear error when useTheme is used outside a provider", () => {
    installMatchMedia(false)
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(
      /useTheme must be used within a ThemeProvider/
    )
    consoleError.mockRestore()
  })
})

describe("theme helpers", () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.className = ""
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("resolveTheme prefers the explicit choice and otherwise the system", () => {
    installMatchMedia(true)
    expect(resolveTheme("light")).toBe("light")
    expect(resolveTheme("dark")).toBe("dark")
    expect(resolveTheme(null)).toBe("dark")
  })

  it("applyTheme toggles the .dark class on the document element", () => {
    applyTheme("dark")
    expect(htmlHasDark()).toBe(true)
    applyTheme("light")
    expect(htmlHasDark()).toBe(false)
  })
})
