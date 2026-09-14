import { useCallback, useState, type ReactNode } from "react"
import { Outlet } from "react-router-dom"
import { cn } from "@/lib/utils"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { AppHeader } from "@/components/shell/AppHeader"
import { AppSidebar } from "@/components/shell/AppSidebar"

/** Wide-viewport rail state (D-24d). Absence means "open". */
const SIDEBAR_STORAGE_KEY = "huntloop-sidebar"

function readCollapsedPreference(): boolean {
  if (typeof localStorage === "undefined") return false
  try {
    return localStorage.getItem(SIDEBAR_STORAGE_KEY) === "collapsed"
  } catch {
    return false
  }
}

function writeCollapsedPreference(collapsed: boolean): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "collapsed" : "open")
  } catch {
    // Storage denied — the rail still folds for this session.
  }
}

interface AppShellProps {
  /**
   * A page-provided primary action. The shell accepts it now so a page can hand
   * one over later without any change to routes or behavior; today it is unused.
   */
  primaryActions?: ReactNode
}

/**
 * The app frame (GAP-17.1 / D-24d): a fixed sidebar for wide viewports that
 * folds to an icon rail and remembers the choice, a sticky header, and the
 * routed page inside `<main>`. Narrow viewports get the same nav in the vendored
 * `Sheet` drawer, opened from the header trigger. DOM order is
 * sidebar -> header -> main so focus order agrees with what the eye sees, and a
 * skip link leads straight to the content.
 */
export function AppShell({ primaryActions }: AppShellProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() =>
    readCollapsedPreference()
  )
  const [mobileOpen, setMobileOpen] = useState(false)

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current
      writeCollapsedPreference(next)
      return next
    })
  }, [])

  return (
    <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-lg focus:border focus:border-border focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-elevation-1 focus:outline-2 focus:outline-offset-2 focus:outline-ring"
      >
        Skip to content
      </a>

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 lg:flex",
          collapsed ? "w-[4.5rem]" : "w-64"
        )}
      >
        <AppSidebar
          collapsed={collapsed}
          onToggleCollapsed={toggleCollapsed}
        />
      </aside>

      <div
        className={cn(
          "flex min-h-svh flex-col transition-[padding] duration-200",
          collapsed ? "lg:pl-[4.5rem]" : "lg:pl-64"
        )}
      >
        <AppHeader primaryActions={primaryActions} />
        <main
          id="main"
          tabIndex={-1}
          className="min-w-0 flex-1 px-6 py-8 focus:outline-none md:px-8"
        >
          <Outlet />
        </main>
      </div>

      <SheetContent
        side="left"
        className="w-72 max-w-[85vw] bg-sidebar p-0 text-sidebar-foreground"
      >
        <SheetHeader className="sr-only">
          <SheetTitle>Navigation</SheetTitle>
        </SheetHeader>
        <AppSidebar onNavigate={() => setMobileOpen(false)} />
      </SheetContent>
    </Sheet>
  )
}
