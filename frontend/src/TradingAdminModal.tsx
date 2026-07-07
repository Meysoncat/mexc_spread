import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, Power, RefreshCw, Save, Shield, Square, X, Zap } from "lucide-react";
import { apiUrl } from "./config";
import type { TradingEventsResponse, TradingStatusResponse } from "./types";

const ADMIN_TOKEN_STORAGE_KEY = "mexc-admin-token";

// ─── Task 8.1: Exchange and Market types ────────────────────────────────────────
type SupportedExchange = "mexc" | "binance" | "bybit" | "okx" | "gateio" | "htx" | "bitget";
type MarketType = "spot" | "futures";
type OrderType = "LIMIT" | "MARKET";
type OrderSide = "BUY" | "SELL";

interface ExchangeAvailability {
  exchange: SupportedExchange;
  available: boolean;
  paper_only: boolean;
  markets: MarketType[];
  spot_base_url: string;
  futures_base_url: string;
  order_types: OrderType[];
}

const EXCHANGE_LABELS: Record<SupportedExchange, string> = {
  mexc: "MEXC",
  binance: "Binance",
  bybit: "Bybit",
  okx: "OKX",
  gateio: "Gate.io",
  htx: "HTX",
  bitget: "Bitget",
};

// ─── Task 8.2: Extended RuntimePatch with order_type and order_side ─────────────
type RuntimePatch = {
  enabled: boolean;
  mode: "paper" | "live";
  symbol: string;
  min_net_spread_bps: number;
  order_quote_notional: number;
  limit_price_offset_bps: number;
  loop_interval_sec: number;
  max_orders_per_day: number;
  max_open_orders: number;
  max_consecutive_errors: number;
  kill_switch: boolean;
  recv_window_ms: number;
  events_log_path: string;
  order_type: OrderType;
  order_side: OrderSide;
};

const EMPTY_PATCH: RuntimePatch = {
  enabled: false,
  mode: "paper",
  symbol: "BTCUSDT",
  min_net_spread_bps: -2,
  order_quote_notional: 25,
  limit_price_offset_bps: 0,
  loop_interval_sec: 3,
  max_orders_per_day: 20,
  max_open_orders: 3,
  max_consecutive_errors: 5,
  kill_switch: true,
  recv_window_ms: 5000,
  events_log_path: "data/trading_events.jsonl",
  order_type: "LIMIT",
  order_side: "BUY",
};

function fmtTs(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString("ru-RU");
}

interface TradingAdminModalProps {
  open?: boolean;
  onClose?: () => void;
  /** Render as full-page content without modal overlay */
  pageMode?: boolean;
}

