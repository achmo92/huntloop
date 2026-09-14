import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import {
  applyTheme,
  readStoredTheme,
  resolveTheme,
  THEME_STORAGE_KEY,
  type Theme,
} from "@/lib/theme"

/**
 * Theme runtime (D-24a): `theme` is the *explicit* user choice, or `null` for
 * "follow the system". Only explicit choices are persisted, so the toggle stays
 * an honest two-state control while the default genuinely tracks the OS.
 */

interface ThemeContextValue {
  /** The explicit choice, or `null` when following the system. */
  theme: Theme | null
  /** The theme actually in effect right now. */
  resolvedTheme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function writeStoredTheme(theme: Theme): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Storage denied (private mode): the session still switches, it just
    // won't be remembered.
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme | null>(() => readStoredTheme())
  // A tick bumped by the OS listener; `resolvedTheme` then re-reads matchMedia.
  const [, forceSystemTick] = useState(0)

  // While no explicit choice is stored, follow the system live.
  useEffect(() => {
    if (theme !== null) return
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return
    }
    const query = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => forceSystemTick((tick) => tick + 1)
    query.addEventListener("change", onChange)
    return () => query.removeEventListener("change", onChange)
  }, [theme])

  const resolvedTheme = theme ?? resolveTheme(null)

  // Pre-paint already set the class; this keeps it correct on every change.
  useEffect(() => {
    applyTheme(resolvedTheme)
  }, [resolvedTheme])

  const setTheme = useCallback((next: Theme) => {
    writeStoredTheme(next)
    setThemeState(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(resolvedTheme === "dark" ? "light" : "dark")
  }, [resolvedTheme, setTheme])

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme, toggleTheme }),
    [theme, resolvedTheme, setTheme, toggleTheme]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (context === null) {
    throw new Error("useTheme must be used within a ThemeProvider")
  }
  return context
}
