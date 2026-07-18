import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  Ban,
  BarChart3,
  Bell,
  BookOpen,
  Shield,
  Cpu,
  Layers,
  Plus,
  RefreshCw,
  Target,
  Trash2,
  Wallet,
} from "lucide-react";
import { apiFetch } from "../config";
import { Skeleton } from "../components/ui/Skeleton";
import { MetaScalpOrderbookChart } from "../components/charts/MetaScalpOrderbookChart";
import { MetaScalpClusterChart } from "../components/charts/MetaScalpClusterChart";
import { SymbolPicker } from "../components/SymbolPicker";
import { useNavigationState } from "../hooks/useNavigationState";

// ─── Types ─────────────────────────────────────────────────────────────────────

interface MetaScalpConnection {
  id: string;
  name: string;
  exchange: string;
  status: string;
}

interface MetaScalpBalance {
  coin: string;
  total: number;
  free: number;
  locked: number;
}

interface MetaScalpOrder {
  order_id: string;
  ticker: string;
  side: string;
  type: string;
  price: number;
  filled_price: number;
  size: number;
  filled_size: number;
  fee: number;
  fee_currency: string;
  status: string;
  time: string;
}

interface MetaScalpPosition {
  position_id: string;
  ticker: string;
  side: string;
  size: number;
  avg_price: number;
  avg_price_fix: number;
  avg_price_dyn: number;
  status: string;
}

interface MetaScalpOrderbookLevel {
  price: number;
  size: number;
  type: string;
}

interface MetaScalpOrderbook {
  ticker: string;
  best_ask: number;
  best_bid: number;
  asks: MetaScalpOrderbookLevel[];
  bids: MetaScalpOrderbookLevel[];
}

interface MetaScalpSignalLevel {
  id: string;
  connection_id: string;
  ticker: string;
  price: number;
  is_triggered: boolean;
  trigger_time: string;
  trigger_rule: string;
}

