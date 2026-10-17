import { useState, useEffect, useCallback } from "react";
import { Radio } from "lucide-react";
import { apiUrl } from "../config";

const POLL_INTERVAL_SEC = 15;

interface FeedHealth {
  running: boolean;
  symbols: number;
  last_message_age_sec: number | null;
  live: boolean;
}

type FeedsHealth = Record<string, FeedHealth>;

/** Статус WS-фидов бирж из /api/health: сколько живых, какие отвалились. */
export function FeedsStatusWidget() {
  const [feeds, setFeeds] = useState<FeedsHealth | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/health"));
      if (!r.ok) return;
      const data = await r.json();
      setFeeds((data?.ws_feeds ?? null) as FeedsHealth | null);
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  if (!feeds) return null;

  // Фиды стартуют лениво при первом снапшоте биржи — показываем только запущенные
  const started = Object.entries(feeds).filter(([, f]) => f.running);
  if (started.length === 0) return null;

  const liveCount = started.filter(([, f]) => f.live).length;
  const allLive = liveCount === started.length;
  const noneLive = liveCount === 0;
  const color = allLive
    ? "text-emerald-500"
    : noneLive
      ? "text-rose-500"
      : "text-amber-500";

  const title = started
    .map(([name, f]) => {
      const age =
        f.last_message_age_sec == null ? "—" : `${f.last_message_age_sec} с`;
      return `${name}: ${f.live ? "live" : "stale"}, ${f.symbols} символов, последнее сообщение ${age}`;
    })
    .join("\n");

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-xs"
      title={`WS-фиды бирж:\n${title}`}
    >
      <Radio className={`h-3.5 w-3.5 ${color}`} />
      <span className="font-medium text-ink-muted">WS</span>
      <span className={`font-mono font-semibold tabular-nums ${color}`}>
        {liveCount}/{started.length}
      </span>
    </div>
  );
}
