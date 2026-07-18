/**
 * Обращение к backend (прокси Vite по умолчанию).
 * Для фронта без proxy: в frontend/.env задайте VITE_API_BASE_URL=http://127.0.0.1:8000
 * См. также config/external_apis.json — только для Python (MEXC и т.д.).
 */
const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
export const API_BASE_URL = (rawBase ?? "").replace(/\/$/, "");

export const ADMIN_TOKEN_STORAGE_KEY = "mexc-admin-token";

export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${p}`;
}

/**
 * Singleton promise that resolves once the admin token is available.
 *
 * Why this exists: App.tsx used to fire `GET /api/admin-token` as a detached
 * promise (no await), while protected pages (ArbitragePage, SpreadCapturePage,
 * LeadLagPage, ...) fired their own requests in the same tick — reading an
 * empty localStorage and getting 401. Centralizing the token fetch as an
 * awaitable promise lets every page guarantee the token is in place before
 * sending the first authenticated request.
 *
 * On first import we check localStorage: if a token is already cached (typical
 * for a returning user), we resolve immediately; otherwise we kick off the
 * network fetch and resolve when it lands (or null if it fails — pages should
 * treat a missing token as "auth will fail, surface the error to the user").
 */
function readCachedToken(): string | null {
  try {
    return localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistToken(token: string): void {
  try {
    localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token);
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export const adminTokenReady: Promise<string | null> = (() => {
  const cached = readCachedToken();
  if (cached) return Promise.resolve(cached);
  return fetch(apiUrl("/api/admin-token"))
    .then((r) => (r.ok ? r.json() : null))
    .then((data: { ok?: boolean; token?: string } | null) => {
      if (data?.ok && data.token) {
        persistToken(data.token);
        return data.token;
      }
      return null;
    })
    .catch(() => null);
})();

/**
 * Get the admin token synchronously (after adminTokenReady has resolved).
 * Returns null if the token isn't ready yet or wasn't issued.
 */
export function getAdminToken(): string | null {
  return readCachedToken();
}

/**
 * Build the auth headers for an admin request. Safe to call before the token
 * bootstrap has finished — returns an empty object in that case.
 */
export function adminAuthHeaders(): Record<string, string> {
  const token = getAdminToken();
  return token ? { "X-Admin-Token": token } : {};
}

/**
 * Drop-in replacement for `fetch(apiUrl(path), init)` that automatically:
 *   1. awaits `adminTokenReady` (kills the 401 race condition), and
 *   2. injects the `X-Admin-Token` header from localStorage.
 *
 * Caller-supplied headers win on conflict (so a page can override if needed).
 *
 * Usage:
 *   const r = await apiFetch("/api/arbitrage/status");
 *   const r = await apiFetch("/api/capture/start", { method: "POST" });
 */
export async function apiFetch(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  await adminTokenReady;
  const headers = new Headers(init.headers || {});
  const token = getAdminToken();
  if (token && !headers.has("X-Admin-Token")) {
    headers.set("X-Admin-Token", token);
  }
  return fetch(apiUrl(path), { ...init, headers });
}
