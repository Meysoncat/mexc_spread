import { ExternalLink } from "lucide-react";
import { apiUrl, apiFetch } from "../config";

let adminToken = "";
try {
  adminToken = localStorage.getItem("mexc-admin-token") || "";
} catch {
  // ignore
}

async function _fetchAdminToken() {
  if (adminToken) return adminToken;
  try {
    const res = await fetch(apiUrl("/api/admin-token"));
    const data = await res.json();
    if (data.ok && data.token) {
      adminToken = data.token;
      localStorage.setItem("mexc-admin-token", adminToken);
      return adminToken;
    }
  } catch {
    // ignore
  }
  return "";
}

/**
 * Открыть тикер в MetaScalp.
 *
 * 1. Пробуем POST на backend для создания сигнального уровня —
 *    MetaScalp покажет popup с тикером. Получаем metascalp:// URL
 *    с connection ID.
 * 2. Параллельно/в случае успеха открываем custom protocol URL (metascalp://) —
 *    браузер покажет диалог "Открыть в приложении".
 */
export async function openInMetaScalp(ticker: string) {
  let metascalpUrl = `metascalp://open-ticker/${ticker}`;
  const token = await _fetchAdminToken();

  if (token) {
    try {
      const response = await apiFetch("/api/metascalp/open-ticker", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ticker }),
      });
      if (response.ok) {
        const data = await response.json();
        if (data.ok && data.metascalp_url) {
          metascalpUrl = data.metascalp_url;
        }
      }
    } catch {
      // ignore
    }
  }

  // Open the custom protocol handler
  try {
    window.location.assign(metascalpUrl);
  } catch {
    // ignore
  }
}

interface OpenInMetaScalpButtonProps {
  ticker: string;
  className?: string;
}

export function OpenInMetaScalpButton({
  ticker,
  className = "",
}: OpenInMetaScalpButtonProps) {
  return (
    <button
      type="button"
      className={`rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-emerald-500 dark:hover:text-emerald-400 ${className}`}
      title="Открыть в MetaScalp"
      aria-label={`Открыть ${ticker} в MetaScalp`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        openInMetaScalp(ticker);
      }}
    >
      <ExternalLink className="h-4 w-4" strokeWidth={2} />
    </button>
  );
}
