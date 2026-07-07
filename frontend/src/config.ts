/**
 * Обращение к backend (прокси Vite по умолчанию).
 * Для фронта без proxy: в frontend/.env задайте VITE_API_BASE_URL=http://127.0.0.1:8000
 * См. также config/external_apis.json — только для Python (MEXC и т.д.).
 */
const rawBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
export const API_BASE_URL = (rawBase ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${p}`;
}
