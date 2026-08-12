import type { Exchange } from "../types";

/** Биржи с отдельным spot WS-фидом (см. mexc_monitor/ws_bookticker._spot_feeds). */
export const SPOT_WS_EXCHANGES: ReadonlySet<Exchange> = new Set<Exchange>([
  "okx",
  "gateio",
  "htx",
]);

/** Статус REST-пробы (совпадает с backend/source_probes.py). */
export type RestStatus = "ok" | "rate_limited" | "geo_blocked" | "error";

export interface RestProbe {
  exchange: string;
  ok: boolean;
  status: RestStatus;
  status_code: number | null;
  elapsed_ms: number;
  error?: string;
  url: string | null;
}

export interface WsHealth {
  running: boolean;
  symbols: number;
  last_message_age_sec: number | null;
  live: boolean;
}

export type Recommended = "ws" | "rest" | "none";

export interface SourceRow {
  exchange: Exchange;
  rest: RestProbe;
  ws: WsHealth | null;
  /** Spot WS-фид (только мультирыночные okx/gateio/htx), иначе null. */
  ws_spot?: WsHealth | null;
  recommended: Recommended;
}

export interface DiagnosticsResponse {
  ok: boolean;
  generated_at: string;
  active_proxy: string | null;
  summary: {
    total: number;
    rest_reachable: number;
    ws_live: number;
    ws_spot_live: number;
  };
  sources: SourceRow[];
}

/** Визуальный вариант бейджа (маппится на цветовые токены в UI). */
export type BadgeTone = "ok" | "warn" | "bad" | "muted";

export interface Badge {
  tone: BadgeTone;
  label: string;
  detail?: string;
}

/** Человекочитаемый бейдж REST-статуса с латентностью. */
export function restBadge(rest: RestProbe): Badge {
  switch (rest.status) {
    case "ok":
      return { tone: "ok", label: "Доступен", detail: `${rest.elapsed_ms} мс` };
    case "geo_blocked":
      return {
        tone: "bad",
        label: "Геоблок",
        detail: rest.status_code ? `HTTP ${rest.status_code}` : undefined,
      };
    case "rate_limited":
      return { tone: "warn", label: "Rate limit", detail: "HTTP 429" };
    default:
      return {
        tone: "bad",
        label: "Ошибка",
        detail: rest.status_code ? `HTTP ${rest.status_code}` : "нет ответа",
      };
  }
}

/** Бейдж состояния WebSocket-фида. */
export function wsBadge(ws: WsHealth | null): Badge {
  if (!ws) return { tone: "muted", label: "Нет фида" };
  if (ws.live) {
    const age = ws.last_message_age_sec;
    return {
      tone: "ok",
      label: "Онлайн",
      detail:
        `${ws.symbols} симв.` +
        (age != null ? ` · ${age.toFixed(age < 10 ? 1 : 0)} с` : ""),
    };
  }
  if (ws.running)
    return { tone: "warn", label: "Устарел", detail: `${ws.symbols} симв.` };
  return { tone: "muted", label: "Остановлен" };
}

/** Бейдж рекомендуемого источника снимка. */
export function recommendedBadge(rec: Recommended): Badge {
  switch (rec) {
    case "ws":
      return { tone: "ok", label: "WebSocket" };
    case "rest":
      return { tone: "warn", label: "REST" };
    default:
      return { tone: "bad", label: "Недоступен" };
  }
}

/** Приоритет строки: чем меньше — тем «проблемнее» (поднимается наверх). */
function healthRank(row: SourceRow): number {
  if (row.recommended === "none") return 0; // недоступен вовсе
  if (row.recommended === "rest") return 1; // только REST (нет живого WS)
  return 2; // WebSocket онлайн — всё хорошо
}

/**
 * Сортировка источников по проблемности: недоступные наверху, здоровые внизу.
 * При равном здоровье — по возрастанию REST-латентности (медленные выше).
 * Не мутирует исходный массив.
 */
export function sortSources(sources: SourceRow[]): SourceRow[] {
  return [...sources].sort((a, b) => {
    const rank = healthRank(a) - healthRank(b);
    if (rank !== 0) return rank;
    const la = a.rest.status === "ok" ? a.rest.elapsed_ms : Infinity;
    const lb = b.rest.status === "ok" ? b.rest.elapsed_ms : Infinity;
    if (la !== lb) return lb - la; // медленнее → выше
    return a.exchange.localeCompare(b.exchange);
  });
}
