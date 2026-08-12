import { useEffect, useState } from "react";
import { History, RefreshCw } from "lucide-react";

interface HistEvent {
  id: number;
  symbol: string;
  found_at: string;
  exited_at: string | null;
  duration_sec: number | null;
  net_spread_bps: number | null;
  spread_bps: number | null;
  l1_notional: number | null;
  volume_24h_quote: number | null;
  lifetime_sec: number | null;
  spread_zscore: number | null;
  book_update_rate_per_min: number | null;
  score: number | null;
  score_breakdown: Record<string, number> | null;
  open: boolean;
}

const fmtTime = (iso: string) => {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};
const fmtDur = (s: number | null) => {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = s / 60;
  if (m < 60) return `${m.toFixed(1)}m`;
  return `${(m / 60).toFixed(1)}h`;
};
const fmtUsd = (v: number | null) => {
  if (v == null) return "—";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
};

export function ScreenerHistoryPage() {
  const [events, setEvents] = useState<HistEvent[]>([]);
  const [symbol, setSymbol] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "200" });
    if (symbol.trim()) params.set("symbol", symbol.trim());
    if (onlyOpen) params.set("only_open", "true");
    fetch(`/api/screener/history?${params}`)
      .then((r) => r.json())
      .then((d) => setEvents(d.events ?? []))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-3">
          <History className="h-5 w-5 text-accent" />
          <div>
            <h1 className="text-base font-semibold text-ink">
              История скринера
            </h1>
            <p className="text-xs text-ink-muted">
              Монеты, найденные скринером: когда обнаружена, как долго
              продержалась, и почему попала (EV / разбивка).
            </p>
          </div>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:border-accent hover:text-accent"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Обновить
        </button>
      </div>

      {/* Filters */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface-elevated px-4 py-2">
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Символ (напр. SOLUSDT)"
          className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
        />
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={onlyOpen}
            onChange={(e) => setOnlyOpen(e.target.checked)}
          />
          только активные
        </label>
        <button
          onClick={load}
          className="rounded-md bg-accent px-3 py-1 text-sm font-medium text-white hover:bg-accent/90"
        >
          Применить
        </button>
      </div>

      {/* Table */}
      <div className="min-h-0 flex-1 overflow-auto">
        {events.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-ink-muted">
            <History className="h-10 w-10 opacity-30" />
            <p className="text-sm font-medium">История пуста</p>
            <p className="max-w-md text-xs">
              События появляются, когда монета впервые попадает в шорт-лист
              скринера. Нужен включённый <code>history_enabled</code> в конфиге.
            </p>
          </div>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-3 py-2 text-left">Символ</th>
                <th className="px-3 py-2 text-left">Найдена</th>
                <th className="px-3 py-2 text-right">Длит.</th>
                <th className="px-3 py-2 text-right">Net bps</th>
                <th className="px-3 py-2 text-right">EV</th>
                <th className="px-3 py-2 text-right">L1 $</th>
                <th className="px-3 py-2 text-right">Life</th>
                <th className="px-3 py-2 text-right">z</th>
                <th className="px-3 py-2 text-right">updt/м</th>
                <th className="px-3 py-2 text-left">Причина</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-t border-line/60">
                  <td className="px-3 py-2 font-medium text-ink">
                    {e.symbol}
                    {e.open && (
                      <span className="ml-1.5 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600">
                        live
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-ink-muted">
                    {fmtTime(e.found_at)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {fmtDur(e.duration_sec ?? (e.open ? null : 0))}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink">
                    {e.net_spread_bps == null
                      ? "—"
                      : e.net_spread_bps.toFixed(1)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink">
                    {e.score_breakdown?.ev == null
                      ? "—"
                      : e.score_breakdown.ev.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {fmtUsd(e.l1_notional)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {e.lifetime_sec == null
                      ? "—"
                      : `${e.lifetime_sec.toFixed(0)}s`}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {e.spread_zscore == null ? "—" : e.spread_zscore.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {e.book_update_rate_per_min == null
                      ? "—"
                      : e.book_update_rate_per_min.toFixed(0)}
                  </td>
                  <td
                    className="px-3 py-2 text-[11px] text-ink-muted"
                    title={
                      e.score_breakdown
                        ? Object.entries(e.score_breakdown)
                            .map(([k, v]) => `${k}: ${v.toFixed(2)}`)
                            .join("\n")
                        : ""
                    }
                  >
                    {e.score_breakdown
                      ? Object.entries(e.score_breakdown)
                          .filter(([, v]) => Math.abs(v) > 0.01)
                          .sort(([, a], [, b]) => b - a)
                          .slice(0, 3)
                          .map(([k, v]) => `${k}:${v.toFixed(1)}`)
                          .join(" · ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {err && <p className="px-4 py-2 text-xs text-red-500">Ошибка: {err}</p>}
    </div>
  );
}
