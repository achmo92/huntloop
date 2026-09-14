/**
 * The theme runtime — pure helpers only, no React. Both the pre-paint script in
 * index.html and the React provider resolve the theme with the same rule, so
 * there is no flash and no second source of truth.
 */

export type Theme = "light" | "dark"

/** One key, shared by the pre-paint script and the provider. */
export const THEME_STORAGE_KEY = "huntloop-theme"

/**
 * The stored explicit choice, or `null` when the user has never chosen (or the
 * stored value is not a known theme). Storage can be denied in private mode, so
 * every access is guarded.
 */
export function readStoredTheme(): Theme | null {
  if (typeof localStorage === "undefined") return null
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY)
    return raw === "light" || raw === "dark" ? raw : null
  } catch {
    return null
  }
}

/** The OS preference; `false` when `matchMedia` is unavailable (SSR, old env). */
export function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false
  }
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
  } catch {
    return false
  }
}

/** An explicit choice wins; otherwise the system preference decides. */
export function resolveTheme(theme: Theme | null): Theme {
  if (theme) return theme
  return systemPrefersDark() ? "dark" : "light"
}

/** Apply the theme by toggling the `.dark` class on `<html>`. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return
  document.documentElement.classList.toggle("dark", theme === "dark")
}