interface BasisData {
  ticker: string;
  spot_mid: number;
  futures_mid: number;
  basis_bps: number;
  executable_cc_bps: number;
  executable_rcc_bps: number;
  funding_rate: number;
  estimated_apy: number;
  status: string;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

async function apiGet(path: string) {
  const res = await apiFetch(path);
  return res.json();
}

async function apiPost(path: string, body: unknown) {
  const res = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiDelete(path: string) {
  const res = await apiFetch(path, {
    method: "DELETE",
  });
  return res.json();
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "\u2014";
  return n.toFixed(d);
}

// ─── Components ────────────────────────────────────────────────────────────────

function SectionCard({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </div>
      {children}
    </div>
  );
}

function Badge({ status }: { status: string }) {
  const color =
    status === "Connected" || status === "true" || status === "ok"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
      : status === "Disconnected" || status === "error"
      ? "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400"
      : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
  return <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${color}`}>{status}</span>;
}

export function MetaScalpPage() {
  const [ping, setPing] = useState<{ ok: boolean; base_url?: string; error?: string } | null>(null);
  const [connections, setConnections] = useState<MetaScalpConnection[]>([]);
  const [selectedConn, setSelectedConn] = useState<string>("");
  const [balances, setBalances] = useState<MetaScalpBalance[]>([]);
  const [orders, setOrders] = useState<MetaScalpOrder[]>([]);
  const [positions, setPositions] = useState<MetaScalpPosition[]>([]);
  const [orderbook, setOrderbook] = useState<MetaScalpOrderbook | null>(null);
  const [cluster, setCluster] = useState<{ ticker: string; rows: unknown[] } | null>(null);
  const [signalLevels, setSignalLevels] = useState<MetaScalpSignalLevel[]>([]);
  const [riskMetrics, setRiskMetrics] = useState<{
    open_notional: number;
    open_symbols: string[];
    positions_count: number;
    orders_count: number;
  } | null>(null);
  // Глобальный символ из контекста навигации (выбор в шапке Layout).
  const { state: navState, setSymbol: setTicker } = useNavigationState();
  const ticker = navState.symbol;
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const refreshIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Basis monitor
  const [basisData, setBasisData] = useState<BasisData | null>(null);
  const [spreadLoading, setSpreadLoading] = useState(false);
  const [spreadNotional, setSpreadNotional] = useState("200");

  // Order form
  const [orderForm, setOrderForm] = useState({
    ticker: "BTCUSDT",
    side: "Buy",
    type: "Limit",
    size: "0.01",
    price: "",
  });

  // Signal level form
  const [signalForm, setSignalForm] = useState({ ticker: "BTCUSDT", price: "", rule: "" });

  // Auto-trade config
  const [autoTradeConfig, setAutoTradeConfig] = useState({
    enabled: false,
    default_side: "Buy",
    default_type: "Market",
    default_size: "0.01",
    auto_cancel_triggered: true,
  });

  const fetchPing = useCallback(async () => {
    const data = await apiGet("/api/metascalp/ping");
    setPing(data);
  }, []);

  const fetchConnections = useCallback(async () => {
    const data = await apiGet("/api/metascalp/connections");
    setConnections(data);
    if (data.length > 0 && !selectedConn) {
      setSelectedConn(data[0].id);
    }
  }, [selectedConn]);

  const fetchAll = useCallback(async () => {
    if (!selectedConn) return;
    setLoading(true);
    try {
      const [b, o, p, ob, cl, sl] = await Promise.all([
        apiGet(`/api/metascalp/connections/${selectedConn}/balance`),
        apiGet(`/api/metascalp/connections/${selectedConn}/orders?ticker=${encodeURIComponent(ticker)}`),
        apiGet(`/api/metascalp/connections/${selectedConn}/positions`),
        apiGet(`/api/metascalp/connections/${selectedConn}/orderbook?ticker=${encodeURIComponent(ticker)}`),
        apiGet(`/api/metascalp/connections/${selectedConn}/cluster?ticker=${encodeURIComponent(ticker)}`),
        apiGet(`/api/metascalp/connections/${selectedConn}/signal-levels?ticker=${encodeURIComponent(ticker)}`),
      ]);
      setBalances(b.balances || []);
      setOrders(o.orders || []);
      setPositions(p.positions || []);
      setOrderbook(ob.ok ? ob : null);
      setCluster(cl.ok ? cl : null);
      setSignalLevels(sl.levels || []);
    } finally {
      setLoading(false);
      setLastUpdated(new Date());
    }
  }, [selectedConn, ticker]);

  const fetchBasis = useCallback(async () => {
    const data = await apiGet(`/api/metascalp/basis?ticker=${encodeURIComponent(ticker)}`);
    if (data.ok) {
      setBasisData(data);
    } else {
      setBasisData(null);
    }
  }, [ticker]);

  const fetchAutoTradeConfig = useCallback(async () => {
    const data = await apiGet("/api/metascalp/auto-trade/config");
    if (data.ok && data.config) {
      setAutoTradeConfig({
        enabled: data.config.enabled ?? false,
        default_side: data.config.default_side ?? "Buy",
        default_type: data.config.default_type ?? "Market",
        default_size: String(data.config.default_size ?? 0.01),
        auto_cancel_triggered: data.config.auto_cancel_triggered ?? true,
      });
    }
  }, []);

  const fetchRisk = useCallback(async () => {
    const data = await apiGet("/api/metascalp/risk");
    if (data.ok) {
      setRiskMetrics({
        open_notional: data.open_notional,
        open_symbols: data.open_symbols,
        positions_count: data.positions_count,
        orders_count: data.orders_count,
      });
    }
  }, []);

  useEffect(() => {
    fetchPing();
    fetchConnections();
    fetchRisk();
    fetchAutoTradeConfig();
  }, [fetchPing, fetchConnections, fetchRisk, fetchAutoTradeConfig]);

  useEffect(() => {
    fetchAll();
    fetchBasis();
  }, [fetchAll, fetchBasis]);

  // Auto-refresh every 5 seconds
  useEffect(() => {
    if (autoRefresh) {
      refreshIntervalRef.current = setInterval(() => {
        fetchAll();
        fetchBasis();
      }, 5000);
    }
    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
        refreshIntervalRef.current = null;
      }
    };
  }, [autoRefresh, fetchAll, fetchBasis]);

  const handlePlaceOrder = async () => {
    const payload = {
      ticker: orderForm.ticker,
      side: orderForm.side,
      type: orderForm.type,
      size: parseFloat(orderForm.size),
      price: orderForm.price ? parseFloat(orderForm.price) : undefined,
    };
    const res = await apiPost(`/api/metascalp/connections/${selectedConn}/orders`, payload);
    alert(res.ok ? "Ордер размещён" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleCancelOrder = async (orderId: string) => {
    const res = await apiPost(`/api/metascalp/connections/${selectedConn}/orders/cancel`, { order_id: orderId });
    alert(res.ok ? "Ордер отменён" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleCancelAll = async () => {
    const res = await apiPost(`/api/metascalp/connections/${selectedConn}/orders/cancel-all`, { ticker });
    alert(res.ok ? "Все ордера отменены" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handlePlaceSignal = async () => {
    const payload = {
      ticker: signalForm.ticker,
      price: parseFloat(signalForm.price),
      rule: signalForm.rule,
    };
    const res = await apiPost(`/api/metascalp/connections/${selectedConn}/signal-levels`, payload);
    alert(res.ok ? "Уровень установлен" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleRemoveSignal = async (levelId: string) => {
    const res = await apiDelete(`/api/metascalp/connections/${selectedConn}/signal-levels/${levelId}`);
    alert(res.ok ? "Уровень удалён" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleRemoveAllSignals = async () => {
    const res = await apiDelete(`/api/metascalp/connections/${selectedConn}/signal-levels?ticker=${encodeURIComponent(ticker)}`);
    alert(res.ok ? "Все уровни удалены" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleAutoTradeConfigUpdate = async (updates: Partial<typeof autoTradeConfig>) => {
    const newConfig = { ...autoTradeConfig, ...updates };
    setAutoTradeConfig(newConfig);
    const payload = {
      enabled: newConfig.enabled,
      default_side: newConfig.default_side,
      default_type: newConfig.default_type,
      default_size: parseFloat(newConfig.default_size),
      auto_cancel_triggered: newConfig.auto_cancel_triggered,
    };
    const res = await apiPost("/api/metascalp/auto-trade/config", payload);
    if (!res.ok) {
      alert(`Ошибка обновления конфигурации: ${res.error}`);
    }
  };

  const handleTestTrigger = async () => {
    if (!selectedConn) return;
    const res = await apiPost("/api/metascalp/auto-trade/trigger", {
      conn_id: selectedConn,
      ticker,
      price: parseFloat(orderForm.price) || 0,
      rule: signalForm.rule || "BuyMarket",
    });
    alert(res.ok ? "Триггер выполнен" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleRemoveTriggered = async () => {
    const res = await apiDelete("/api/metascalp/signal-levels/triggered");
    alert(res.ok ? "Сработавшие уровни удалены" : `Ошибка: ${res.error}`);
    fetchAll();
  };

  const handleOpenSpread = async (side: "Buy" | "Sell") => {
    if (!selectedConn) {
      alert("Выберите подключение");
      return;
    }
    setSpreadLoading(true);
    try {
      const res = await apiPost("/api/metascalp/spread", {
        conn_id: selectedConn,
        ticker,
        side,
        notional: parseFloat(spreadNotional) || 200,
        combo: "mexc_spot+mexc_futures",
      });
      if (res.ok) {
        alert(`Спред открыт: ${res.strategy}\nSpot: ${JSON.stringify(res.spot_order)}\nFutures: ${JSON.stringify(res.futures_order)}`);
        fetchAll();
      } else {
        alert(`Ошибка: ${res.error}`);
      }
    } finally {
      setSpreadLoading(false);
    }
  };

  const basisStatusColor =
    basisData?.status === "active"
      ? "text-emerald-500"
      : basisData?.status === "stale"
      ? "text-amber-500"
      : "text-ink-muted";

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-bold text-ink">MetaScalp Integration</h1>
        </div>
        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="text-[10px] text-ink-muted">
              Обновлено: {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => setAutoRefresh((v) => !v)}
            className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium ${
              autoRefresh
                ? "bg-emerald-500 text-white hover:bg-emerald-600"
                : "bg-surface-elevated text-ink-muted hover:text-ink"
            }`}
          >
            <Activity className="h-3.5 w-3.5" />
            {autoRefresh ? "Auto ON" : "Auto OFF"}
          </button>
          <button
            onClick={fetchAll}
            className="flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Обновить
          </button>
        </div>
      </div>

      {/* Status + Connection selector */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <SectionCard title="Статус MetaScalp" action={
          <button onClick={fetchPing} className="text-ink-muted hover:text-accent">
            <RefreshCw className="h-4 w-4" />
          </button>
        }>
          {ping ? (
            <div className="flex items-center gap-2">
              <Badge status={ping.ok ? "Connected" : "Disconnected"} />
              <span className="text-xs text-ink-muted">{ping.base_url || ping.error}</span>
            </div>
          ) : (
            // Loading placeholder inside a SectionCard (which renders children
            // inside a <div>, not a <tbody>). Using SkeletonTableRows here used
            // to emit a <tr> inside a <div>, triggering React's
            // validateDOMNesting warning. Plain Skeleton bars fit this non-table
            // context correctly.
            <div className="space-y-2">
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          )}
        </SectionCard>

        <SectionCard title="Подключение" action={
          <select
            className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            value={selectedConn}
            onChange={(e) => setSelectedConn(e.target.value)}
          >
            <option value="">— выберите —</option>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.exchange})
              </option>
            ))}
          </select>
        }>
          <div className="flex flex-wrap gap-2">
            {connections.map((c) => (
              <div
                key={c.id}
                className={`cursor-pointer rounded border px-2 py-1 text-xs ${
                  selectedConn === c.id
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-line bg-surface text-ink-muted hover:text-ink"
                }`}
                onClick={() => setSelectedConn(c.id)}
              >
                {c.name} <Badge status={c.status} />
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      {/* Ticker input */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-ink-muted">Тикер:</span>
        <SymbolPicker
          value={ticker}
          onChange={setTicker}
          exchange={navState.exchange}
          market={navState.market}
          className="w-32"
        />
        <button
          onClick={() => { fetchAll(); fetchBasis(); }}
          className="rounded bg-surface-elevated px-2 py-1 text-xs text-ink hover:bg-accent/10"
        >
          Загрузить
        </button>
      </div>

      {loading && <div className="text-xs text-ink-muted">Загрузка…</div>}

      {/* Data grids */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Balance */}
        <SectionCard title="Баланс" action={<Wallet className="h-4 w-4 text-ink-muted" />}>
          <div className="max-h-48 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-ink-muted">
                  <th className="pb-1">Coin</th>
                  <th className="pb-1">Total</th>
                  <th className="pb-1">Free</th>
                  <th className="pb-1">Locked</th>
                </tr>
              </thead>
              <tbody>
                {balances.length === 0 && <tr><td colSpan={4} className="py-2 text-ink-muted">Нет данных</td></tr>}
                {balances.map((b) => (
                  <tr key={b.coin} className="border-t border-line/50">
                    <td className="py-1 font-medium">{b.coin}</td>
                    <td className="py-1">{b.total.toFixed(4)}</td>
                    <td className="py-1">{b.free.toFixed(4)}</td>
                    <td className="py-1">{b.locked.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        {/* Positions */}
        <SectionCard title="Позиции" action={<Layers className="h-4 w-4 text-ink-muted" />}>
          <div className="max-h-48 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-ink-muted">
                  <th className="pb-1">Ticker</th>
                  <th className="pb-1">Side</th>
                  <th className="pb-1">Size</th>
                  <th className="pb-1">Avg Price</th>
                  <th className="pb-1">Status</th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && <tr><td colSpan={5} className="py-2 text-ink-muted">Нет позиций</td></tr>}
                {positions.map((p) => (
                  <tr key={p.position_id} className="border-t border-line/50">
                    <td className="py-1 font-medium">{p.ticker}</td>
                    <td className={`py-1 font-medium ${p.side === "Buy" ? "text-emerald-500" : "text-rose-500"}`}>{p.side}</td>
                    <td className="py-1">{p.size}</td>
                    <td className="py-1">{p.avg_price.toFixed(2)}</td>
                    <td className="py-1"><Badge status={p.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        {/* Orders */}
        <SectionCard
          title="Активные ордера"
          action={
            <button
              onClick={handleCancelAll}
              className="flex items-center gap-1 rounded bg-rose-100 px-2 py-1 text-xs text-rose-700 hover:bg-rose-200 dark:bg-rose-900/30 dark:text-rose-400"
            >
              <Ban className="h-3 w-3" />
              Отменить все
            </button>
          }
        >
          <div className="max-h-48 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-ink-muted">
                  <th className="pb-1">ID</th>
                  <th className="pb-1">Ticker</th>
                  <th className="pb-1">Side</th>
                  <th className="pb-1">Type</th>
                  <th className="pb-1">Price</th>
                  <th className="pb-1">Size</th>
                  <th className="pb-1">Status</th>
                  <th className="pb-1"></th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 && <tr><td colSpan={8} className="py-2 text-ink-muted">Нет ордеров</td></tr>}
                {orders.map((o) => (
                  <tr key={o.order_id} className="border-t border-line/50">
                    <td className="py-1 font-mono text-[10px]">{o.order_id.slice(0, 8)}…</td>
                    <td className="py-1">{o.ticker}</td>
                    <td className={`py-1 font-medium ${o.side === "Buy" ? "text-emerald-500" : "text-rose-500"}`}>{o.side}</td>
                    <td className="py-1">{o.type}</td>
                    <td className="py-1">{o.price.toFixed(2)}</td>
                    <td className="py-1">{o.size}</td>
                    <td className="py-1"><Badge status={o.status} /></td>
                    <td className="py-1">
                      <button
                        onClick={() => handleCancelOrder(o.order_id)}
                        className="text-rose-500 hover:text-rose-700"
                      >
                        <Ban className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        {/* Orderbook Chart */}
        <SectionCard title="Стакан" action={<BookOpen className="h-4 w-4 text-ink-muted" />}>
          {orderbook ? (
            <div className="h-64">
              <MetaScalpOrderbookChart
                asks={orderbook.asks}
                bids={orderbook.bids}
              />
            </div>
          ) : (
            <div className="text-xs text-ink-muted">Нет данных</div>
          )}
        </SectionCard>
      </div>

      {/* Cluster Chart */}
      <SectionCard title="Кластер (Volume Profile)" action={<Activity className="h-4 w-4 text-ink-muted" />}>
        {cluster && cluster.rows.length > 0 ? (
          <div className="h-64">
            <MetaScalpClusterChart rows={cluster.rows as any} />
          </div>
        ) : (
          <div className="text-xs text-ink-muted">Нет данных кластера</div>
        )}
      </SectionCard>

      {/* Basis Monitor */}
      <SectionCard title="Basis Monitor (Spread Sniper)" action={<BarChart3 className="h-4 w-4 text-ink-muted" />}>
        {basisData ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Spot Mid</div>
                <div className="text-sm font-semibold font-mono text-ink">{fmt(basisData.spot_mid, 2)}</div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Futures Mid</div>
                <div className="text-sm font-semibold font-mono text-ink">{fmt(basisData.futures_mid, 2)}</div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Basis bps</div>
                <div className={`text-sm font-semibold font-mono ${basisData.basis_bps > 0 ? "text-emerald-500" : "text-rose-500"}`}>
                  {fmt(basisData.basis_bps, 1)}
                </div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Funding</div>
                <div className="text-sm font-semibold font-mono text-blue-400">
                  {(basisData.funding_rate * 100).toFixed(4)}%
                </div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">CC bps</div>
                <div className="text-sm font-semibold font-mono text-ink">{fmt(basisData.executable_cc_bps, 1)}</div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">RCC bps</div>
                <div className="text-sm font-semibold font-mono text-ink">{fmt(basisData.executable_rcc_bps, 1)}</div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Est. APY</div>
                <div className="text-sm font-semibold font-mono text-yellow-400">{fmt(basisData.estimated_apy, 1)}%</div>
              </div>
              <div className="rounded border border-line bg-surface p-2">
                <div className="text-[10px] text-ink-muted">Status</div>
                <div className={`text-sm font-semibold font-mono ${basisStatusColor}`}>{basisData.status}</div>
              </div>
            </div>

            {/* Spread open form */}
            <div className="flex items-end gap-3 pt-1">
              <div>
                <label className="text-[10px] text-ink-muted">Notional (USDT)</label>
                <input
                  type="number"
                  step="10"
                  value={spreadNotional}
                  onChange={(e) => setSpreadNotional(e.target.value)}
                  className="w-28 rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                />
              </div>
              <button
                onClick={() => handleOpenSpread("Buy")}
                disabled={spreadLoading || !selectedConn}
                className="flex items-center gap-1 rounded bg-emerald-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                <Target className="h-3.5 w-3.5" />
                {spreadLoading ? "..." : "Открыть C&C (Buy+Short)"}
              </button>
              <button
                onClick={() => handleOpenSpread("Sell")}
                disabled={spreadLoading || !selectedConn}
                className="flex items-center gap-1 rounded bg-amber-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50"
              >
                <Target className="h-3.5 w-3.5" />
                {spreadLoading ? "..." : "Открыть RCC (Sell+Long)"}
              </button>
            </div>
          </div>
        ) : (
          <div className="text-xs text-ink-muted">Нет данных базиса. Убедитесь, что futures-arb движок запущен.</div>
        )}
      </SectionCard>

      {/* Risk Metrics */}
      <SectionCard title="Риск-метрики" action={<Shield className="h-4 w-4 text-ink-muted" />}>
        {riskMetrics ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div className="rounded border border-line bg-surface p-2">
              <div className="text-[10px] text-ink-muted">Open Notional</div>
              <div className="text-sm font-semibold text-ink">{riskMetrics.open_notional.toFixed(2)} USDT</div>
            </div>
            <div className="rounded border border-line bg-surface p-2">
              <div className="text-[10px] text-ink-muted">Positions</div>
              <div className="text-sm font-semibold text-ink">{riskMetrics.positions_count}</div>
            </div>
            <div className="rounded border border-line bg-surface p-2">
              <div className="text-[10px] text-ink-muted">Orders</div>
              <div className="text-sm font-semibold text-ink">{riskMetrics.orders_count}</div>
            </div>
            <div className="rounded border border-line bg-surface p-2">
              <div className="text-[10px] text-ink-muted">Symbols</div>
              <div className="text-sm font-semibold text-ink">{riskMetrics.open_symbols.join(", ") || "—"}</div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-ink-muted">Нет данных</div>
        )}
      </SectionCard>

      {/* Signal Levels */}
      <SectionCard
        title="Signal Levels"
        action={
          <div className="flex gap-2">
            <button
              onClick={handleRemoveAllSignals}
              className="flex items-center gap-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-700 hover:bg-amber-200 dark:bg-amber-900/30 dark:text-amber-400"
            >
              <Trash2 className="h-3 w-3" />
              Удалить все
            </button>
            <button
              onClick={handleRemoveTriggered}
              className="flex items-center gap-1 rounded bg-rose-100 px-2 py-1 text-xs text-rose-700 hover:bg-rose-200 dark:bg-rose-900/30 dark:text-rose-400"
            >
              <Bell className="h-3 w-3" />
              Очистить сработавшие
            </button>
          </div>
        }
      >
        <div className="max-h-48 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr className="text-left text-ink-muted">
                <th className="pb-1">ID</th>
                <th className="pb-1">Ticker</th>
                <th className="pb-1">Price</th>
                <th className="pb-1">Rule</th>
                <th className="pb-1">Triggered</th>
                <th className="pb-1"></th>
              </tr>
            </thead>
            <tbody>
              {signalLevels.length === 0 && <tr><td colSpan={6} className="py-2 text-ink-muted">Нет уровней</td></tr>}
              {signalLevels.map((sl) => (
                <tr key={sl.id} className="border-t border-line/50">
                  <td className="py-1 font-mono text-[10px]">{sl.id.slice(0, 8)}…</td>
                  <td className="py-1">{sl.ticker}</td>
                  <td className="py-1">{sl.price.toFixed(2)}</td>
                  <td className="py-1">{sl.trigger_rule || "—"}</td>
                  <td className="py-1">
                    <Badge status={sl.is_triggered ? "Triggered" : "Active"} />
                  </td>
                  <td className="py-1">
                    <button onClick={() => handleRemoveSignal(sl.id)} className="text-rose-500 hover:text-rose-700">
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* Place Order Form */}
      <SectionCard title="Размещение ордера" action={<Plus className="h-4 w-4 text-ink-muted" />}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div>
            <label className="text-[10px] text-ink-muted">Ticker</label>
            <input
              type="text"
              value={orderForm.ticker}
              onChange={(e) => setOrderForm((s) => ({ ...s, ticker: e.target.value.toUpperCase() }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            />
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Side</label>
            <select
              value={orderForm.side}
              onChange={(e) => setOrderForm((s) => ({ ...s, side: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            >
              <option value="Buy">Buy</option>
              <option value="Sell">Sell</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Type</label>
            <select
              value={orderForm.type}
              onChange={(e) => setOrderForm((s) => ({ ...s, type: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            >
              <option value="Limit">Limit</option>
              <option value="Market">Market</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Size</label>
            <input
              type="number"
              step="0.001"
              value={orderForm.size}
              onChange={(e) => setOrderForm((s) => ({ ...s, size: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            />
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Price (опц.)</label>
            <input
              type="number"
              step="0.01"
              value={orderForm.price}
              onChange={(e) => setOrderForm((s) => ({ ...s, price: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
              placeholder="Market"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handlePlaceOrder}
              className={`flex items-center gap-1 rounded px-3 py-1.5 text-xs font-medium text-white ${
                orderForm.side === "Buy"
                  ? "bg-emerald-500 hover:bg-emerald-600"
                  : "bg-rose-500 hover:bg-rose-600"
              }`}
            >
              {orderForm.side === "Buy" ? <ArrowUpCircle className="h-3.5 w-3.5" /> : <ArrowDownCircle className="h-3.5 w-3.5" />}
              Разместить
            </button>
          </div>
        </div>
      </SectionCard>

      {/* Auto-Trade Config */}
      <SectionCard
        title="Авто-торговля Signal Levels"
        action={
          <button
            onClick={() => handleAutoTradeConfigUpdate({ enabled: !autoTradeConfig.enabled })}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs font-medium ${
              autoTradeConfig.enabled
                ? "bg-emerald-500 text-white hover:bg-emerald-600"
                : "bg-surface-elevated text-ink-muted hover:text-ink"
            }`}
          >
            <Activity className="h-3 w-3" />
            {autoTradeConfig.enabled ? "ON" : "OFF"}
          </button>
        }
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div>
            <label className="text-[10px] text-ink-muted">Default Side</label>
            <select
              value={autoTradeConfig.default_side}
              onChange={(e) => handleAutoTradeConfigUpdate({ default_side: e.target.value })}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            >
              <option value="Buy">Buy</option>
              <option value="Sell">Sell</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Default Type</label>
            <select
              value={autoTradeConfig.default_type}
              onChange={(e) => handleAutoTradeConfigUpdate({ default_type: e.target.value })}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            >
              <option value="Market">Market</option>
              <option value="Limit">Limit</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Default Size</label>
            <input
              type="number"
              step="0.001"
              value={autoTradeConfig.default_size}
              onChange={(e) => handleAutoTradeConfigUpdate({ default_size: e.target.value })}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handleTestTrigger}
              className="flex items-center gap-1 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90"
            >
              <ArrowUpCircle className="h-3.5 w-3.5" />
              Тест триггера
            </button>
          </div>
        </div>
      </SectionCard>

      {/* Place Signal Level Form */}
      <SectionCard title="Установка уровня сигнала" action={<Bell className="h-4 w-4 text-ink-muted" />}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div>
            <label className="text-[10px] text-ink-muted">Ticker</label>
            <input
              type="text"
              value={signalForm.ticker}
              onChange={(e) => setSignalForm((s) => ({ ...s, ticker: e.target.value.toUpperCase() }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            />
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Price</label>
            <input
              type="number"
              step="0.01"
              value={signalForm.price}
              onChange={(e) => setSignalForm((s) => ({ ...s, price: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            />
          </div>
          <div>
            <label className="text-[10px] text-ink-muted">Rule (опц.)</label>
            <input
              type="text"
              value={signalForm.rule}
              onChange={(e) => setSignalForm((s) => ({ ...s, rule: e.target.value }))}
              className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
              placeholder="e.g. BuyMarket"
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={handlePlaceSignal}
              className="flex items-center gap-1 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90"
            >
              <Plus className="h-3.5 w-3.5" />
              Установить
            </button>
          </div>
        </div>
      </SectionCard>
    </div>
  );
}
