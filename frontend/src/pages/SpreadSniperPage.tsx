import { useCallback, useEffect, useRef, useState } from "react";
import {
  Play,
  Pause,
  ChevronDown,
  ChevronUp,
  Settings,
  TrendingUp,
  Activity,
  Target,
  Info,
  BookOpen,
  BarChart3,
  LineChart,
} from "lucide-react";
import { apiUrl, apiFetch } from "../config";
import { PriceChart, BasisChart } from "../components/charts";
import { SymbolPicker } from "../components/SymbolPicker";
import { LiveModeWarning } from "../components/LiveModeWarning";
import { useEngineCapabilities } from "../hooks/useEngineCapabilities";
import { useNavigationState } from "../hooks/useNavigationState";
import { EXCHANGE_LABELS, type ChartInterval, ChartVisualType, DepthResponse } from "../types";

interface FuturesArbSettings {
  enabled: boolean;
  mode: "paper" | "live";
  symbols: string[];
  exchange_combos: string[];
  entry_threshold_bps: number;
  exit_threshold_bps: number;
  position_notional_usdt: number;
  max_concurrent_positions: number;
  futures_leverage: number;
  max_hold_duration_hours: number;
  kill_switch: boolean;
  spot_taker_fee_bps: number;
  futures_taker_fee_bps: number;
}

interface FuturesArbPosition {
  id: string;
  symbol: string;
  exchange_combo: string;
  strategy: string;
  state: string;
  spot_side: string;
  spot_entry_price: number;
  spot_qty: number;
  futures_side: string;
  futures_entry_price: number;
  futures_qty: number;
  basis_pnl: number;
  cumulative_funding: number;
  total_pnl: number;
  open_time_ms: number;
  margin_ratio: number;
}

interface BasisRow {
  symbol: string;
  exchange_combo: string;
  spot_mid: number;
  futures_mid: number;
  basis_bps: number;
  executable_basis_cc_bps: number;
  executable_basis_rcc_bps: number;
  funding_rate: number | null;
  estimated_apy: number;
  status: string;
}

interface FuturesArbStatus {
  ok: boolean;
  running: boolean;
  settings?: FuturesArbSettings;
  current_basis?: BasisRow[];
  stats?: {
    total_trades: number;
    win_rate: number;
    total_net_pnl_usdt: number;
    total_funding_earned: number;
  };
  open_count?: number;
  total_exposure_usdt?: number;
}

const INTERVALS: ChartInterval[] = ["5m", "15m", "1h", "4h", "1d"];

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toFixed(d);
}

