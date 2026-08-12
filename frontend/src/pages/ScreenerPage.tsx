import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  Radar,
  SlidersHorizontal,
  ExternalLink,
  ChevronDown,
  ArrowUp,
  ArrowDown,
} from "lucide-react";
import { apiUrl, apiFetch } from "../config";
import { EmptyState } from "../components/ui/EmptyState";
import { useNavigationState } from "../hooks/useNavigationState";
import { baseFromSymbol } from "../lib/symbol";

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

// ─── Threshold presets (one-click gate calibration) ──────────────────────────

type PresetPatch = Partial<ScreenerConfig>;

interface Preset {
  id: string;
  label: string;
  hint: string;
  patch: PresetPatch;
}

const PRESETS: Preset[] = [
  {
    id: "conservative",
    label: "Консервативный",
    hint: "Только надёжные: широкий спред, глубокая ликвидность, долгое удержание",
    patch: {
      min_net_spread_bps: 8,
      min_l1_notional_usdt: 500,
      min_volume_24h_usdt: 500000,
      min_lifetime_sec: 5,
      adaptive_mode: false,
    },
  },
  {
    id: "balanced",
    label: "Сбалансированный",
    hint: "Разумный компромисс между качеством и количеством сигналов",
    patch: {
      min_net_spread_bps: 4,
      min_l1_notional_usdt: 200,
      min_volume_24h_usdt: 100000,
      min_lifetime_sec: 2,
      adaptive_mode: false,
    },
  },
  {
    id: "aggressive",
    label: "Агрессивный",
    hint: "Больше кандидатов, включая тонкую ликвидность и короткое удержание",
    patch: {
      min_net_spread_bps: 2,
      min_l1_notional_usdt: 50,
      min_volume_24h_usdt: 20000,
      min_lifetime_sec: 0,
      adaptive_mode: false,
    },
  },
];

// ─── Sortable columns ────────────────────────────────────────────────────────

type SortKey =
  | "net_spread_bps"
  | "l1_notional"
  | "lifetime_sec"
  | "volume_24h_quote"
  | "pct_time_above"
  | "spread_std"
  | "spread_zscore"
  | "book_update_rate_per_min"
  | "ev"
  | "score";

const sortValue = (o: ScreenerOpportunity, key: SortKey): number => {
  if (key === "ev") return o.score_breakdown?.ev ?? -Infinity;
  const v = o[key as keyof ScreenerOpportunity];
  return typeof v === "number" ? v : -Infinity;
};

// Color the net-spread cell by how far it clears the gate: below → muted,
// modest (< 2×) → amber, strong (≥ 2×) → emerald.
const netSpreadColor = (net: number | null, gate: number): string => {
  if (net == null) return "text-ink-muted";
  if (gate > 0 && net < gate) return "text-ink-muted";
  if (gate > 0 && net < gate * 2) return "text-amber-500";
  return "text-emerald-500";
};

// ─── Sortable table header ───────────────────────────────────────────────────

interface SortHeaderProps {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: "asc" | "desc";
  onSort: (key: SortKey) => void;
}

