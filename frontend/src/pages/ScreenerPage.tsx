import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Radar, SlidersHorizontal, ExternalLink } from "lucide-react";
import { apiUrl, apiFetch } from "../config";
import { useNavigationState } from "../hooks/useNavigationState";

// ─── Types (mirror backend ScreenerOpportunity / ScreenerConfig) ─────────────

interface ScreenerOpportunity {
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread_bps: number | null;
  net_spread_bps: number | null;
  l1_notional: number;
  volume_24h_quote: number;
  lifetime_sec: number;
  pct_time_above: number;
  spread_std: number | null;
  spread_zscore: number | null;
  book_update_rate_per_min: number | null;
  tick_age_ms: number;
  score: number;
  score_breakdown: Record<string, number>;
  observed_at: string;
}

interface ScreenerConfig {
  min_net_spread_bps: number;
  max_spread_bps: number;
  min_l1_notional_usdt: number;
  min_volume_24h_usdt: number;
  min_lifetime_sec: number;
  w_spread: number;
  w_liq: number;
  w_life: number;
  w_stab: number;
  w_vol: number;
  w_stale: number;
  w_zscore: number;
  adaptive_mode: boolean;
  spread_percentile: number;
  min_spread_zscore: number;
  target_opportunity_min: number;
  target_opportunity_max: number;
  use_spread_zscore: boolean;
}

interface StreamPayload {
  scanned_at: string;
  total_universe: number;
  opportunity_count: number;
  opportunities: ScreenerOpportunity[];
  spread_percentile?: number;
  percentile_cutoff?: number | null;
}

// ─── Formatters ──────────────────────────────────────────────────────────────

const fmtBps = (v: number | null) => (v == null ? "—" : `${v.toFixed(1)}`);
const fmtPct = (v: number) => `${v.toFixed(0)}%`;
const fmtSec = (v: number) => `${v.toFixed(0)}s`;
const fmtUsd = (v: number) => {
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
};

// ─── Config field specs (for the tunable filter panel) ───────────────────────

// Only the numeric config fields (booleans are handled by dedicated toggles).
type NumKey =
  | "min_net_spread_bps"
  | "max_spread_bps"
  | "min_l1_notional_usdt"
  | "min_volume_24h_usdt"
  | "min_lifetime_sec"
  | "w_spread"
  | "w_liq"
  | "w_life"
  | "w_stab"
  | "w_vol"
  | "w_stale"
  | "w_zscore"
  | "spread_percentile"
  | "min_spread_zscore"
  | "target_opportunity_min"
  | "target_opportunity_max";

interface FieldSpec {
  key: NumKey;
  label: string;
  step: number;
  hint?: string;
}

const GATE_FIELDS: FieldSpec[] = [
  { key: "min_net_spread_bps", label: "Min net spread", step: 0.5, hint: "bps" },
  { key: "max_spread_bps", label: "Max spread (sanity)", step: 5, hint: "bps" },
  { key: "min_l1_notional_usdt", label: "Min L1 notional", step: 50, hint: "USDT" },
  { key: "min_volume_24h_usdt", label: "Min 24h volume", step: 50000, hint: "USDT" },
  { key: "min_lifetime_sec", label: "Min lifetime", step: 1, hint: "sec" },
];

const WEIGHT_FIELDS: FieldSpec[] = [
  { key: "w_spread", label: "w spread", step: 0.1 },
  { key: "w_liq", label: "w liquidity", step: 0.1 },
  { key: "w_life", label: "w lifetime", step: 0.1 },
  { key: "w_stab", label: "w stability", step: 0.1 },
  { key: "w_vol", label: "w volatility", step: 0.1 },
  { key: "w_stale", label: "w staleness", step: 0.05 },
  { key: "w_zscore", label: "w zscore", step: 0.1 },
];

