import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ColorType, createChart, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { BarChart3, RefreshCw } from "lucide-react";
import { apiUrl } from "./config";

type TimeRange = "1h" | "6h" | "24h" | "7d" | "30d";
const RANGES: { value: TimeRange; label: string; hours: number }[] = [
  { value: "1h", label: "1ч", hours: 1 },
  { value: "6h", label: "6ч", hours: 6 },
  { value: "24h", label: "24ч", hours: 24 },
  { value: "7d", label: "7д", hours: 168 },
  { value: "30d", label: "30д", hours: 720 },
];

export function CrossSpreadHistoryChart({ open, onClose, isDark }: { open: boolean; onClose: () => void; isDark: boolean }) {
  const chartRef = useRef<HTMLDivElement | null>(null);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [range, setRange] = useState<TimeRange>("24h");
  const [loading, setLoading] = useState(false);
  const [count, setCount] = useState(0);

  useLayoutEffect(() => {
    if (!open) return;
    const el = chartRef.current;
    if (!el) return;

    const bg = isDark ? "#1e293b" : "#ffffff";
    const fg = isDark ? "#e2e8f0" : "#0f172a";
    const grid = isDark ? "#334155" : "#e2e8f0";

    const chart = createChart(el, {
      layout: { background: { type: ColorType.Solid, color: bg }, textColor: fg },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid, autoScale: true },
      timeScale: { borderColor: grid, timeVisible: true },
      width: el.clientWidth,
      height: el.clientHeight,
    });

    const series = chart.addSeries(LineSeries, {
      color: "#8b5cf6",
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      title: "Basis (bps)",
    });

    const ro = new ResizeObserver(() => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth, height: chartRef.current.clientHeight });
    });
    ro.observe(el);

    // Fetch data
    setLoading(true);
    const hours = RANGES.find((r) => r.value === range)?.hours ?? 24;
    const since = new Date(Date.now() - hours * 3600_000).toISOString();
    const params = new URLSearchParams({ symbol, since, limit: "2000" });

    fetch(apiUrl(`/api/cross-spread/history?${params}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) return;
        const rows = data.rows ?? [];
        setCount(rows.length);
        const lineData = rows
          .filter((r: any) => r.basis_bps != null && r.observed_at)
          .map((r: any) => ({
            time: (new Date(r.observed_at).getTime() / 1000) as UTCTimestamp,
            value: r.basis_bps as number,
          }));
        if (lineData.length > 0) {
          series.setData(lineData);
          chart.timeScale().fitContent();
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));

    return () => { ro.disconnect(); chart.remove(); };
  }, [open, symbol, range, isDark]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[75] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-5xl flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-3">
            <BarChart3 className="h-5 w-5 text-violet-500" />
            <h2 className="text-lg font-semibold text-ink">История кросс-спреда MEXC ↔ AsterDEX</h2>
            {loading && <RefreshCw className="h-4 w-4 animate-spin text-ink-muted" />}
            <span className="text-xs text-ink-muted">{count} точек</span>
          </div>
          <button onClick={onClose} className="rounded-lg border border-line p-2 text-ink hover:bg-surface">✕</button>
        </div>
        <div className="flex items-center gap-3 border-b border-line px-5 py-2">
          <input type="text" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-32 rounded-lg border border-line bg-surface px-2 py-1 text-sm font-mono text-ink" placeholder="BTCUSDT" />
          {RANGES.map((r) => (
            <button key={r.value} onClick={() => setRange(r.value)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${range === r.value ? "bg-violet-500 text-white" : "bg-surface text-ink-muted hover:bg-surface-hover border border-line"}`}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="relative flex-1 p-2">
          <div ref={chartRef} className="h-[min(50vh,420px)] w-full min-h-[280px]" />
          {!loading && count === 0 && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
              <div className="pointer-events-auto max-w-md rounded-xl border border-line bg-surface-elevated/95 px-5 py-4 text-center text-sm text-ink-muted shadow-lg">
                <p className="font-medium text-ink">
                  Нет точек за выбранный период для {symbol || "…"}
                </p>
                <p className="mt-2 leading-relaxed">
                  История базиса копится автоматически, пока работает бэкенд и
                  сборщик кросс-спреда MEXC ↔ AsterDEX. Проверьте написание
                  символа (например BTCUSDT), выберите период подлиннее или
                  оставьте сервер работать — точки появятся через несколько минут.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