export function TradingAdminModal({ open = true, onClose, pageMode = false }: TradingAdminModalProps) {
  const [token, setToken] = useState("");
  const [status, setStatus] = useState<TradingStatusResponse | null>(null);
  const [events, setEvents] = useState<Record<string, unknown>[]>([]);
  const [form, setForm] = useState<RuntimePatch>(EMPTY_PATCH);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  // ─── Task 8.1: Exchange and market selection state ──────────────────────────
  const [selectedExchange, setSelectedExchange] = useState<SupportedExchange>("mexc");
  const [selectedMarket, setSelectedMarket] = useState<MarketType>("spot");
  const [exchanges, setExchanges] = useState<ExchangeAvailability[]>([]);

  const headers = useMemo(() => {
    const h: HeadersInit = { "Content-Type": "application/json" };
    if (token.trim()) h["X-Admin-Token"] = token.trim();
    return h;
  }, [token]);

  const readJson = useCallback(async <T,>(r: Response): Promise<T> => {
    const text = await r.text();
    let payload: unknown = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      /* ignore */
    }
    if (!r.ok) {
      const msg =
        (payload as { detail?: string })?.detail ??
        text ??
        `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return payload as T;
  }, []);

  // ─── Task 8.3: Build query params for all API calls ──────────────────────────
  const engineParams = useMemo(() => {
    const p = new URLSearchParams();
    p.set("exchange", selectedExchange);
    p.set("market", selectedMarket);
    return p.toString();
  }, [selectedExchange, selectedMarket]);

  const loadStatus = useCallback(async () => {
    const r = await fetch(apiUrl(`/api/trading/status?${engineParams}`), { headers });
    const data = await readJson<TradingStatusResponse>(r);
    setStatus(data);
    setForm({
      enabled: Boolean(data.settings.enabled),
      mode: data.settings.mode,
      symbol: data.settings.symbol || "BTCUSDT",
      min_net_spread_bps: Number(data.settings.min_net_spread_bps ?? -2),
      order_quote_notional: Number(data.settings.order_quote_notional ?? 25),
      limit_price_offset_bps: Number(data.settings.limit_price_offset_bps ?? 0),
      loop_interval_sec: Number(data.settings.loop_interval_sec ?? 3),
      max_orders_per_day: Number(data.settings.max_orders_per_day ?? 20),
      max_open_orders: Number(data.settings.max_open_orders ?? 3),
      max_consecutive_errors: Number(data.settings.max_consecutive_errors ?? 5),
      kill_switch: Boolean(data.state.kill_switch),
      recv_window_ms: Number(data.settings.recv_window_ms ?? 5000),
      events_log_path: data.settings.events_log_path || "data/trading_events.jsonl",
      order_type: ((data.settings as Record<string, unknown>).order_type as OrderType) || "LIMIT",
      order_side: ((data.settings as Record<string, unknown>).order_side as OrderSide) || "BUY",
    });
  }, [engineParams, headers, readJson]);

  const loadEvents = useCallback(async () => {
    const r = await fetch(apiUrl(`/api/trading/events?${engineParams}&limit=120`), { headers });
    const data = await readJson<TradingEventsResponse>(r);
    setEvents(Array.isArray(data.rows) ? data.rows : []);
  }, [engineParams, headers, readJson]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      await Promise.all([loadStatus(), loadEvents()]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [loadStatus, loadEvents]);

  const runAction = useCallback(
    async (path: string, method: "POST" | "PATCH", body?: unknown, ok?: string) => {
      setActionBusy(true);
      setErr(null);
      setOkMsg(null);
      try {
        // Task 8.3: Append exchange/market params to all action calls
        const separator = path.includes("?") ? "&" : "?";
        const fullPath = `${path}${separator}${engineParams}`;
        const r = await fetch(apiUrl(fullPath), {
          method,
          headers,
          body: body == null ? undefined : JSON.stringify(body),
        });
        await readJson<unknown>(r);
        await Promise.all([loadStatus(), loadEvents()]);
        if (ok) setOkMsg(ok);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setActionBusy(false);
      }
    },
    [engineParams, headers, loadEvents, loadStatus, readJson],
  );

  // ─── Task 8.1: Fetch exchange list on mount ─────────────────────────────────
  const loadExchanges = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/trading/exchanges"), { headers });
      const data = await readJson<{ ok: boolean; exchanges: ExchangeAvailability[] }>(r);
      if (Array.isArray(data.exchanges)) {
        setExchanges(data.exchanges);
      }
    } catch {
      // Non-critical: exchange list may not be available yet
    }
  }, [headers, readJson]);

  useEffect(() => {
    if (!open) return;
    try {
      setToken(window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) ?? "");
    } catch {
      setToken("");
    }
  }, [open]);

  // Task 8.1: Load exchanges on mount
  useEffect(() => {
    if (!open) return;
    void loadExchanges();
  }, [open, loadExchanges]);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => void refreshAll(), 50);
    return () => window.clearTimeout(t);
  }, [open, refreshAll]);

  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => void refreshAll(), 4000);
    return () => window.clearInterval(t);
  }, [open, refreshAll]);

  useEffect(() => {
    if (!open || pageMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, pageMode]);

  if (!open && !pageMode) return null;

  const content = (
    <>
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2
          id="trading-admin-title"
          className="flex items-center gap-2 text-lg font-semibold text-ink"
        >
          <Shield className="h-5 w-5 text-accent" />
          Trading Admin
        </h2>
        {!pageMode && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line p-2 text-ink transition hover:bg-surface"
            aria-label="Закрыть"
          >
            <X className="h-5 w-5" />
          </button>
        )}
      </div>

        {/* ─── Task 8.1: Exchange and Market selectors ──────────────────────────── */}
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2">
          {/* Exchange selector */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">Биржа:</span>
            <div className="flex flex-wrap gap-1">
              {(exchanges.length > 0
                ? exchanges
                : ([{ exchange: "mexc", available: false, paper_only: true }] as ExchangeAvailability[])
              ).map((ex) => (
                <button
                  key={ex.exchange}
                  type="button"
                  onClick={() => setSelectedExchange(ex.exchange)}
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                    selectedExchange === ex.exchange
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-line text-ink hover:bg-surface"
                  }`}
                >
                  {EXCHANGE_LABELS[ex.exchange] ?? ex.exchange}
                  {/* Availability badge: green=live, yellow=paper-only */}
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${
                      ex.available && !ex.paper_only
                        ? "bg-emerald-500"
                        : "bg-yellow-500"
                    }`}
                    title={ex.available && !ex.paper_only ? "Live available" : "Paper only"}
                  />
                </button>
              ))}
            </div>
          </div>

          {/* Market selector (spot/futures toggle) */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">Рынок:</span>
            <div className="inline-flex rounded-lg border border-line">
              <button
                type="button"
                onClick={() => setSelectedMarket("spot")}
                className={`rounded-l-lg px-3 py-1.5 text-xs font-medium transition ${
                  selectedMarket === "spot"
                    ? "bg-accent text-white"
                    : "text-ink hover:bg-surface"
                }`}
              >
                Spot
              </button>
              <button
                type="button"
                onClick={() => setSelectedMarket("futures")}
                className={`rounded-r-lg px-3 py-1.5 text-xs font-medium transition ${
                  selectedMarket === "futures"
                    ? "bg-accent text-white"
                    : "text-ink hover:bg-surface"
                }`}
              >
                Futures
              </button>
            </div>
          </div>

          {/* Task 8.3: Running state indicator per engine */}
          <div className="ml-auto flex items-center gap-1.5 text-xs text-ink-muted">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                status?.state.running ? "bg-emerald-500 animate-pulse" : "bg-red-400"
              }`}
            />
            {status?.state.running ? "Running" : "Stopped"}
            <span className="ml-1 font-mono text-[10px] opacity-70">
              ({EXCHANGE_LABELS[selectedExchange]}/{selectedMarket})
            </span>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto p-4 lg:grid-cols-[1.1fr_0.9fr]">
          <section className="space-y-3">
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Доступ
              </p>
              <label className="mb-1 block text-xs text-ink-muted">ADMIN_TOKEN</label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="X-Admin-Token"
                  className="w-full rounded-lg border border-line bg-surface-elevated px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                />
                <button
                  type="button"
                  onClick={() => {
                    try {
                      window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token.trim());
                      setOkMsg("Токен сохранён локально");
                    } catch {
                      setErr("Не удалось сохранить токен");
                    }
                  }}
                  className="rounded-lg border border-line px-3 py-2 text-sm text-ink transition hover:bg-accent/10"
                >
                  Сохранить
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-line bg-surface p-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  Runtime настройки
                </p>
                <button
                  type="button"
                  onClick={() => void refreshAll()}
                  className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs text-ink transition hover:bg-accent/10"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                  Обновить
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2 text-sm">
                <label className="col-span-1">
                  <span className="mb-1 block text-xs text-ink-muted">Mode</span>
                  <select
                    value={form.mode}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, mode: e.target.value as "paper" | "live" }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  >
                    <option value="paper">paper</option>
                    <option value="live">live</option>
                  </select>
                </label>
                <label className="col-span-1">
                  <span className="mb-1 block text-xs text-ink-muted">Symbol</span>
                  <input
                    value={form.symbol}
                    onChange={(e) => setForm((s) => ({ ...s, symbol: e.target.value.toUpperCase() }))}
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>

                {/* ─── Task 8.2: Order Type selector (LIMIT/MARKET) ──────────────── */}
                <div className="col-span-1">
                  <span className="mb-1 block text-xs text-ink-muted">Order Type</span>
                  <div className="flex gap-2">
                    {(["LIMIT", "MARKET"] as const).map((ot) => (
                      <label key={ot} className="inline-flex items-center gap-1 text-xs text-ink">
                        <input
                          type="radio"
                          name="order_type"
                          value={ot}
                          checked={form.order_type === ot}
                          onChange={() => setForm((s) => ({ ...s, order_type: ot }))}
                          className="text-accent focus:ring-accent"
                        />
                        {ot}
                      </label>
                    ))}
                  </div>
                </div>

                {/* ─── Task 8.2: Order Side selector (BUY/SELL) ──────────────────── */}
                <div className="col-span-1">
                  <span className="mb-1 block text-xs text-ink-muted">Order Side</span>
                  <div className="flex gap-2">
                    {(["BUY", "SELL"] as const).map((os) => (
                      <label key={os} className="inline-flex items-center gap-1 text-xs text-ink">
                        <input
                          type="radio"
                          name="order_side"
                          value={os}
                          checked={form.order_side === os}
                          onChange={() => setForm((s) => ({ ...s, order_side: os }))}
                          className="text-accent focus:ring-accent"
                        />
                        {os}
                      </label>
                    ))}
                  </div>
                </div>

                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Min net spread bps</span>
                  <input
                    type="number"
                    step="0.1"
                    value={form.min_net_spread_bps}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, min_net_spread_bps: Number(e.target.value) || 0 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Order quote notional</span>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={form.order_quote_notional}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, order_quote_notional: Number(e.target.value) || 0 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Price offset bps</span>
                  <input
                    type="number"
                    step="0.1"
                    value={form.limit_price_offset_bps}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        limit_price_offset_bps: Number(e.target.value) || 0,
                      }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Loop interval sec</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0.5"
                    value={form.loop_interval_sec}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, loop_interval_sec: Number(e.target.value) || 0.5 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Max orders/day</span>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={form.max_orders_per_day}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, max_orders_per_day: Number(e.target.value) || 1 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Max open orders</span>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={form.max_open_orders}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, max_open_orders: Number(e.target.value) || 0 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Max consecutive errors</span>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={form.max_consecutive_errors}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        max_consecutive_errors: Number(e.target.value) || 1,
                      }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">Recv window ms</span>
                  <input
                    type="number"
                    min="1000"
                    step="100"
                    value={form.recv_window_ms}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, recv_window_ms: Number(e.target.value) || 1000 }))
                    }
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
                <label className="col-span-2">
                  <span className="mb-1 block text-xs text-ink-muted">Events log path</span>
                  <input
                    value={form.events_log_path}
                    onChange={(e) => setForm((s) => ({ ...s, events_log_path: e.target.value }))}
                    className="w-full rounded-lg border border-line bg-surface-elevated px-2 py-2 text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                </label>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <label className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) => setForm((s) => ({ ...s, enabled: e.target.checked }))}
                    className="rounded border-line text-accent focus:ring-accent"
                  />
                  enabled (autostart on backend startup)
                </label>
                <label className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                  <input
                    type="checkbox"
                    checked={form.kill_switch}
                    onChange={(e) => setForm((s) => ({ ...s, kill_switch: e.target.checked }))}
                    className="rounded border-line text-accent focus:ring-accent"
                  />
                  kill switch
                </label>
              </div>

              <div className="mt-3">
                <button
                  type="button"
                  disabled={actionBusy}
                  onClick={() =>
                    void runAction(
                      "/api/trading/runtime-settings",
                      "PATCH",
                      form,
                      "Runtime параметры обновлены",
                    )
                  }
                  className="inline-flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  Применить настройки
                </button>
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Управление движком
              </p>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={actionBusy}
                  onClick={() => void runAction("/api/trading/start", "POST", undefined, "Trading start")}
                  className="inline-flex items-center justify-center gap-1 rounded-lg border border-line px-2 py-2 text-sm text-ink transition hover:bg-accent/10 disabled:opacity-50"
                >
                  <Play className="h-4 w-4" />
                  Start
                </button>
                <button
                  type="button"
                  disabled={actionBusy}
                  onClick={() => void runAction("/api/trading/stop", "POST", undefined, "Trading stop")}
                  className="inline-flex items-center justify-center gap-1 rounded-lg border border-line px-2 py-2 text-sm text-ink transition hover:bg-accent/10 disabled:opacity-50"
                >
                  <Square className="h-4 w-4" />
                  Stop
                </button>
                <button
                  type="button"
                  disabled={actionBusy}
                  onClick={() =>
                    void runAction("/api/trading/run-once", "POST", undefined, "Trading step выполнен")
                  }
                  className="inline-flex items-center justify-center gap-1 rounded-lg border border-line px-2 py-2 text-sm text-ink transition hover:bg-accent/10 disabled:opacity-50"
                >
                  <Zap className="h-4 w-4" />
                  Run once
                </button>
                <button
                  type="button"
                  disabled={actionBusy}
                  onClick={() =>
                    void runAction(
                      `/api/trading/kill-switch?enabled=${status?.state.kill_switch ? "false" : "true"}`,
                      "POST",
                      undefined,
                      status?.state.kill_switch ? "Kill switch OFF" : "Kill switch ON",
                    )
                  }
                  className="inline-flex items-center justify-center gap-1 rounded-lg border border-line px-2 py-2 text-sm text-ink transition hover:bg-accent/10 disabled:opacity-50"
                >
                  <Power className="h-4 w-4" />
                  {status?.state.kill_switch ? "Kill OFF" : "Kill ON"}
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-line bg-surface p-3 text-sm">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Статус
              </p>
              <dl className="grid grid-cols-2 gap-y-1 text-xs font-mono text-ink">
                <dt className="text-ink-muted">running</dt>
                <dd>{String(status?.state.running ?? false)}</dd>
                <dt className="text-ink-muted">mode</dt>
                <dd>{status?.state.mode ?? "—"}</dd>
                <dt className="text-ink-muted">symbol</dt>
                <dd>{status?.state.symbol ?? "—"}</dd>
                <dt className="text-ink-muted">kill_switch</dt>
                <dd>{String(status?.state.kill_switch ?? true)}</dd>
                <dt className="text-ink-muted">signals_seen</dt>
                <dd>{status?.state.signals_seen ?? 0}</dd>
                <dt className="text-ink-muted">orders_submitted</dt>
                <dd>{status?.state.orders_submitted ?? 0}</dd>
                <dt className="text-ink-muted">open_orders</dt>
                <dd>{status?.state.open_orders ?? 0}</dd>
                <dt className="text-ink-muted">last_signal_net_bps</dt>
                <dd>{String(status?.state.last_signal_net_spread_bps ?? "—")}</dd>
                <dt className="text-ink-muted">last_error</dt>
                <dd className="truncate" title={status?.state.last_error ?? ""}>
                  {status?.state.last_error ?? "—"}
                </dd>
                <dt className="text-ink-muted">started_at</dt>
                <dd>{fmtTs(status?.state.started_at)}</dd>
                <dt className="text-ink-muted">stopped_at</dt>
                <dd>{fmtTs(status?.state.stopped_at)}</dd>
              </dl>
            </div>

            <div className="rounded-xl border border-line bg-surface p-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  Последние события
                </p>
                <button
                  type="button"
                  onClick={() => void loadEvents()}
                  className="rounded-lg border border-line px-2 py-1 text-xs text-ink transition hover:bg-accent/10"
                >
                  Обновить
                </button>
              </div>
              <div className="max-h-72 overflow-auto rounded-lg border border-line bg-surface-elevated p-2 font-mono text-[11px] text-ink-muted">
                {events.length === 0 ? (
                  <p>Нет событий</p>
                ) : (
                  events
                    .slice()
                    .reverse()
                    .map((evt, idx) => (
                      <pre key={idx} className="whitespace-pre-wrap break-words border-b border-line/50 py-1 last:border-b-0">
                        {JSON.stringify(evt)}
                      </pre>
                    ))
                )}
              </div>
            </div>

            {err && (
              <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
                {err}
              </p>
            )}
            {okMsg && (
              <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
                {okMsg}
              </p>
            )}
          </section>
        </div>
      </>
    );

  if (pageMode) {
    return (
      <div className="flex w-full flex-col overflow-hidden rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark">
        {content}
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="trading-admin-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[94vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark"
        onClick={(e) => e.stopPropagation()}
      >
        {content}
      </div>
    </div>
  );
}
