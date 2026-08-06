/**
 * Shared API client.
 *
 * One place that knows about the base URL, the bearer token and error shape.
 * Every feature module goes through this -- scattering `fetch` calls across
 * screens is how auth headers end up missing on exactly one endpoint.
 */

const BASE = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

let authToken: string | null = null;
export const setAuthToken = (t: string | null) => { authToken = t; };
export const getAuthToken = () => authToken;

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (e) {
    // Network-level failure. Distinguish from an HTTP error so the UI can say
    // "API unreachable" rather than a misleading "request failed".
    throw new ApiError(0, "API unreachable — is the backend running?", e);
  }

  if (res.status === 401) {
    setAuthToken(null);
    throw new ApiError(401, "Session expired — sign in again");
  }
  if (res.status === 403) {
    throw new ApiError(403, "You do not have permission for that action");
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.error ?? res.statusText;
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, String(detail), detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T,>(p: string) => request<T>(p);
export const post = <T,>(p: string, body: unknown) =>
  request<T>(p, { method: "POST", body: JSON.stringify(body) });

export const qs = (params: Record<string, string | number | boolean | undefined>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
