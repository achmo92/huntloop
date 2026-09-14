import { MoonIcon, SunIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme/ThemeProvider"

/**
 * The header's theme control. The accessible name states the *action*, not the
 * current state, so a screen-reader user is told what pressing it will do. The
 * two glyphs cross-fade in place — a small, material acknowledgment of the
 * whole page changing underfoot.
 */
export function ThemeToggle() {
  const { resolvedTheme, toggleTheme } = useTheme()
  const label =
    resolvedTheme === "dark"
      ? "Switch to light theme"
      : "Switch to dark theme"
  const isDark = resolvedTheme === "dark"

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      onClick={toggleTheme}
      className="relative text-muted-foreground hover:text-foreground"
    >
      <SunIcon
        aria-hidden="true"
        className={cn(
          "absolute transition-all duration-300 ease-out",
          isDark ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-0 opacity-0"
        )}
      />
      <MoonIcon
        aria-hidden="true"
        className={cn(
          "absolute transition-all duration-300 ease-out",
          isDark ? "rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
        )}
      />
    </Button>
  )
}
