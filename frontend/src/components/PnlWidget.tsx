import { useState, useEffect, useCallback } from "react";
import { DollarSign } from "lucide-react";

const POLL_INTERVAL_SEC = 10;

const ENGINE_LABELS: Record<string, string> = {
  capture: "Spread Capture",
  arb: "Арбитраж MEXC↔Aster",
  futures: "Futures Arb",
};

interface EnginePnl {
  engine_name: string;
  net_pnl: number;
}

interface PnlSummary {
  total_pnl: number;
  engines: EnginePnl[];
}

export function PnlWidget() {
  const [pnl, setPnl] = useState<PnlSummary | null>(null);

  const fetchPnl = useCallback(async () => {
    try {
      const results = await Promise.all([
        fetch("/api/capture/status").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/arbitrage/status").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/futures-arb/status").then((r) => r.ok ? r.json() : null).catch(() => null),
      ]);

      const engines: EnginePnl[] = [];
      let total = 0;

      if (results[0]?.stats) {
        const v = results[0].stats.net_pnl_usdt ?? 0;
        engines.push({ engine_name: "capture", net_pnl: v });
        total += v;
      }
      if (results[1]?.stats) {
        const v = results[1].stats.net_pnl_usdt ?? 0;
        engines.push({ engine_name: "arb", net_pnl: v });
        total += v;
      }
      if (results[2]?.stats) {
        const v = results[2].stats.total_net_pnl_usdt ?? 0;
        engines.push({ engine_name: "futures", net_pnl: v });
        total += v;
      }

      setPnl({ total_pnl: total, engines });
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    fetchPnl();
    const id = setInterval(fetchPnl, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchPnl]);

  if (!pnl || pnl.engines.length === 0) return null;

  const positive = pnl.total_pnl >= 0;

  return (
    <div
      className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-xs"
      title={`Суммарный PnL торговых движков (USDT):\n${pnl.engines
        .map((e) => `${ENGINE_LABELS[e.engine_name] ?? e.engine_name}: ${e.net_pnl.toFixed(2)}`)
        .join("\n")}`}
    >
      <DollarSign className="h-3.5 w-3.5 text-ink-muted" />
      <span className="font-medium text-ink-muted">PnL</span>
      <span className="font-mono font-semibold tabular-nums">
        <span className={positive ? "text-emerald-500" : "text-rose-500"}>
          {positive ? "+" : ""}{pnl.total_pnl.toFixed(2)}
        </span>
        <span className="ml-1 text-ink-muted/60">USDT</span>
      </span>
    </div>
  );
}
