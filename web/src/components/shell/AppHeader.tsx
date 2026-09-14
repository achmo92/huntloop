import type { ReactNode } from "react"
import { useLocation } from "react-router-dom"
import { MenuIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SheetTrigger } from "@/components/ui/sheet"
import { ThemeToggle } from "@/components/theme/ThemeToggle"
import { NAV_ITEMS } from "@/components/shell/nav"

interface AppHeaderProps {
  /** A page-provided primary action, rendered before the theme control. */
  primaryActions?: ReactNode
}

function useCurrentSection() {
  const { pathname } = useLocation()
  const match = NAV_ITEMS.find((item) =>
    item.end
      ? pathname === item.to
      : pathname === item.to || pathname.startsWith(`${item.to}/`)
  )
  return match ?? NAV_ITEMS[0]
}

/**
 * The sticky header (D-24c): it names the current section without becoming a
 * second heading — each page already owns its `<h1>` — and holds the theme
 * control plus an optional primary-action slot. The mobile trigger is the
 * vendored `Sheet` trigger, so its `aria-expanded` tracks the drawer exactly.
 * A solid 95% background keeps content legible as it scrolls underneath,
 * without resorting to glass.
 */
export function AppHeader({ primaryActions }: AppHeaderProps) {
  const section = useCurrentSection()

  return (
    <header className="sticky top-0 z-30 border-b border-border/70 bg-background/95">
      <div className="flex h-16 items-center gap-3 px-4 md:px-6">
        <SheetTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="lg:hidden"
              aria-label="Open navigation"
            />
          }
        >
          <MenuIcon aria-hidden="true" />
        </SheetTrigger>

        <div className="min-w-0 flex-1">
          <p className="truncate font-heading text-sm font-semibold tracking-[-0.01em] text-foreground">
            {section.label}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {section.context}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {primaryActions}
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
