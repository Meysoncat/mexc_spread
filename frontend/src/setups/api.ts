import { apiUrl } from "../config";

const API_KEY_STORAGE = "screener-api-key";

/** URL API v1 с ключом доступа, если он сохранён (SCREENER_API_KEY на бэкенде). */
export function v1Url(path: string, params?: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  try {
    const key = localStorage.getItem(API_KEY_STORAGE);
    if (key) qs.set("api_key", key);
  } catch {
    /* localStorage недоступен */
  }
  const q = qs.toString();
  return apiUrl(`/api/v1${path}${q ? `?${q}` : ""}`);
}

export async function v1Get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const r = await fetch(v1Url(path, params));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}
