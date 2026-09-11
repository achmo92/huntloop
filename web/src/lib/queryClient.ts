import { QueryClient } from "@tanstack/react-query"

/**
 * Factory so tests and the app each get a fresh cache. The 15s staleTime
 * suits a daily-monitoring surface: run/listing status pages poll via
 * react-query invalidation and refetch windows, not raw intervals.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
      },
    },
  })
}