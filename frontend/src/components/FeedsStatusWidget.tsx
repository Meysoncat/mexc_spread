import { useState, useEffect, useCallback } from "react";
import { Radio, WifiOff } from "lucide-react";
import { apiUrl } from "../config";

const POLL_INTERVAL_SEC = 10;

interface FeedHealth {
  running: boolean;
  symbols: number;
  last_message_age_sec: number | null;
  live: boolean;
}

interface HealthResponse {
  status: string;
  ws_feeds: Record<string, FeedHealth>;
}

const FEED_LABELS: Record<string, string> = {
  mexc: "MEXC",
  gate: "Gate",
  bybit: "Bybit",
  bitget: "Bitget",
  kucoin: "KuCoin",
  okx: "OKX",
  binance: "Binance",
  aster: "AsterDEX",
  dydx: "dYdX",
};

function feedLabel(name: string): string {
  return FEED_LABELS[name] ?? name.toUpperCase();
}

/**
 * Индикатор здоровья WS-фидов бирж в шапке (итерация 0.Б, пункт 0.9).
 * Опрашивает /api/health и показывает, сколько потоков живы; детали — в тултипе.
 */
export function FeedsStatusWidget() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  const fetchHealth = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/health"));
      if (!r.ok) {
        setUnreachable(true);
        return;
      }
      const data: HealthResponse = await r.json();
      setHealth(data);
      setUnreachable(false);
    } catch {
      setUnreachable(true);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  if (unreachable) {
    return (
      <div
        className="flex items-center gap-2 rounded-md border border-red-700/50 bg-red-900/10 px-3 py-1.5 text-xs"
        title="Бэкенд недоступен: не отвечает /api/health. Проверьте, что сервер запущен."
      >
        <WifiOff className="h-3.5 w-3.5 text-red-500" />
        <span className="font-medium text-red-500">API офлайн</span>
      </div>
    );
  }

  if (!health) return null;

  const entries = Object.entries(health.ws_feeds ?? {});
  // Показываем только фиды, которые хоть раз запускались или имеют данные —
  // незапущенные (по требованию) потоки не считаем проблемой.
  const started = entries.filter(([, f]) => f.running || f.symbols > 0);
  const liveCount = started.filter(([, f]) => f.live).length;
  const total = started.length;

  const allLive = total > 0 && liveCount === total;
  const noneLive = total > 0 && liveCount === 0;

  const colorCls = total === 0
    ? "border-line text-ink-muted"
    : allLive
      ? "border-green-700/30 bg-green-900/10"
      : noneLive
        ? "border-red-700/50 bg-red-900/10"
        : "border-yellow-700/50 bg-yellow-900/10";

  const dotCls = total === 0
    ? "bg-ink-muted/50"
    : allLive
      ? "bg-emerald-500"
      : noneLive
        ? "bg-red-500"
        : "bg-amber-500";

  const tooltip =
    total === 0
      ? "WS-потоки бирж ещё не запускались: данные загружаются по REST при первом запросе."
      : entries
          .map(([name, f]) => {
            const state = f.live
              ? `live, ${f.symbols} симв., данные ${f.last_message_age_sec ?? "?"} с назад`
              : f.running
                ? `нет свежих данных${f.last_message_age_sec != null ? ` (${f.last_message_age_sec} с)` : ""}`
                : "не запущен";
            return `${feedLabel(name)}: ${state}`;
          })
          .join("\n");

  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs ${colorCls}`}
      title={tooltip}
    >
      <Radio className="h-3.5 w-3.5 text-ink-muted" />
      <span className="relative flex h-2 w-2">
        {total > 0 && !noneLive && (
          <span
            className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-40 ${dotCls}`}
          />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${dotCls}`} />
      </span>
      <span className="font-mono tabular-nums text-ink-muted">
        {total === 0 ? "WS —" : `WS ${liveCount}/${total}`}
      </span>
    </div>
  );
}
