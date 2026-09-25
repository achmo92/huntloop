import { lazy, Suspense, type ReactNode } from "react"
import {
  Navigate,
  RouterProvider,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { AppShell } from "@/components/shell/AppShell"

const Criteria = lazy(() => import("@/pages/Criteria"))
const Dashboard = lazy(() => import("@/pages/Dashboard"))
const Employers = lazy(() => import("@/pages/Employers"))
const Listings = lazy(() => import("@/pages/Listings"))
const ProposalsPage = lazy(() => import("@/pages/proposals/ProposalsPage"))
const Runs = lazy(() => import("@/pages/Runs"))
const Settings = lazy(() => import("@/pages/Settings"))

function PageFallback() {
  return (
    <p role="status" className="text-sm text-muted-foreground">
      Loading page…
    </p>
  )
}

function loadPage(page: ReactNode) {
  return <Suspense fallback={<PageFallback />}>{page}</Suspense>
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: loadPage(<Dashboard />) },
      { path: "listings", element: loadPage(<Listings />) },
      { path: "employers", element: loadPage(<Employers />) },
      { path: "criteria", element: loadPage(<Criteria />) },
      { path: "proposals", element: loadPage(<ProposalsPage />) },
      { path: "runs", element: loadPage(<Runs />) },
      { path: "settings", element: loadPage(<Settings />) },
      // GAP-3: Get Started was merged into Criteria. Old links land here.
      { path: "onboarding", element: <Navigate to="/criteria" replace /> },
    ],
  },
]

export const router = createBrowserRouter(routes)

export default function App() {
  return (
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  )
}
