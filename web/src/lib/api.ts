/**
 * The one HTTP module every page imports. Plain fetch only — no axios
 * (STACK.md guidance). All calls are same-origin under /api: the Vite dev
 * proxy forwards to the FastAPI backend in development, and the app serves
 * both halves from one origin in production — no CORS anywhere, ever.
 */

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body: unknown = await response.json()
      if (typeof body === "object" && body !== null && "detail" in body) {
        const candidate = (body as { detail?: unknown }).detail
        if (typeof candidate === "string") detail = candidate
      }
    } catch {
      // Non-JSON error body — statusText is the best we have.
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function api<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init)
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) })
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body) })
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) })
}