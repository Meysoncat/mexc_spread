import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Layers, RefreshCw, Search, TrendingUp, TrendingDown, ArrowUpCircle, ArrowDownCircle, List, LayoutGrid } from "lucide-react";
import { apiUrl } from "../config";
import type { Exchange } from "../types";
import { EXCHANGE_LABELS } from "../types";
import { DensityDots } from "../DensityDots";

interface DensityWall {
  side: string;
  price: number;
  notional_usdt: number;
  ratio_to_median: number;
}

interface DensityInfo {
  total_bid_notional: number;
  total_ask_notional: number;
  bid_ask_ratio: number;
  imbalance: string;
}

interface SymbolDensity {
  symbol: string;
  mid: number;
  spread_bps: number | null;
  volume_24h_quote: number;
  density: DensityInfo | null;
  walls: DensityWall[];
  wall_count: number;
  largest_wall: DensityWall | null;
}

const EXCHANGES: Exchange[] = ["mexc", "binance", "bybit", "okx", "gateio", "bitget"];

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function fmtNotional(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function imbalanceColor(imbalance: string): string {
  if (imbalance === "bid_heavy") return "text-emerald-500";
  if (imbalance === "ask_heavy") return "text-rose-500";
  return "text-ink-muted";
}

function wallBarWidth(notional: number, maxNotional: number): number {
  if (maxNotional <= 0) return 0;
  return Math.min(100, (notional / maxNotional) * 100);
}

type FilterSide = "all" | "bid" | "ask";
type FilterImbalance = "all" | "bid_heavy" | "ask_heavy";

export function DensityMonitorPage() {
  const [exchange, setExchange] = useState<Exchange>("binance");
  const [market, setMarket] = useState<"spot" | "futures">("futures");
  const [data, setData] = useState<SymbolDensity[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [minVolume, setMinVolume] = useState(100_000);
  const [minWall, setMinWall] = useState(0);
  const [filterSide, setFilterSide] = useState<FilterSide>("all");
  const [filterImbalance, setFilterImbalance] = useState<FilterImbalance>("all");
  const [sortBy, setSortBy] = useState<"wall" | "ratio" | "symbol" | "volume" | "wall_count">("wall");
  const [sortAsc, setSortAsc] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [viewMode, setViewMode] = useState<"list" | "tiles">("list");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = new URLSearchParams({
        exchange,
        market,
        limit: "20",
        min_volume: String(minVolume),
      });
      const r = await fetch(apiUrl(`/api/density/overview?${q}`));
      const d = await r.json();
      if (d.ok) setData(d.symbols ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [exchange, market, minVolume]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    if (!autoRefresh) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    pollRef.current = setInterval(load, 30_000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [autoRefresh, load]);

  // Все стены из всех символов — "горячие" уровни
  const allWalls = useMemo(() => {
    const walls: Array<DensityWall & { symbol: string; mid: number }> = [];
    for (const r of data) {
      for (const w of r.walls) {
        walls.push({ ...w, symbol: r.symbol, mid: r.mid });
      }
    }
    walls.sort((a, b) => b.notional_usdt - a.notional_usdt);
    return walls;
  }, [data]);

  const maxWallNotional = useMemo(() => {
    return Math.max(...allWalls.map((w) => w.notional_usdt), 1);
  }, [allWalls]);

  const filtered = useMemo(() => {
    const s = search.trim().toUpperCase();
    let out = data.filter((r) => {
      if (s && !r.symbol.includes(s)) return false;
      if (minWall > 0) {
        const maxN = r.largest_wall?.notional_usdt ?? 0;
        if (maxN < minWall) return false;
      }
      if (filterSide !== "all" && r.largest_wall) {
        if (r.largest_wall.side !== filterSide) return false;
      }
      if (filterImbalance !== "all" && r.density) {
        if (r.density.imbalance !== filterImbalance) return false;
      }
      return true;
    });

    out.sort((a, b) => {
      let av: number, bv: number;
      if (sortBy === "symbol") {
        return sortAsc ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
      }
      if (sortBy === "wall") {
        av = a.largest_wall?.notional_usdt ?? 0;
        bv = b.largest_wall?.notional_usdt ?? 0;
      } else if (sortBy === "ratio") {
        av = a.density?.bid_ask_ratio ?? 0;
        bv = b.density?.bid_ask_ratio ?? 0;
      } else if (sortBy === "wall_count") {
        av = a.wall_count;
        bv = b.wall_count;
      } else {
        av = a.volume_24h_quote;
        bv = b.volume_24h_quote;
      }
      return sortAsc ? av - bv : bv - av;
    });

    return out;
  }, [data, search, minWall, filterSide, filterImbalance, sortBy, sortAsc]);

  const toggleSort = (key: typeof sortBy) => {
    if (sortBy === key) setSortAsc(!sortAsc);
    else { setSortBy(key); setSortAsc(false); }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-3 md:p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink flex items-center gap-2">
            <Layers className="h-5 w-5 text-violet-500" />
            Density Screener
          </h1>
          <p className="mt-1 text-xs text-ink-muted">
            Поиск крупных плотностей (walls) в стакане. Фильтруй по стороне, объёму, имбалансу.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium ${
              autoRefresh
                ? "bg-emerald-500 text-white"
                : "bg-surface-elevated text-ink-muted"
            }`}
          >
            {autoRefresh ? "Auto ON" : "Auto OFF"}
          </button>
          <div className="flex rounded-lg border border-line">
            <button
              onClick={() => setViewMode("list")}
              className={`px-2 py-1.5 text-xs font-medium transition ${viewMode === "list" ? "bg-accent text-accent-foreground" : "text-ink-muted hover:bg-surface"}`}
            >
              <List className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setViewMode("tiles")}
              className={`px-2 py-1.5 text-xs font-medium transition ${viewMode === "tiles" ? "bg-accent text-accent-foreground" : "text-ink-muted hover:bg-surface"}`}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-surface-elevated disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Обновить
          </button>
        </div>
      </div>

      {/* Hot Walls — топ-10 крупнейших стен */}
      {allWalls.length > 0 && (
        <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
          <h2 className="text-sm font-semibold text-violet-700 dark:text-violet-400 mb-3">
            Горячие стены — топ-{Math.min(10, allWalls.length)} крупнейших ордеров
          </h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {allWalls.slice(0, 10).map((w, i) => (
              <div
                key={`${w.symbol}-${w.side}-${w.price}-${i}`}
                className={`rounded-lg border p-2.5 ${
                  w.side === "bid"
                    ? "border-emerald-500/30 bg-emerald-500/5"
                    : "border-rose-500/30 bg-rose-500/5"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-bold text-ink">{w.symbol}</span>
                  <span className={`flex items-center gap-1 text-[10px] font-semibold ${
                    w.side === "bid" ? "text-emerald-500" : "text-rose-500"
                  }`}>
                    {w.side === "bid" ? <ArrowUpCircle className="h-3 w-3" /> : <ArrowDownCircle className="h-3 w-3" />}
                    {w.side.toUpperCase()}
                  </span>
                </div>
                <div className="mt-1 font-mono text-lg font-bold text-ink">
                  {fmtNotional(w.notional_usdt)}
                </div>
                <div className="mt-1 text-[10px] text-ink-muted">
                  @ {fmt(w.price)} · {w.ratio_to_median.toFixed(0)}× медианы
                </div>
                {/* Bar visualization */}
                <div className="mt-1.5 h-1.5 rounded-full bg-surface overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      w.side === "bid" ? "bg-emerald-500" : "bg-rose-500"
                    }`}
                    style={{ width: `${wallBarWidth(w.notional_usdt, maxWallNotional)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-line">
          {(["spot", "futures"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMarket(m)}
              className={`px-3 py-1.5 text-xs font-medium transition ${
                market === m
                  ? "bg-accent text-accent-foreground"
                  : "text-ink-muted hover:bg-surface"
              }`}
            >
              {m === "spot" ? "Спот" : "Фьючерсы"}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-line">
          {EXCHANGES.map((ex) => (
            <button
              key={ex}
              onClick={() => setExchange(ex)}
              className={`px-2.5 py-1.5 text-xs font-medium transition ${
                exchange === ex
                  ? "bg-accent text-accent-foreground"
                  : "text-ink-muted hover:bg-surface"
              }`}
            >
              {EXCHANGE_LABELS[ex] ?? ex}
            </button>
          ))}
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="BTC, ETH…"
            className="w-36 rounded-lg border border-line bg-surface pl-8 pr-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          Мин. объём
          <input
            type="number"
            value={minVolume}
            onChange={(e) => setMinVolume(Number(e.target.value) || 0)}
            className="w-24 rounded border border-line bg-surface px-2 py-1 font-mono text-xs text-ink"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          Мин. стена
          <input
            type="number"
            value={minWall || ""}
            onChange={(e) => setMinWall(Number(e.target.value) || 0)}
            placeholder="0"
            className="w-24 rounded border border-line bg-surface px-2 py-1 font-mono text-xs text-ink"
          />
        </label>
        <div className="flex rounded-lg border border-line">
          {(["all", "bid", "ask"] as FilterSide[]).map((v) => (
            <button
              key={v}
              onClick={() => setFilterSide(v)}
              className={`px-2 py-1.5 text-xs font-medium transition ${
                filterSide === v
                  ? v === "bid" ? "bg-emerald-500 text-white" : v === "ask" ? "bg-rose-500 text-white" : "bg-accent text-accent-foreground"
                  : "text-ink-muted hover:bg-surface"
              }`}
            >
              {v === "all" ? "Все" : v === "bid" ? "Bid↑" : "Ask↑"}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-line">
          {(["all", "bid_heavy", "ask_heavy"] as FilterImbalance[]).map((v) => (
            <button
              key={v}
              onClick={() => setFilterImbalance(v)}
              className={`px-2 py-1.5 text-xs font-medium transition ${
                filterImbalance === v ? "bg-accent text-accent-foreground" : "text-ink-muted hover:bg-surface"
              }`}
            >
              {v === "all" ? "Любой" : v === "bid_heavy" ? "Bid-heavy" : "Ask-heavy"}
            </button>
          ))}
        </div>
        <span className="text-xs text-ink-muted">
          Символов: <span className="font-mono">{filtered.length}</span>
        </span>
      </div>

      {/* Table or Tiles */}
      {viewMode === "tiles" ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="grid gap-3 p-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {filtered.map((r) => {
              const d = r.density;
              const w = r.largest_wall;
              return (
                <article
                  key={r.symbol}
                  className={`rounded-xl border p-3 transition hover:border-accent/40 ${
                    w && w.notional_usdt >= 100000
                      ? "border-emerald-500/30 bg-emerald-500/[0.03]"
                      : "border-line bg-surface-elevated"
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono font-bold text-sm text-ink">{r.symbol}</span>
                    <span className="text-[10px] text-ink-muted">{fmt(r.spread_bps)} bps</span>
                  </div>
                  <div className="grid grid-cols-2 gap-1.5 text-[11px] mb-2">
                    <div className="flex justify-between">
                      <span className="text-ink-muted">Mid</span>
                      <span className="font-mono">{fmt(r.mid)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-ink-muted">Объём</span>
                      <span className="font-mono">{fmtNotional(r.volume_24h_quote)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-emerald-600 dark:text-emerald-400">Bid</span>
                      <span className="font-mono">{d ? fmtNotional(d.total_bid_notional) : "—"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-rose-600 dark:text-rose-400">Ask</span>
                      <span className="font-mono">{d ? fmtNotional(d.total_ask_notional) : "—"}</span>
                    </div>
                  </div>
                  {/* Ratio bar */}
                  {d && (
                    <div className="flex items-center gap-1.5 mb-2">
                      <div className="flex-1 h-2 rounded-full bg-surface overflow-hidden flex">
                        <div
                          className="h-full bg-emerald-500/60"
                          style={{ width: `${(d.bid_ask_ratio / (d.bid_ask_ratio + 1)) * 100}%` }}
                        />
                        <div
                          className="h-full bg-rose-500/60"
                          style={{ width: `${(1 / (d.bid_ask_ratio + 1)) * 100}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-ink-muted">{fmt(d.bid_ask_ratio, 2)}</span>
                    </div>
                  )}
                  {/* Wall info */}
                  <div className="flex items-center justify-between border-t border-line pt-2">
                    <div className="flex items-center gap-1">
                      {w ? (
                        <>
                          <span className={w.side === "bid" ? "text-emerald-500 text-[10px] font-semibold" : "text-rose-500 text-[10px] font-semibold"}>
                            {w.side === "bid" ? "BID↑" : "ASK↑"}
                          </span>
                          <span className="font-mono text-xs font-bold text-ink">{fmtNotional(w.notional_usdt)}</span>
                        </>
                      ) : (
                        <span className="text-[10px] text-ink-muted">Нет стен</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {d?.imbalance === "bid_heavy" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
                      {d?.imbalance === "ask_heavy" && <TrendingDown className="h-3 w-3 text-rose-500" />}
                      <span className="text-[10px] text-ink-muted">{r.wall_count}W</span>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
          {filtered.length === 0 && !loading && (
            <div className="flex h-32 items-center justify-center text-sm text-ink-muted">
              Нет данных. Нажмите «Обновить» или измените фильтры.
            </div>
          )}
        </div>
      ) : (
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-line bg-surface-elevated">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface-elevated text-[10px] uppercase tracking-wide text-ink-muted">
            <tr>
              <th
                className="cursor-pointer px-3 py-2.5 text-left hover:text-ink"
                onClick={() => toggleSort("symbol")}
              >
                Символ {sortBy === "symbol" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th className="px-3 py-2.5 text-right">Mid</th>
              <th className="px-3 py-2.5 text-right">bps</th>
              <th
                className="cursor-pointer px-3 py-2.5 text-right hover:text-ink"
                onClick={() => toggleSort("volume")}
              >
                Объём 24h {sortBy === "volume" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th className="px-3 py-2.5 text-right">Bid</th>
              <th className="px-3 py-2.5 text-right">Ask</th>
              <th
                className="cursor-pointer px-3 py-2.5 text-center hover:text-ink"
                onClick={() => toggleSort("ratio")}
              >
                Ratio {sortBy === "ratio" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th className="px-3 py-2.5 text-center">Imbalance</th>
              <th
                className="cursor-pointer px-3 py-2.5 text-right hover:text-ink"
                onClick={() => toggleSort("wall")}
              >
                Стена {sortBy === "wall" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th className="px-3 py-2.5 text-center">Side</th>
              <th
                className="cursor-pointer px-3 py-2.5 text-right hover:text-ink"
                onClick={() => toggleSort("wall_count")}
              >
                Walls {sortBy === "wall_count" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th className="px-3 py-2.5 text-left w-[15%]">Визуализация</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const d = r.density;
              const w = r.largest_wall;
              return (
                <tr
                  key={r.symbol}
                  className="border-b border-line/40 hover:bg-accent/5 cursor-default"
                >
                  <td className="px-3 py-2 font-mono font-medium text-ink">{r.symbol}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmt(r.mid)}</td>
                  <td className="px-3 py-2 text-right font-mono text-accent">{fmt(r.spread_bps)}</td>
                  <td className="px-3 py-2 text-right font-mono text-ink-muted">{fmtNotional(r.volume_24h_quote)}</td>
                  <td className="px-3 py-2 text-right font-mono text-emerald-600 dark:text-emerald-400">
                    {d ? fmtNotional(d.total_bid_notional) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-rose-600 dark:text-rose-400">
                    {d ? fmtNotional(d.total_ask_notional) : "—"}
                  </td>
                  <td className={`px-3 py-2 text-center font-mono ${d ? imbalanceColor(d.imbalance) : ""}`}>
                    {d ? fmt(d.bid_ask_ratio, 2) : "—"}
                  </td>
                  <td className={`px-3 py-2 text-center font-medium ${d ? imbalanceColor(d.imbalance) : ""}`}>
                    {d?.imbalance === "bid_heavy" ? (
                      <span className="flex items-center justify-center gap-0.5"><TrendingUp className="h-3 w-3" /> BID</span>
                    ) : d?.imbalance === "ask_heavy" ? (
                      <span className="flex items-center justify-center gap-0.5"><TrendingDown className="h-3 w-3" /> ASK</span>
                    ) : "—"}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${
                    w ? (w.notional_usdt >= 100000 ? "text-emerald-500 font-bold" : "text-emerald-600 dark:text-emerald-400") : "text-ink-muted"
                  }`}>
                    {w ? fmtNotional(w.notional_usdt) : "—"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {w ? (
                      <span className={w.side === "bid" ? "text-emerald-500 font-semibold" : "text-rose-500 font-semibold"}>
                        {w.side === "bid" ? "BID" : "ASK"}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <DensityDots
                      wallCount={r.wall_count}
                      largestWallNotional={r.largest_wall?.notional_usdt ?? null}
                      imbalance={r.density?.imbalance ?? null}
                    />
                  </td>
                  {/* Mini bar visualization */}
                  <td className="px-3 py-2">
                    {d && (
                      <div className="flex items-center gap-1">
                        <div className="flex-1 h-2 rounded-full bg-surface overflow-hidden flex">
                          <div
                            className="h-full bg-emerald-500/60"
                            style={{ width: `${(d.bid_ask_ratio / (d.bid_ask_ratio + 1)) * 100}%` }}
                          />
                          <div
                            className="h-full bg-rose-500/60"
                            style={{ width: `${(1 / (d.bid_ask_ratio + 1)) * 100}%` }}
                          />
                        </div>
                        <span className="text-[9px] text-ink-muted w-6 text-right">
                          {r.wall_count > 0 ? `${r.wall_count}W` : ""}
                        </span>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length === 0 && !loading && (
          <div className="flex h-32 items-center justify-center text-sm text-ink-muted">
            Нет данных. Нажмите «Обновить» или измените фильтры.
          </div>
        )}
      </div>
      )}
    </div>
  );
}