const ADAPTIVE_FIELDS: FieldSpec[] = [
  { key: "spread_percentile", label: "Percentile (95=топ 5%)", step: 1 },
  { key: "min_spread_zscore", label: "Min z-score", step: 0.5 },
  { key: "target_opportunity_min", label: "Target min", step: 1 },
  { key: "target_opportunity_max", label: "Target max", step: 1 },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export function ScreenerPage() {
  const [opps, setOpps] = useState<ScreenerOpportunity[]>([]);
  const [config, setConfig] = useState<ScreenerConfig | null>(null);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [totalUniverse, setTotalUniverse] = useState(0);
  const [percentile, setPercentile] = useState<number | null>(null);
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const patchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();
  const { setSymbol } = useNavigationState();

  // Initial fetch + SSE subscription.
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        const r = await fetch(apiUrl("/api/screener/opportunities?limit=50"));
        const data = await r.json();
        if (cancelled) return;
        if (data.ok) {
          setOpps(data.opportunities ?? []);
          setScannedAt(data.scanned_at ?? null);
          setTotalUniverse(data.total_universe ?? 0);
          if (data.config) setConfig(data.config);
        }
      } catch {
        /* SSE will fill in */
      }
    }
    bootstrap();

    const es = new EventSource(apiUrl("/api/screener/stream"));
    esRef.current = es;
    es.onopen = () => !cancelled && setConnected(true);
    es.onerror = () => {
      if (cancelled) return;
      setConnected(false);
      // EventSource auto-reconnects; nothing else to do.
    };
    es.onmessage = (ev) => {
      if (cancelled) return;
      try {
        const p: StreamPayload = JSON.parse(ev.data);
        setOpps(p.opportunities ?? []);
        setScannedAt(p.scanned_at ?? null);
        setTotalUniverse(p.total_universe ?? 0);
        setPercentile(p.spread_percentile ?? null);
        setCutoff(p.percentile_cutoff ?? null);
        setErr(null);
      } catch {
        /* ignore malformed */
      }
    };

    return () => {
      cancelled = true;
      es.close();
      esRef.current = null;
    };
  }, []);

  // Debounced PATCH when a filter field changes.
  const updateField = useCallback(
    (key: NumKey, value: number) => {
      setConfig((prev) => (prev ? { ...prev, [key]: value } : prev));
      if (patchTimerRef.current) clearTimeout(patchTimerRef.current);
      patchTimerRef.current = setTimeout(async () => {
        try {
          const r = await apiFetch("/api/screener/config", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [key]: value }),
          });
          const data = await r.json();
          if (data.ok && data.config) setConfig(data.config);
        } catch (e) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      }, 500);
    },
    [],
  );

  const updateBool = useCallback(
    (key: "adaptive_mode" | "use_spread_zscore", value: boolean) => {
      setConfig((prev) => (prev ? { ...prev, [key]: value } : prev));
      apiFetch("/api/screener/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      })
        .then((r) => r.json())
        .then((d) => {
          if (d.ok && d.config) setConfig(d.config);
        })
        .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
    },
    [],
  );

  const openSymbol = (symbol: string) => {
    setSymbol(symbol);
    navigate("/");
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-3">
          <Radar className="h-5 w-5 text-accent" />
          <div>
            <h1 className="text-base font-semibold text-ink">Spread Screener</h1>
            <p className="text-xs text-ink-muted">
              Монеты, где захват bid/ask-спреда выгоден прямо сейчас (MEXC spot,
              maker 0%)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs text-ink-muted">
          <span
            className={`flex items-center gap-1.5 ${
              connected ? "text-emerald-500" : "text-ink-muted"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                connected ? "bg-emerald-500" : "bg-ink-muted/50"
              }`}
            />
            {connected ? "live" : "подключение…"}
          </span>
          {scannedAt && (
            <span>
              скан: {new Date(scannedAt).toLocaleTimeString()}
            </span>
          )}
          <span>
            найдено: <span className="font-semibold text-ink">{opps.length}</span>{" "}
            / {totalUniverse}
          </span>
          <button
            onClick={() => setPanelOpen((v) => !v)}
            className="flex items-center gap-1 rounded-md px-2 py-1 hover:bg-accent/10 hover:text-accent"
          >
            <SlidersHorizontal className="h-4 w-4" />
            {panelOpen ? "Скрыть фильтры" : "Фильтры"}
          </button>
        </div>
      </div>

      {/* Filter panel */}
      {panelOpen && config && (
        <div className="shrink-0 border-b border-line bg-surface-elevated px-4 py-3">
          {/* Adaptive controls */}
          <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2">
            <label className="flex items-center gap-2 text-xs text-ink">
              <input
                type="checkbox"
                checked={config.adaptive_mode}
                onChange={(e) => updateBool("adaptive_mode", e.target.checked)}
                className="h-3.5 w-3.5 accent-accent"
              />
              <span className="font-medium">Адаптивный режим</span>
            </label>
            <label className="flex items-center gap-2 text-xs text-ink">
              <input
                type="checkbox"
                checked={config.use_spread_zscore}
                onChange={(e) => updateBool("use_spread_zscore", e.target.checked)}
                className="h-3.5 w-3.5 accent-accent"
              />
              <span className="font-medium">Z-score gate</span>
            </label>
            {config.adaptive_mode && (
              <span className="rounded-md bg-accent/10 px-2 py-1 text-[11px] text-accent">
                авто-калибровка: P{percentile ?? config.spread_percentile}
                {cutoff != null && ` → срез ${cutoff.toFixed(1)} bps`} (цель{" "}
                {config.target_opportunity_min}–{config.target_opportunity_max})
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3 lg:grid-cols-6">
            {[
              ...ADAPTIVE_FIELDS,
              ...GATE_FIELDS,
              ...WEIGHT_FIELDS,
            ].map((f) => (
              <label key={f.key} className="flex flex-col gap-1">
                <span className="text-[10px] font-medium uppercase tracking-wide text-ink-muted">
                  {f.label}
                  {f.hint ? ` (${f.hint})` : ""}
                </span>
                <input
                  type="number"
                  step={f.step}
                  value={config[f.key]}
                  onChange={(e) =>
                    updateField(f.key, parseFloat(e.target.value) || 0)
                  }
                  className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                />
              </label>
            ))}
          </div>
          {err && (
            <p className="mt-2 text-xs text-red-500">Ошибка обновления: {err}</p>
          )}
        </div>
      )}

      {/* Table / empty state */}
      <div className="min-h-0 flex-1 overflow-auto">
        {opps.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-ink-muted">
            <Radar className="h-10 w-10 opacity-30" />
            <p className="text-sm font-medium">
              Сейчас возможностей нет
            </p>
            <p className="max-w-md text-xs">
              Ни одна монета не прошла все фильтры (net-спред, ликвидность,
              объём, время удержания). Расширьте пороги в панели фильтров или
              подождите — скринер обновляется в реальном времени.
            </p>
          </div>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Символ</th>
                <th className="px-3 py-2 text-right">Net bps</th>
                <th className="px-3 py-2 text-right">L1 $</th>
                <th className="px-3 py-2 text-right">Lifetime</th>
                <th className="px-3 py-2 text-right">Vol 24h</th>
                <th className="px-3 py-2 text-right">% выше</th>
                <th className="px-3 py-2 text-right">σ bps</th>
                <th className="px-3 py-2 text-right">z</th>
                <th className="px-3 py-2 text-right">updt/мин</th>
                <th className="px-3 py-2 text-right">EV</th>
                <th className="px-3 py-2 text-right">Score</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {opps.map((o, i) => (
                <tr
                  key={o.symbol}
                  className="border-t border-line/60 transition-colors hover:bg-accent/5"
                >
                  <td className="px-3 py-2 text-ink-muted">{i + 1}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => openSymbol(o.symbol)}
                      className="font-mono font-medium text-ink hover:text-accent"
                    >
                      {o.symbol}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-right font-mono font-semibold text-emerald-500">
                    {fmtBps(o.net_spread_bps)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink">
                    {fmtUsd(o.l1_notional)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink">
                    {fmtSec(o.lifetime_sec)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {fmtUsd(o.volume_24h_quote)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {fmtPct(o.pct_time_above)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {o.spread_std == null ? "—" : o.spread_std.toFixed(1)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">
                    {o.spread_zscore == null ? "—" : o.spread_zscore.toFixed(2)}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-ink-muted"
                    title="bookTicker updates/min — real-time activity proxy (≥60 ≈ active)"
                  >
                    {o.book_update_rate_per_min == null
                      ? "—"
                      : o.book_update_rate_per_min.toFixed(0)}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-ink"
                    title="Realizable edge ≈ net_spread × activity_factor (from score_breakdown.ev)"
                  >
                    {o.score_breakdown?.ev == null
                      ? "—"
                      : o.score_breakdown.ev.toFixed(2)}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono font-semibold text-ink"
                    title={Object.entries(o.score_breakdown)
                      .map(([k, v]) => `${k}: ${v.toFixed(2)}`)
                      .join("\n")}
                  >
                    {o.score.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => openSymbol(o.symbol)}
                      className="text-ink-muted hover:text-accent"
                      title="Открыть в Spread Monitor"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