function fmtTime(ms: number): string {
  if (ms <= 0) return "—";
  const sec = Math.floor((Date.now() - ms) / 1000);
  if (sec < 60) return `${sec}с`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}м`;
  return `${(sec / 3600).toFixed(1)}ч`;
}

function parseCombo(combo: string): { entry: string; exit: string } {
  const parts = combo.split("+");
  if (parts.length === 2) {
    return { entry: parts[0], exit: parts[1] };
  }
  return { entry: combo, exit: combo };
}

/** Метка для ноги combo (mexc_spot/asterdex_perp/gateio …). */
function comboLabel(ex: string): string {
  // Особые составные ноги (spot/futures/perp) — человекочитаемая метка.
  if (ex === "mexc_spot") return "MEXC Spot";
  if (ex === "mexc_futures") return "MEXC Futures";
  if (ex === "asterdex_perp") return "AsterDEX";
  // Обычные биржи — берём каноническую метку из реестра.
  return EXCHANGE_LABELS[ex as keyof typeof EXCHANGE_LABELS] ?? ex;
}

/** Значения ног combo для select'ов (вход/выход). */
const COMBO_LEGS: string[] = [
  "mexc_spot",
  "mexc_futures",
  "asterdex_perp",
  "gateio",
  "binance",
  "bybit",
  "okx",
];

// Mini orderbook component
function MiniOrderbook({ market, symbol }: { market: "spot" | "futures"; symbol: string }) {
  const [depth, setDepth] = useState<DepthResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchDepth = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(
        apiUrl(`/api/depth?market=${market}&symbol=${symbol}&limit=5`)
      );
      if (r.ok) {
        const d = await r.json();
        if (d.ok) setDepth(d);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [market, symbol]);

  useEffect(() => {
    fetchDepth();
    const id = window.setInterval(fetchDepth, 2000);
    return () => window.clearInterval(id);
  }, [fetchDepth]);

  if (loading && !depth) {
    return <p className="text-[10px] text-ink-muted py-1">Загрузка…</p>;
  }
  if (!depth || !depth.bids || !depth.asks) {
    return <p className="text-[10px] text-ink-muted py-1">Нет данных</p>;
  }

  return (
    <div className="space-y-1">
      <div className="text-[10px] text-ink-muted uppercase font-semibold flex items-center gap-1">
        <BookOpen className="h-3 w-3" /> {market === "spot" ? "Spot" : "Futures"} — {symbol}
      </div>
      <div className="grid grid-cols-2 gap-1 text-[10px]">
        <div className="space-y-0.5">
          {depth.bids.slice(0, 5).map((b, i) => (
            <div
              key={i}
              className="flex justify-between px-1 py-0.5 rounded bg-green-500/10"
            >
              <span className="text-green-400 font-mono">{fmt(b.price, 2)}</span>
              <span className="text-ink-muted font-mono">{fmt(b.qty, 4)}</span>
            </div>
          ))}
        </div>
        <div className="space-y-0.5">
          {depth.asks.slice(0, 5).map((a, i) => (
            <div
              key={i}
              className="flex justify-between px-1 py-0.5 rounded bg-red-500/10"
            >
              <span className="text-red-400 font-mono">{fmt(a.price, 2)}</span>
              <span className="text-ink-muted font-mono">{fmt(a.qty, 4)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SpreadSniperPage() {
  const { state: navState, setSymbol } = useNavigationState();
  const symbol = navState.symbol;
  const [status, setStatus] = useState<FuturesArbStatus | null>(null);
  const [positions, setPositions] = useState<FuturesArbPosition[]>([]);
  const [fetchFailed, setFetchFailed] = useState(false);
  const pollRef = useRef<number>(0);

  // Chart state
  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [visual, setVisual] = useState<ChartVisualType>("candle");
  const market: "spot" | "futures" =
    navState.market === "futures" ? "futures" : "spot";
  const [chartMode, setChartMode] = useState<"price" | "basis">("price");

  // Collapsible sections
  const [showPnl, setShowPnl] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await apiFetch("/api/futures-arb/status");
      if (r.ok) {
        const d = await r.json();
        if (d.ok) {
          setStatus(d);
          setFetchFailed(false);
          // Синхронизируем глобальный символ с настройкой движка с бэкенда.
          if (d.settings?.symbols?.[0]) {
            setSymbol(d.settings.symbols[0]);
          }
          return;
        }
      }
      setFetchFailed(true);
    } catch {
      setFetchFailed(true);
    }
  }, [setSymbol]);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await apiFetch("/api/futures-arb/positions");
      if (r.ok) {
        const d = await r.json();
        if (d.ok) setPositions(d.positions ?? []);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchPositions();
    pollRef.current = window.setInterval(() => {
      fetchStatus();
      fetchPositions();
    }, 3000);
    return () => window.clearInterval(pollRef.current);
  }, [fetchStatus, fetchPositions]);

  const doStart = async () => {
    await apiFetch("/api/futures-arb/start", {
      method: "POST",
    });
    fetchStatus();
  };

  const doStop = async () => {
    await apiFetch("/api/futures-arb/stop", {
      method: "POST",
    });
    fetchStatus();
  };

  const updateSetting = async (patch: Record<string, unknown>) => {
    try {
      const r = await apiFetch("/api/futures-arb/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (r.ok) {
        const d = await r.json();
        if (d.ok) fetchStatus();
      }
    } catch {
      /* ignore */
    }
  };

  const settings = status?.settings;
  const running = status?.running ?? false;
  const combo = settings?.exchange_combos?.[0] ?? "mexc_spot+mexc_futures";
  const { entry, exit } = parseCombo(combo);
  const { liveReady: faLiveReady, reasons: faLiveReasons } =
    useEngineCapabilities("futures_arb");

  // Basis for current symbol
  const basisRow = status?.current_basis?.find(
    (b) => b.symbol === symbol && b.exchange_combo === combo
  );

  const activePosition = positions.find(
    (p) => p.symbol === symbol && p.exchange_combo === combo && p.state !== "closed"
  );

  // Determine market for each leg
  const entryMarket = entry.includes("futures") ? "futures" : "spot";
  const exitMarket = exit.includes("futures") ? "futures" : "spot";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Top toolbar */}
      <div className="flex items-center justify-between border-b border-line bg-surface px-3 py-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-ink-muted uppercase tracking-wider">
            График
          </span>
          <div className="flex items-center gap-1">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                onClick={() => setInterval(iv)}
                className={`rounded px-2 py-0.5 text-xs font-medium transition ${
                  interval === iv
                    ? "bg-accent text-white"
                    : "text-ink-muted hover:bg-accent/10 hover:text-ink"
                }`}
              >
                {iv}
              </button>
            ))}
          </div>
          <div className="mx-2 h-4 w-px bg-line" />
          <div className="flex items-center gap-1">
            {(["candle", "line"] as ChartVisualType[]).map((v) => (
              <button
                key={v}
                onClick={() => setVisual(v)}
                className={`rounded px-2 py-0.5 text-xs font-medium transition ${
                  visual === v
                    ? "bg-accent text-white"
                    : "text-ink-muted hover:bg-accent/10 hover:text-ink"
                }`}
              >
                {v === "candle" ? "Свечи" : "Линия"}
              </button>
            ))}
          </div>
          <div className="mx-2 h-4 w-px bg-line" />
          <div className="flex items-center gap-1">
            <button
              onClick={() => setChartMode("price")}
              className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition ${
                chartMode === "price"
                  ? "bg-accent text-white"
                  : "text-ink-muted hover:bg-accent/10 hover:text-ink"
              }`}
            >
              <LineChart className="h-3 w-3" /> Цена
            </button>
            <button
              onClick={() => setChartMode("basis")}
              className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition ${
                chartMode === "basis"
                  ? "bg-accent text-white"
                  : "text-ink-muted hover:bg-accent/10 hover:text-ink"
              }`}
            >
              <BarChart3 className="h-3 w-3" /> Базис
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span
            className={`flex h-2 w-2 rounded-full ${
              running ? "bg-green-500" : "bg-red-500"
            }`}
          />
          <span className="text-xs font-medium text-ink-muted">
            {running ? "Онлайн" : "Офлайн"}
          </span>
          <span className="text-xs text-ink-muted">
            {settings?.mode === "live" ? "Live" : "Paper"}
          </span>
        </div>
      </div>

      {/* Live-mode readiness warning: futures_arb engine has no live order
          path by design, so "live" mode is effectively paper. Tell the user
          instead of letting them believe they're trading real size. */}
      {settings?.mode === "live" && !faLiveReady && (
        <LiveModeWarning engineName="Futures Arb" reasons={faLiveReasons} />
      )}

      {/* Error banner */}
      {fetchFailed && (
        <div className="mx-3 mt-2 flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
          <Info className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            Не удалось загрузить статус движка. Проверьте, что бэкенд запущен.
          </span>
          <button
            onClick={() => {
              fetchStatus();
              fetchPositions();
            }}
            className="shrink-0 rounded-md border border-red-500/30 px-2 py-1 text-xs hover:bg-red-500/20"
          >
            Повторить
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chart area */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 p-2">
            {chartMode === "price" ? (
              <PriceChart
                symbol={symbol}
                market={market}
                interval={interval}
                visual={visual}
                className="h-full w-full rounded-lg border border-line"
              />
            ) : (
              <div className="h-full w-full rounded-lg border border-line bg-surface-elevated p-2">
                <BasisChart
                  symbol={symbol}
                  exchangeCombo={combo}
                  entryThresholdBps={settings?.entry_threshold_bps ?? 30}
                  exitThresholdBps={settings?.exit_threshold_bps ?? 5}
                  className="h-full w-full"
                />
              </div>
            )}
          </div>
        </div>

        {/* Right panel */}
        <div className="w-[420px] flex flex-col border-l border-line bg-surface-elevated overflow-y-auto scroll-thin">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div className="flex items-center gap-2">
              <Target className="h-5 w-5 text-accent" />
              <h2 className="text-base font-semibold text-ink">Spread Sniper</h2>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                  running
                    ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                    : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                }`}
              >
                {running ? "Running" : "Stopped"}
              </span>
            </div>
          </div>

          {/* Entry / Exit columns */}
          <div className="grid grid-cols-2 border-b border-line">
            {/* Entry column */}
            <div className="border-r border-line p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-green-500">Вход</span>
                <span className="text-[10px] text-ink-muted">
                  {basisRow ? `${fmt(basisRow.executable_basis_cc_bps)} bps` : "--"}
                </span>
              </div>
              <div className="space-y-2">
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Биржа</label>
                  <select
                    value={entry}
                    onChange={(e) => {
                      const newCombo = `${e.target.value}+${exit}`;
                      updateSetting({ exchange_combos: [newCombo] });
                    }}
                    className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                  >
                    {COMBO_LEGS.map((leg) => (
                      <option key={leg} value={leg}>
                        {comboLabel(leg)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Сторона</label>
                  <select className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
                    <option value="long">Лонг</option>
                    <option value="short">Шорт</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Рынок</label>
                  <select className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
                    <option value="futures">Фьючерс</option>
                    <option value="spot">Спот</option>
                  </select>
                </div>
                {/* Current price */}
                <div className="rounded bg-surface p-2 text-center">
                  <div className="text-[10px] text-ink-muted uppercase">Mid</div>
                  <div className="text-sm font-mono font-bold text-ink">
                    {basisRow ? fmt(basisRow.spot_mid, 2) : "—"}
                  </div>
                </div>
              </div>
            </div>

            {/* Exit column */}
            <div className="p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-red-500">Выход</span>
                <span className="text-[10px] text-ink-muted">
                  {basisRow ? `${fmt(basisRow.executable_basis_rcc_bps)} bps` : "--"}
                </span>
              </div>
              <div className="space-y-2">
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Биржа</label>
                  <select
                    value={exit}
                    onChange={(e) => {
                      const newCombo = `${entry}+${e.target.value}`;
                      updateSetting({ exchange_combos: [newCombo] });
                    }}
                    className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                  >
                    {COMBO_LEGS.map((leg) => (
                      <option key={leg} value={leg}>
                        {comboLabel(leg)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Сторона</label>
                  <select className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
                    <option value="short">Шорт</option>
                    <option value="long">Лонг</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-ink-muted uppercase">Рынок</label>
                  <select className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
                    <option value="futures">Фьючерс</option>
                    <option value="spot">Спот</option>
                  </select>
                </div>
                {/* Current price */}
                <div className="rounded bg-surface p-2 text-center">
                  <div className="text-[10px] text-ink-muted uppercase">Mid</div>
                  <div className="text-sm font-mono font-bold text-ink">
                    {basisRow ? fmt(basisRow.futures_mid, 2) : "—"}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Deal parameters */}
          <div className="border-b border-line">
            <button
              onClick={() => setShowPnl(!showPnl)}
              className="flex w-full items-center justify-between px-4 py-2 text-xs font-semibold text-ink-muted hover:bg-accent/5"
            >
              <span className="flex items-center gap-1.5">
                <Settings className="h-3.5 w-3.5" />
                Параметры сделки
              </span>
              {showPnl ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
            {showPnl && (
              <div className="space-y-3 px-4 pb-3">
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Тикер</span>
                    <SymbolPicker
                      value={symbol}
                      onChange={(s) => {
                        setSymbol(s);
                        updateSetting({ symbols: [s] });
                      }}
                      exchange={navState.exchange}
                      market={navState.market}
                      className="mt-0.5 w-full"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Ордер (USDT)</span>
                    <input
                      type="number"
                      step="10"
                      value={settings?.position_notional_usdt ?? 200}
                      onChange={(e) =>
                        updateSetting({
                          position_notional_usdt: parseFloat(e.target.value) || 200,
                        })
                      }
                      className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs font-mono text-ink"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Порог входа (bps)</span>
                    <input
                      type="number"
                      step="1"
                      value={settings?.entry_threshold_bps ?? 30}
                      onChange={(e) =>
                        updateSetting({
                          entry_threshold_bps: parseFloat(e.target.value) || 30,
                        })
                      }
                      className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs font-mono text-ink"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Порог выхода (bps)</span>
                    <input
                      type="number"
                      step="1"
                      value={settings?.exit_threshold_bps ?? 5}
                      onChange={(e) =>
                        updateSetting({
                          exit_threshold_bps: parseFloat(e.target.value) || 5,
                        })
                      }
                      className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs font-mono text-ink"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Макс. позиций</span>
                    <input
                      type="number"
                      step="1"
                      value={settings?.max_concurrent_positions ?? 5}
                      onChange={(e) =>
                        updateSetting({
                          max_concurrent_positions: parseInt(e.target.value) || 5,
                        })
                      }
                      className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs font-mono text-ink"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-ink-muted uppercase">Плечо</span>
                    <input
                      type="number"
                      step="1"
                      value={settings?.futures_leverage ?? 3}
                      onChange={(e) =>
                        updateSetting({
                          futures_leverage: parseInt(e.target.value) || 3,
                        })
                      }
                      className="mt-0.5 w-full rounded border border-line bg-surface px-2 py-1 text-xs font-mono text-ink"
                    />
                  </label>
                </div>

                {/* Start / Stop buttons */}
                <div className="grid grid-cols-2 gap-2 pt-1">
                  {!running ? (
                    <button
                      onClick={doStart}
                      className="col-span-2 flex items-center justify-center gap-1 rounded-lg bg-green-600 px-3 py-2 text-sm font-medium text-white hover:bg-green-700 transition"
                    >
                      <Play className="h-4 w-4" /> Старт
                    </button>
                  ) : (
                    <button
                      onClick={doStop}
                      className="col-span-2 flex items-center justify-center gap-1 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 transition"
                    >
                      <Pause className="h-4 w-4" /> Стоп
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* PNL section */}
          <div className="border-b border-line">
            <button
              onClick={() => setShowPnl(!showPnl)}
              className="flex w-full items-center justify-between px-4 py-2 text-xs font-semibold text-ink-muted hover:bg-accent/5"
            >
              <span className="flex items-center gap-1.5">
                <TrendingUp className="h-3.5 w-3.5" />
                Цена входа / PNL
              </span>
              {showPnl ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
            {showPnl && (
              <div className="px-4 pb-3">
                {activePosition ? (
                  <div className="space-y-2 text-xs">
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Spot Entry</span>
                        <div className="font-mono text-ink">{fmt(activePosition.spot_entry_price, 4)}</div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Futures Entry</span>
                        <div className="font-mono text-ink">{fmt(activePosition.futures_entry_price, 4)}</div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Basis PNL</span>
                        <div className={`font-mono ${activePosition.basis_pnl >= 0 ? "text-green-500" : "text-red-500"}`}>
                          {fmt(activePosition.basis_pnl, 4)} $
                        </div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Funding</span>
                        <div className="font-mono text-blue-400">{fmt(activePosition.cumulative_funding, 4)} $</div>
                      </div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Total PNL</span>
                      <div className={`font-mono text-base font-bold ${activePosition.total_pnl >= 0 ? "text-green-500" : "text-red-500"}`}>
                        {activePosition.total_pnl >= 0 ? "+" : ""}
                        {fmt(activePosition.total_pnl, 4)} $
                      </div>
                    </div>
                    <div className="text-[10px] text-ink-muted">
                      Удержание: {fmtTime(activePosition.open_time_ms)} | Margin: {(activePosition.margin_ratio * 100).toFixed(0)}%
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2 text-xs text-ink-muted">
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Spot Entry</span>
                        <div className="font-mono text-ink">0</div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Futures Entry</span>
                        <div className="font-mono text-ink">0</div>
                      </div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Total PNL</span>
                      <div className="font-mono text-ink">0.00 $</div>
                    </div>
                    <p className="text-center py-1">Нет открытой позиции</p>
                  </div>
                )}

                {/* Stats summary */}
                {status?.stats && (
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Сделок</span>
                      <div className="font-mono text-ink">{status.stats.total_trades}</div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Win Rate</span>
                      <div className="font-mono text-ink">{fmt(status.stats.win_rate * 100, 1)}%</div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Net PNL</span>
                      <div className={`font-mono ${status.stats.total_net_pnl_usdt >= 0 ? "text-green-500" : "text-red-500"}`}>
                        {fmt(status.stats.total_net_pnl_usdt, 2)} $
                      </div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Funding</span>
                      <div className="font-mono text-blue-400">{fmt(status.stats.total_funding_earned, 4)} $</div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Advanced parameters */}
          <div className="border-b border-line">
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex w-full items-center justify-between px-4 py-2 text-xs font-semibold text-ink-muted hover:bg-accent/5"
            >
              <span className="flex items-center gap-1.5">
                <Activity className="h-3.5 w-3.5" />
                Продвинутые параметры
              </span>
              {showAdvanced ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
            {showAdvanced && (
              <div className="px-4 pb-3 space-y-3">
                {basisRow ? (
                  <div className="space-y-2 text-xs">
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Spot Mid</span>
                        <div className="font-mono text-ink">{fmt(basisRow.spot_mid, 2)}</div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Futures Mid</span>
                        <div className="font-mono text-ink">{fmt(basisRow.futures_mid, 2)}</div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Basis bps</span>
                        <div className={`font-mono ${basisRow.basis_bps > 0 ? "text-green-500" : "text-red-500"}`}>
                          {fmt(basisRow.basis_bps, 1)}
                        </div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">Funding</span>
                        <div className="font-mono text-blue-400">
                          {basisRow.funding_rate != null ? (basisRow.funding_rate * 100).toFixed(4) + "%" : "—"}
                        </div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">CC bps</span>
                        <div className="font-mono text-ink">{fmt(basisRow.executable_basis_cc_bps, 1)}</div>
                      </div>
                      <div className="rounded bg-surface p-2">
                        <span className="text-[10px] text-ink-muted">RCC bps</span>
                        <div className="font-mono text-ink">{fmt(basisRow.executable_basis_rcc_bps, 1)}</div>
                      </div>
                    </div>
                    <div className="rounded bg-surface p-2">
                      <span className="text-[10px] text-ink-muted">Estimated APY</span>
                      <div className="font-mono text-yellow-400">{fmt(basisRow.estimated_apy, 1)}%</div>
                    </div>
                  </div>
                ) : (
                  <p className="text-center text-xs text-ink-muted py-2">Нет данных базиса для {symbol}</p>
                )}

                {/* Orderbook mini-views */}
                <div className="space-y-2">
                  <div className="text-[10px] font-semibold text-ink-muted uppercase">
                    Стакан (Orderbook)
                  </div>
                  <MiniOrderbook market={entryMarket} symbol={symbol} />
                  <MiniOrderbook market={exitMarket} symbol={symbol} />
                </div>

                {/* Open positions count */}
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded bg-surface p-2">
                    <span className="text-[10px] text-ink-muted">Позиций</span>
                    <div className="font-mono text-ink">{status?.open_count ?? 0}</div>
                  </div>
                  <div className="rounded bg-surface p-2">
                    <span className="text-[10px] text-ink-muted">Exposure</span>
                    <div className="font-mono text-ink">{fmt(status?.total_exposure_usdt, 0)} USDT</div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Positions list */}
          {positions.length > 0 && (
            <div className="px-4 py-3">
              <h3 className="text-xs font-semibold text-ink-muted mb-2">Открытые позиции ({positions.length})</h3>
              <div className="space-y-2">
                {positions.map((p) => (
                  <div
                    key={p.id}
                    className="rounded border border-line bg-surface p-2 text-xs"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono font-medium text-ink">{p.symbol}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${p.state === "open" ? "bg-green-900/30 text-green-400" : "bg-yellow-900/30 text-yellow-400"}`}>
                        {p.state}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-1 text-[10px] text-ink-muted">
                      <span>Spot: {p.spot_side} @{fmt(p.spot_entry_price, 2)}</span>
                      <span>Fut: {p.futures_side} @{fmt(p.futures_entry_price, 2)}</span>
                      <span className={p.total_pnl >= 0 ? "text-green-500" : "text-red-500"}>
                        PNL: {fmt(p.total_pnl, 4)} $
                      </span>
                      <span>{fmtTime(p.open_time_ms)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