function SortHeader({ label, col, sortKey, sortDir, onSort }: SortHeaderProps) {
  const active = sortKey === col;
  return (
    <th className="px-3 py-2 text-right font-medium">
      <button
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-1 hover:text-ink ${
          active ? "text-ink" : ""
        }`}
      >
        {label}
        {active ? (
          sortDir === "desc" ? (
            <ArrowDown className="h-3 w-3" />
          ) : (
            <ArrowUp className="h-3 w-3" />
          )
        ) : (
          <span className="h-3 w-3" />
        )}
      </button>
    </th>
  );
}

// ─── Score breakdown (why a coin ranks where it does) ────────────────────────

// Human labels for each scorer term (mirrors backend score_candidate).
const SCORE_LABELS: Record<string, string> = {
  ev: "EV (спред × активность)",
  spread: "Спред (сырой)",
  liquidity: "Ликвидность L1",
  lifetime: "Время жизни",
  stability: "Стабильность (% выше)",
  volatility: "Волатильность σ",
  staleness: "Устаревание тика",
  zscore: "Z-score",
  volume24h: "Объём 24ч",
};

// Order terms by absolute contribution so the biggest drivers read first.
function ScoreBreakdown({ breakdown }: { breakdown: Record<string, number> }) {
  const entries = Object.entries(breakdown).filter(([, v]) => Math.abs(v) > 1e-6);
  if (entries.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-ink-muted">
        Нет данных для разбивки score.
      </p>
    );
  }
  entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const max = Math.max(...entries.map(([, v]) => Math.abs(v)));

  return (
    <div className="flex flex-col gap-1.5 px-3 py-3">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Из чего складывается score
      </p>
      {entries.map(([key, val]) => {
        const positive = val >= 0;
        const width = max > 0 ? (Math.abs(val) / max) * 100 : 0;
        return (
          <div key={key} className="flex items-center gap-2 text-xs">
            <span className="w-44 shrink-0 truncate text-ink-muted">
              {SCORE_LABELS[key] ?? key}
            </span>
            <div className="relative h-3 flex-1 rounded-sm bg-surface">
              <div
                className={`absolute top-0 h-3 rounded-sm ${
                  positive ? "bg-emerald-500/70" : "bg-red-500/70"
                }`}
                style={{ width: `${width}%` }}
              />
            </div>
            <span
              className={`w-14 shrink-0 text-right font-mono ${
                positive ? "text-emerald-500" : "text-red-500"
              }`}
            >
              {positive ? "+" : ""}
              {val.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

export function ScreenerPage() {
  const [opps, setOpps] = useState<ScreenerOpportunity[]>([]);
  const [config, setConfig] = useState<ScreenerConfig | null>(null);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [totalUniverse, setTotalUniverse] = useState(0);
  const [percentile, setPercentile] = useState<number | null>(null);
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [streamErrored, setStreamErrored] = useState(false);
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const [panelOpen, setPanelOpen] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [showMetrics, setShowMetrics] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expanded, setExpanded] = useState<string | null>(null);
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
          setHasLoaded(true);
        }
      } catch {
        /* SSE will fill in */
      }
    }
    bootstrap();

    const es = new EventSource(apiUrl("/api/screener/stream"));
    esRef.current = es;
    es.onopen = () => {
      if (cancelled) return;
      setConnected(true);
      setStreamErrored(false);
    };
    es.onerror = () => {
      if (cancelled) return;
      setConnected(false);
      setStreamErrored(true);
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
        setHasLoaded(true);
        setStreamErrored(false);
      } catch {
        /* ignore malformed */
      }
    };

    return () => {
      cancelled = true;
      es.close();
      esRef.current = null;
    };
  }, [reconnectNonce]);

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

  // Apply a whole preset in a single PATCH.
  const applyPreset = useCallback((patch: PresetPatch) => {
    setConfig((prev) => (prev ? { ...prev, ...patch } : prev));
    apiFetch("/api/screener/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.ok && d.config) setConfig(d.config);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  // Which preset (if any) matches the current gate config exactly.
  const activePreset = useMemo(() => {
    if (!config) return null;
    return (
      PRESETS.find((p) =>
        Object.entries(p.patch).every(
          ([k, v]) => config[k as keyof ScreenerConfig] === v,
        ),
      )?.id ?? null
    );
  }, [config]);

  const toggleSort = useCallback((key: SortKey) => {
    setSortKey((prevKey) => {
      if (prevKey === key) {
        setSortDir((d) => (d === "desc" ? "asc" : "desc"));
        return key;
      }
      setSortDir("desc");
      return key;
    });
  }, []);

  const sortedOpps = useMemo(() => {
    const arr = [...opps];
    arr.sort((a, b) => {
      const diff = sortValue(a, sortKey) - sortValue(b, sortKey);
      return sortDir === "desc" ? -diff : diff;
    });
    return arr;
  }, [opps, sortKey, sortDir]);

  // Symbol name → coin hub (decision context without leaving the screener flow).
  const openHub = (symbol: string) => {
    const base = baseFromSymbol(symbol) ?? symbol.replace(/[_\-/]/g, "");
    navigate(`/coin/${base}`);
  };

  // External-link icon → the classic Spread Monitor view.
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
              connected
                ? "text-emerald-500"
                : streamErrored
                  ? "text-red-500"
                  : "text-ink-muted"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                connected
                  ? "bg-emerald-500"
                  : streamErrored
                    ? "bg-red-500"
                    : "bg-ink-muted/50"
              }`}
            />
            {connected ? "live" : streamErrored ? "нет связи" : "подключение…"}
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
            onClick={() => setShowMetrics((v) => !v)}
            className={`flex items-center gap-1 rounded-md px-2 py-1 hover:bg-accent/10 hover:text-accent ${
              showMetrics ? "text-accent" : ""
            }`}
            title="Показать аналитические колонки (σ, z-score, активность, EV)"
          >
            {showMetrics ? "Скрыть метрики" : "Метрики"}
          </button>
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
          {/* Presets */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
              Пресет
            </span>
            {PRESETS.map((p) => {
              const active = activePreset === p.id;
              return (
                <button
                  key={p.id}
                  onClick={() => applyPreset(p.patch)}
                  title={p.hint}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    active
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-line text-ink-muted hover:border-accent/50 hover:text-ink"
                  }`}
                >
                  {p.label}
                </button>
              );
            })}
            {config.adaptive_mode && (
              <span className="ml-auto rounded-md bg-accent/10 px-2 py-1 text-[11px] text-accent">
                авто-калибровка: P{percentile ?? config.spread_percentile}
                {cutoff != null && ` → срез ${cutoff.toFixed(1)} bps`} (цель{" "}
                {config.target_opportunity_min}–{config.target_opportunity_max})
              </span>
            )}
          </div>

          {/* Gate thresholds — the everyday controls */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
            {GATE_FIELDS.map((f) => (
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

          {/* Advanced — scoring weights + adaptive tuning, collapsed by default */}
          <button
            onClick={() => setAdvancedOpen((v) => !v)}
            className="mt-3 flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-ink"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                advancedOpen ? "rotate-180" : ""
              }`}
            />
            Продвинутое: веса ранжирования и адаптивная калибровка
          </button>

          {advancedOpen && (
            <div className="mt-3 border-t border-line/60 pt-3">
              <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2">
                <label className="flex items-center gap-2 text-xs text-ink">
                  <input
                    type="checkbox"
                    checked={config.adaptive_mode}
                    onChange={(e) =>
                      updateBool("adaptive_mode", e.target.checked)
                    }
                    className="h-3.5 w-3.5 accent-accent"
                  />
                  <span className="font-medium">Адаптивный режим</span>
                </label>
                <label className="flex items-center gap-2 text-xs text-ink">
                  <input
                    type="checkbox"
                    checked={config.use_spread_zscore}
                    onChange={(e) =>
                      updateBool("use_spread_zscore", e.target.checked)
                    }
                    className="h-3.5 w-3.5 accent-accent"
                  />
                  <span className="font-medium">Z-score gate</span>
                </label>
              </div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
                {[...ADAPTIVE_FIELDS, ...WEIGHT_FIELDS].map((f) => (
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
            </div>
          )}

          {err && (
            <p className="mt-2 text-xs text-red-500">Ошибка обновления: {err}</p>
          )}
        </div>
      )}

      {/* Table / empty state */}
      <div className="min-h-0 flex-1 overflow-auto">
        {opps.length === 0 && !hasLoaded && !streamErrored ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              variant="loading"
              icon={Radar}
              title="Подключение к скринеру…"
              description="Устанавливаем поток данных и сканируем вселенную символов."
            />
          </div>
        ) : opps.length === 0 && streamErrored && !hasLoaded ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              variant="error"
              title="Нет соединения со скринером"
              description="Поток данных недоступен. Проверьте, что бэкенд запущен и биржа достижима из вашей сети, затем повторите."
              action={
                <button
                  onClick={() => {
                    setStreamErrored(false);
                    setReconnectNonce((n) => n + 1);
                  }}
                  className="rounded-md border border-accent bg-accent/10 px-4 py-1.5 text-sm font-medium text-accent transition hover:bg-accent/20"
                >
                  Повторить
                </button>
              }
            />
          </div>
        ) : opps.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={Radar}
              title="Сейчас возможностей нет"
              description="Ни одна монета не прошла все фильтры (net-спред, ликвидность, объём, время удержания). Смягчите пороги или подождите — скринер обновляется в реальном времени."
              action={
                <button
                  onClick={() => applyPreset(PRESETS[2].patch)}
                  className="rounded-md border border-line px-4 py-1.5 text-sm font-medium text-ink-muted transition hover:border-accent/50 hover:text-ink"
                >
                  Смягчить пороги (Агрессивный)
                </button>
              }
            />
          </div>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-3 py-2 text-left font-medium">#</th>
                <th className="px-3 py-2 text-left font-medium">Символ</th>
                <SortHeader
                  label="Net bps"
                  col="net_spread_bps"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                <SortHeader
                  label="L1 $"
                  col="l1_notional"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                <SortHeader
                  label="Lifetime"
                  col="lifetime_sec"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                <SortHeader
                  label="Vol 24h"
                  col="volume_24h_quote"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                <SortHeader
                  label="% выше"
                  col="pct_time_above"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                {showMetrics && (
                  <>
                    <SortHeader
                      label="σ bps"
                      col="spread_std"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                    />
                    <SortHeader
                      label="z"
                      col="spread_zscore"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                    />
                    <SortHeader
                      label="updt/мин"
                      col="book_update_rate_per_min"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                    />
                    <SortHeader
                      label="EV"
                      col="ev"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                    />
                  </>
                )}
                <SortHeader
                  label="Score"
                  col="score"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={toggleSort}
                />
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {sortedOpps.map((o, i) => (
                <Fragment key={o.symbol}>
                <tr className="border-t border-line/60 transition-colors hover:bg-accent/5">
                  <td className="px-3 py-2 text-ink-muted">{i + 1}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => openHub(o.symbol)}
                      className="font-mono font-medium text-ink hover:text-accent"
                      title="Открыть страницу монеты"
                    >
                      {o.symbol}
                    </button>
                  </td>
                  <td
                    className={`px-3 py-2 text-right font-mono font-semibold ${netSpreadColor(
                      o.net_spread_bps,
                      config?.min_net_spread_bps ?? 0,
                    )}`}
                  >
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
                  {showMetrics && (
                    <>
                      <td className="px-3 py-2 text-right font-mono text-ink-muted">
                        {o.spread_std == null ? "—" : o.spread_std.toFixed(1)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-ink-muted">
                        {o.spread_zscore == null
                          ? "—"
                          : o.spread_zscore.toFixed(2)}
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
                    </>
                  )}
                  <td className="px-3 py-2 text-right font-mono font-semibold text-ink">
                    <button
                      onClick={() =>
                        setExpanded((cur) =>
                          cur === o.symbol ? null : o.symbol,
                        )
                      }
                      className="inline-flex items-center gap-1 hover:text-accent"
                      title="Показать, из чего складывается score"
                      aria-expanded={expanded === o.symbol}
                    >
                      {o.score.toFixed(2)}
                      <ChevronDown
                        className={`h-3.5 w-3.5 transition-transform ${
                          expanded === o.symbol ? "rotate-180" : ""
                        }`}
                      />
                    </button>
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
                {expanded === o.symbol && (
                  <tr className="border-t border-line/40 bg-surface-elevated/40">
                    <td colSpan={showMetrics ? 13 : 9} className="p-0">
                      <ScoreBreakdown breakdown={o.score_breakdown} />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
