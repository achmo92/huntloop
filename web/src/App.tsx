import {
  NavLink,
  Outlet,
  RouterProvider,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom"
import { cn } from "@/lib/utils"
import Criteria from "@/pages/Criteria"
import Dashboard from "@/pages/Dashboard"
import Employers from "@/pages/Employers"
import Listings from "@/pages/Listings"
import Onboarding from "@/pages/Onboarding"
import Runs from "@/pages/Runs"
import Settings from "@/pages/Settings"

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/listings", label: "Listings" },
  { to: "/employers", label: "Employers" },
  { to: "/criteria", label: "Criteria" },
  { to: "/runs", label: "Runs" },
  { to: "/settings", label: "Settings" },
  { to: "/onboarding", label: "Get started" },
]

const linkBase =
  "rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
const linkIdle =
  "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
const linkActive =
  "bg-accent font-medium text-accent-foreground"

function NavLinks({ vertical }: { vertical: boolean }) {
  return (
    <nav
      aria-label="Primary"
      className={cn(
        "gap-1",
        vertical
          ? "flex flex-col"
          : "flex items-center gap-1 overflow-x-auto px-4 pb-3"
      )}
    >
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cn(linkBase, isActive ? linkActive : linkIdle)
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

function AppShell() {
  return (
    <div className="min-h-svh bg-background text-foreground">
      {/* Mobile: top bar with horizontally scrollable nav */}
      <header className="border-b md:hidden">
        <div className="flex items-center justify-between px-4 pt-3 pb-3">
          <span className="font-heading text-base font-semibold tracking-tight">
            HuntLoop
          </span>
        </div>
        <NavLinks vertical={false} />
      </header>

      <div className="md:grid md:grid-cols-[14rem_1fr]">
        {/* Desktop: sidebar — the second neutral layer */}
        <aside className="hidden md:flex md:flex-col md:border-r md:bg-muted/40">
          <div className="px-4 py-5">
            <span className="font-heading text-base font-semibold tracking-tight">
              HuntLoop
            </span>
          </div>
          <div className="px-3">
            <NavLinks vertical />
          </div>
        </aside>

        <main className="min-w-0 px-6 py-8 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "listings", element: <Listings /> },
      { path: "employers", element: <Employers /> },
      { path: "criteria", element: <Criteria /> },
      { path: "runs", element: <Runs /> },
      { path: "settings", element: <Settings /> },
      { path: "onboarding", element: <Onboarding /> },
    ],
  },
]

export const router = createBrowserRouter(routes)

export default function App() {
  return <RouterProvider router={router} />
}