import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowLeftRight,
  ExternalLink,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { apiUrl } from "./config";

// ─── Types ─────────────────────────────────────────────────────────────────────

interface AsterTicker {
  symbol: string;
  bid_price: number;
  bid_qty: number;
  ask_price: number;
  ask_qty: number;
  time_ms: number;
  spread_abs: number;
  mid: number;
  spread_bps: number | null;
}

interface CrossSpreadData {
  symbol: string;
  mexc: { bid: number; ask: number; mid: number; spread_bps: number | null } | null;
  aster: { bid: number; ask: number; mid: number; spread_bps: number | null } | null;
  cross_spread: {
    basis_abs: number;
    basis_bps: number | null;
    buy_mexc_sell_aster_abs: number;
    buy_mexc_sell_aster_bps: number | null;
    buy_aster_sell_mexc_abs: number;
    buy_aster_sell_mexc_bps: number | null;
  } | null;
}

interface AsterFunding {
  symbol: string;
  mark_price: number;
  index_price: number;
  last_funding_rate: number;
  next_funding_time: number;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1000) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.01) return n.toFixed(6);
  return n.toFixed(8);
}

type Tab = "tickers" | "cross" | "funding";

// ─── Component ─────────────────────────────────────────────────────────────────

export function AsterDexPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("tickers");
  const [tickers, setTickers] = useState<AsterTicker[]>([]);
  const [funding, setFunding] = useState<AsterFunding[]>([]);
  const [crossResults, setCrossResults] = useState<CrossSpreadData[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [minSpreadBps, setMinSpreadBps] = useState(0);
  const [sortBy, setSortBy] = useState<string>("spread_bps");
  const [ascending, setAscending] = useState(false);
  const [crossSymbols, setCrossSymbols] = useState("BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,ADAUSDT");
  const [autoRefresh, setAutoRefresh] = useState(false);

  const pollRef = useRef<number>(0);

  // ─── Fetch functions ───────────────────────────────────────────────────

  const fetchTickers = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(apiUrl("/api/aster/book-ticker"));
      if (r.ok) {
        const data = await r.json();
        if (data.ok) setTickers(data.tickers ?? []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  const fetchFunding = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/aster/funding"));
      if (r.ok) {
        const data = await r.json();
        if (data.ok) setFunding(data.funding ?? []);
      }
    } catch { /* ignore */ }
  }, []);

  const fetchCrossSpread = useCallback(async () => {
    const symbols = crossSymbols
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (!symbols.length) return;

    setLoading(true);
    const results: CrossSpreadData[] = [];
    for (const sym of symbols) {
      try {
        const r = await fetch(apiUrl(`/api/aster/cross-spread?symbol=${encodeURIComponent(sym)}`));
        if (r.ok) {
          const data = await r.json();
          if (data.ok) {
            results.push(data as CrossSpreadData);
          }
        }
      } catch { /* ignore */ }
    }
    setCrossResults(results);
    setLoading(false);
  }, [crossSymbols]);

  // ─── Effects ───────────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) return;
    if (tab === "tickers") fetchTickers();
    if (tab === "funding") fetchFunding();
    if (tab === "cross") fetchCrossSpread();
  }, [open, tab, fetchTickers, fetchFunding, fetchCrossSpread]);

  useEffect(() => {
    if (!open || !autoRefresh) return;
    const interval = tab === "cross" ? 10000 : 5000;
    pollRef.current = window.setInterval(() => {
      if (tab === "tickers") fetchTickers();
      if (tab === "funding") fetchFunding();
      if (tab === "cross") fetchCrossSpread();
    }, interval);
    return () => window.clearInterval(pollRef.current);
  }, [open, autoRefresh, tab, fetchTickers, fetchFunding, fetchCrossSpread]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // ─── Filtered & sorted tickers ─────────────────────────────────────────

  const filteredTickers = tickers
    .filter((t) => {
      if (search && !t.symbol.toUpperCase().includes(search.toUpperCase())) return false;
      if (minSpreadBps > 0 && (t.spread_bps ?? 0) < minSpreadBps) return false;
      return true;
    })
    .sort((a, b) => {
      const dir = ascending ? 1 : -1;
      const av = (a as any)[sortBy] ?? 0;
      const bv = (b as any)[sortBy] ?? 0;
      if (av === bv) return a.symbol.localeCompare(b.symbol);
      return av < bv ? -dir : dir;
    });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[75] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full max-w-6xl flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-100 dark:bg-purple-900/30">
              <span className="text-lg">⚡</span>
            </div>
            <div>
              <h2 className="text-lg font-semibold text-ink">AsterDEX</h2>
              <p className="text-xs text-ink-muted">Decentralized Perpetuals</p>
            </div>
            {loading && (
              <RefreshCw className="h-4 w-4 animate-spin text-ink-muted" />
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-ink-muted cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-line text-purple-500"
              />
              Авто
            </label>
            <a
              href="https://www.asterdex.com"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-line p-2 text-ink-muted hover:text-purple-500 transition"
              title="Открыть AsterDEX"
            >
              <ExternalLink className="h-4 w-4" />
            </a>
            <button onClick={onClose} className="rounded-lg border border-line p-2 text-ink hover:bg-surface">
              ✕
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-line">
          {([
            { key: "tickers" as Tab, label: "Тикеры", icon: Activity },
            { key: "cross" as Tab, label: "MEXC ↔ Aster", icon: ArrowLeftRight },
            { key: "funding" as Tab, label: "Funding", icon: TrendingUp },
          ]).map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition ${
                tab === key
                  ? "border-b-2 border-purple-500 text-purple-600 dark:text-purple-400"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* ─── Tickers tab ─── */}
          {tab === "tickers" && (
            <>
              <div className="flex items-center gap-3 border-b border-line px-4 py-2">
                <input
                  type="text"
                  placeholder="Поиск символа..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-48 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-purple-500"
                />
                <label className="flex items-center gap-1.5 text-xs text-ink-muted">
                  Min spread (bps):
                  <input
                    type="number"
                    step="0.5"
                    value={minSpreadBps || ""}
                    onChange={(e) => setMinSpreadBps(Number(e.target.value) || 0)}
                    className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-purple-500"
                  />
                </label>
                <span className="ml-auto text-xs text-ink-muted">
                  {filteredTickers.length} / {tickers.length} символов
                </span>
                <button
                  onClick={fetchTickers}
                  className="rounded-lg border border-line p-1.5 text-ink-muted hover:text-purple-500"
                  title="Обновить"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-elevated z-10">
                    <tr className="border-b border-line text-left text-ink-muted">
                      <th className="px-3 py-2 cursor-pointer hover:text-ink" onClick={() => { setSortBy("symbol"); setAscending(!ascending); }}>Символ</th>
                      <th className="px-3 py-2 cursor-pointer hover:text-ink text-right" onClick={() => { setSortBy("bid_price"); setAscending(!ascending); }}>Bid</th>
                      <th className="px-3 py-2 cursor-pointer hover:text-ink text-right" onClick={() => { setSortBy("ask_price"); setAscending(!ascending); }}>Ask</th>
                      <th className="px-3 py-2 cursor-pointer hover:text-ink text-right" onClick={() => { setSortBy("spread_bps"); setAscending(!ascending); }}>Spread (bps)</th>
                      <th className="px-3 py-2 text-right">Bid Qty</th>
                      <th className="px-3 py-2 text-right">Ask Qty</th>
                      <th className="px-3 py-2 text-right">Mid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTickers.slice(0, 200).map((t) => (
                      <tr key={t.symbol} className="border-b border-line/40 hover:bg-purple-50/50 dark:hover:bg-purple-900/10">
                        <td className="px-3 py-1.5 font-mono font-medium text-ink">{t.symbol}</td>
                        <td className="px-3 py-1.5 font-mono text-right text-green-600 dark:text-green-400">{fmtPrice(t.bid_price)}</td>
                        <td className="px-3 py-1.5 font-mono text-right text-red-600 dark:text-red-400">{fmtPrice(t.ask_price)}</td>
                        <td className="px-3 py-1.5 font-mono text-right font-medium">{fmt(t.spread_bps)}</td>
                        <td className="px-3 py-1.5 font-mono text-right text-ink-muted">{fmt(t.bid_qty, 4)}</td>
                        <td className="px-3 py-1.5 font-mono text-right text-ink-muted">{fmt(t.ask_qty, 4)}</td>
                        <td className="px-3 py-1.5 font-mono text-right">{fmtPrice(t.mid)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {filteredTickers.length > 200 && (
                  <p className="p-3 text-center text-xs text-ink-muted">
                    Показано 200 из {filteredTickers.length}. Используйте фильтры.
                  </p>
                )}
              </div>
            </>
          )}

          {/* ─── Cross-exchange tab ─── */}
          {tab === "cross" && (
            <>
              <div className="flex items-center gap-3 border-b border-line px-4 py-2">
                <label className="flex items-center gap-1.5 text-xs text-ink-muted">
                  Символы (через запятую):
                  <input
                    type="text"
                    value={crossSymbols}
                    onChange={(e) => setCrossSymbols(e.target.value)}
                    className="w-80 rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-purple-500"
                  />
                </label>
                <button
                  onClick={fetchCrossSpread}
                  className="flex items-center gap-1 rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-700"
                >
                  <RefreshCw className="h-3 w-3" /> Сравнить
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {crossResults.length === 0 ? (
                  <p className="py-12 text-center text-sm text-ink-muted">
                    Нажмите «Сравнить» для загрузки межбиржевого спреда
                  </p>
                ) : (
                  <div className="space-y-4">
                    {crossResults.map((cr) => (
                      <div
                        key={cr.symbol}
                        className="rounded-xl border border-line p-4 space-y-3"
                      >
                        <div className="flex items-center justify-between">
                          <h3 className="font-mono text-sm font-bold text-ink">{cr.symbol}</h3>
                          {cr.cross_spread && (
                            <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${
                              (cr.cross_spread.basis_bps ?? 0) > 0
                                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                                : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                            }`}>
                              Базис: {fmt(cr.cross_spread.basis_bps)} bps
                            </span>
                          )}
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                          {/* MEXC */}
                          <div className="rounded-lg bg-surface p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <span className="text-xs font-semibold text-ink-muted uppercase">MEXC</span>
                              {cr.mexc?.spread_bps != null && (
                                <span className="text-xs font-mono text-ink-muted">spread: {fmt(cr.mexc.spread_bps)} bps</span>
                              )}
                            </div>
                            {cr.mexc ? (
                              <div className="grid grid-cols-3 gap-2 text-xs">
                                <div>
                                  <span className="text-ink-muted">Bid</span>
                                  <div className="font-mono font-medium text-green-600">{fmtPrice(cr.mexc.bid)}</div>
                                </div>
                                <div>
                                  <span className="text-ink-muted">Ask</span>
                                  <div className="font-mono font-medium text-red-600">{fmtPrice(cr.mexc.ask)}</div>
                                </div>
                                <div>
                                  <span className="text-ink-muted">Mid</span>
                                  <div className="font-mono">{fmtPrice(cr.mexc.mid)}</div>
                                </div>
                              </div>
                            ) : (
                              <p className="text-xs text-ink-muted">Нет данных (WS не подключён для этого символа)</p>
                            )}
                          </div>

                          {/* AsterDEX */}
                          <div className="rounded-lg bg-surface p-3">
                            <div className="flex items-center gap-2 mb-2">
                              <span className="text-xs font-semibold text-purple-600 dark:text-purple-400 uppercase">AsterDEX</span>
                              {cr.aster?.spread_bps != null && (
                                <span className="text-xs font-mono text-ink-muted">spread: {fmt(cr.aster.spread_bps)} bps</span>
                              )}
                            </div>
                            {cr.aster ? (
                              <div className="grid grid-cols-3 gap-2 text-xs">
                                <div>
                                  <span className="text-ink-muted">Bid</span>
                                  <div className="font-mono font-medium text-green-600">{fmtPrice(cr.aster.bid)}</div>
                                </div>
                                <div>
                                  <span className="text-ink-muted">Ask</span>
                                  <div className="font-mono font-medium text-red-600">{fmtPrice(cr.aster.ask)}</div>
                                </div>
                                <div>
                                  <span className="text-ink-muted">Mid</span>
                                  <div className="font-mono">{fmtPrice(cr.aster.mid)}</div>
                                </div>
                              </div>
                            ) : (
                              <p className="text-xs text-ink-muted">Нет данных</p>
                            )}
                          </div>
                        </div>

                        {/* Cross spread results */}
                        {cr.cross_spread && (
                          <div className="grid grid-cols-2 gap-3 pt-2 border-t border-line/50">
                            <div className="flex items-center gap-2 rounded-lg bg-green-50 dark:bg-green-900/10 p-2">
                              <TrendingUp className="h-4 w-4 text-green-600 shrink-0" />
                              <div className="text-xs">
                                <div className="text-ink-muted">Buy MEXC → Sell Aster</div>
                                <div className={`font-mono font-bold ${
                                  (cr.cross_spread.buy_mexc_sell_aster_bps ?? 0) > 0
                                    ? "text-green-700 dark:text-green-400"
                                    : "text-red-600"
                                }`}>
                                  {fmt(cr.cross_spread.buy_mexc_sell_aster_bps)} bps
                                </div>
                              </div>
                            </div>
                            <div className="flex items-center gap-2 rounded-lg bg-blue-50 dark:bg-blue-900/10 p-2">
                              <TrendingDown className="h-4 w-4 text-blue-600 shrink-0" />
                              <div className="text-xs">
                                <div className="text-ink-muted">Buy Aster → Sell MEXC</div>
                                <div className={`font-mono font-bold ${
                                  (cr.cross_spread.buy_aster_sell_mexc_bps ?? 0) > 0
                                    ? "text-green-700 dark:text-green-400"
                                    : "text-red-600"
                                }`}>
                                  {fmt(cr.cross_spread.buy_aster_sell_mexc_bps)} bps
                                </div>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* ─── Funding tab ─── */}
          {tab === "funding" && (
            <>
              <div className="flex items-center gap-3 border-b border-line px-4 py-2">
                <span className="text-xs text-ink-muted">
                  {funding.length} символов с funding data
                </span>
                <button
                  onClick={fetchFunding}
                  className="ml-auto rounded-lg border border-line p-1.5 text-ink-muted hover:text-purple-500"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-elevated z-10">
                    <tr className="border-b border-line text-left text-ink-muted">
                      <th className="px-3 py-2">Символ</th>
                      <th className="px-3 py-2 text-right">Mark Price</th>
                      <th className="px-3 py-2 text-right">Index Price</th>
                      <th className="px-3 py-2 text-right">Funding Rate</th>
                      <th className="px-3 py-2 text-right">Funding (% annualized)</th>
                      <th className="px-3 py-2 text-right">Next Funding</th>
                    </tr>
                  </thead>
                  <tbody>
                    {funding
                      .filter((f) => f.last_funding_rate !== 0)
                      .sort((a, b) => Math.abs(b.last_funding_rate) - Math.abs(a.last_funding_rate))
                      .slice(0, 200)
                      .map((f) => {
                        const annualized = f.last_funding_rate * 3 * 365 * 100; // 8h intervals
                        const nextStr = f.next_funding_time > 0
                          ? new Date(f.next_funding_time).toLocaleTimeString()
                          : "—";
                        return (
                          <tr key={f.symbol} className="border-b border-line/40 hover:bg-purple-50/50 dark:hover:bg-purple-900/10">
                            <td className="px-3 py-1.5 font-mono font-medium text-ink">{f.symbol}</td>
                            <td className="px-3 py-1.5 font-mono text-right">{fmtPrice(f.mark_price)}</td>
                            <td className="px-3 py-1.5 font-mono text-right text-ink-muted">{fmtPrice(f.index_price)}</td>
                            <td className={`px-3 py-1.5 font-mono text-right font-medium ${
                              f.last_funding_rate > 0 ? "text-green-600" : f.last_funding_rate < 0 ? "text-red-600" : ""
                            }`}>
                              {(f.last_funding_rate * 100).toFixed(4)}%
                            </td>
                            <td className={`px-3 py-1.5 font-mono text-right ${
                              annualized > 0 ? "text-green-600" : "text-red-600"
                            }`}>
                              {annualized.toFixed(1)}%
                            </td>
                            <td className="px-3 py-1.5 text-right text-ink-muted">{nextStr}</td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
