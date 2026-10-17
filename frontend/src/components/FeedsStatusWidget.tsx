import { useState, useEffect, useCallback } from "react";
import { Radio } from "lucide-react";
import { v1Get } from "../setups/api";

const POLL_INTERVAL_SEC = 15;

interface VenueHealth {
  instruments: number;
  ws_age_sec: number | null;
  ws_symbols: number;
  rest_age_sec: number | null;
  live: boolean;
}

interface HubHealth {
  venues_live: string[];
  hub?: { venues: Record<string, VenueHealth> };
}

/** Сколько бирж сейчас отдают свежие котировки в MarketHub (/api/v1/health). */
export function FeedsStatusWidget() {
  const [health, setHealth] = useState<HubHealth | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      setHealth(await v1Get<HubHealth>("/health"));
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  const venues = health?.hub?.venues;
  if (!venues) return null;

  const names = Object.keys(venues);
  const liveCount = names.filter((n) => venues[n].live).length;
  const color =
    liveCount === names.length ? "text-emerald-500" : liveCount === 0 ? "text-rose-500" : "text-amber-500";

  const title = names
    .map((n) => {
      const v = venues[n];
      const src =
        v.ws_age_sec != null && v.ws_age_sec <= 10
          ? `WS ${v.ws_symbols} символов`
          : v.rest_age_sec != null
            ? `REST ${v.rest_age_sec} с назад`
            : "нет данных";
      return `${n}: ${v.live ? "live" : "нет"} · ${src}`;
    })
    .join("\n");

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-xs"
      title={`Биржи в MarketHub:\n${title}`}
    >
      <Radio className={`h-3.5 w-3.5 ${color}`} />
      <span className="font-medium text-ink-muted">Биржи</span>
      <span className={`font-mono font-semibold tabular-nums ${color}`}>
        {liveCount}/{names.length}
      </span>
    </div>
  );
}
