import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiUrl } from "../config";
import type { Exchange, MarketRow, SnapshotResponse } from "../types";
import { EXCHANGE_LABELS } from "../types";

const FUTURES_EXCHANGES: Exchange[] = ["mexc", "binance", "bybit", "okx", "gateio", "bitget"];

interface FundingCell {
  symbol: string;
  rates: Record<string, number | null>; // exchange → funding_rate
}

/** Форматирование funding rate в % */
function fmtFunding(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(4) + "%";
}

/** Annualized yield from 8h funding rate */
function annualize(rate8h: number): number {
  return rate8h * 3 * 365 * 100; // 3 times/day × 365 days × 100%
}

/** Цвет ячейки по funding rate */
function fundingColor(v: number | null): string {
  if (v == null) return "text-ink-muted";
  if (v >= 0.001) return "text-emerald-500 font-semibold";
  if (v >= 0.0005) return "text-emerald-600 dark:text-emerald-400";
  if (v <= -0.001) return "text-rose-500 font-semibold";
  if (v <= -0.0005) return "text-rose-600 dark:text-rose-400";
  return "text-ink-muted";
}

/** Background color for heatmap cell */
function heatmapBg(v: number | null): string {
  if (v == null) return "";
  if (v >= 0.003) return "bg-emerald-500/20";
  if (v >= 0.001) return "bg-emerald-500/10";
  if (v >= 0.0005) return "bg-emerald-500/5";
  if (v <= -0.003) return "bg-rose-500/20";
  if (v <= -0.001) return "bg-rose-500/10";
  if (v <= -0.0005) return "bg-rose-500/5";
  return "";
}

export function FundingHeatmapPage() {
  const [data, setData] = useState<FundingCell[]>([]);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sortKey, setSortKey] = useState<"symbol" | "max" | "spread">("max");
  const [sortAsc, setSortAsc] = useState(false);
  const [search, setSearch] = useState("");
  const [minRate, setMinRate] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setErrors({});
    const allData: Record<string, Record<string, number | null>> = {};
    const errs: Record<string, string> = {};

    await Promise.allSettled(
      FUTURES_EXCHANGES.map(async (ex) => {
        try {
          const r = await fetch(apiUrl(`/api/snapshot?market=futures&exchange=${ex}`));
          const d: SnapshotResponse = await r.json();
          if (!d.ok || !d.rows) {
            errs[ex] = d.error ?? "Ошибка";
            return;
          }
          for (const row of d.rows) {
            const sym = (row as MarketRow).symbol;
            const fr = (row as MarketRow).funding_rate;
            if (!allData[sym]) allData[sym] = {};
            allData[sym][ex] = fr ?? null;
          }
        } catch (e) {
          errs[ex] = String(e);
        }
      })
    );

    setErrors(errs);

    // Convert to array
    const cells: FundingCell[] = Object.entries(allData).map(([symbol, rates]) => ({
      symbol,
      rates,
    }));

    setData(cells);
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const s = search.trim().toUpperCase();
    let out = data.filter((r) => {
      if (s && !r.symbol.includes(s)) return false;
      if (minRate > 0) {
        const maxAbs = Math.max(
          ...Object.values(r.rates).map((v) => Math.abs(v ?? 0))
        );
        if (maxAbs < minRate / 100) return false;
      }
      return true;
    });

    out.sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === "symbol") {
        return sortAsc ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
      }
      if (sortKey === "max") {
        av = Math.max(...Object.values(a.rates).map((v) => Math.abs(v ?? 0)));
        bv = Math.max(...Object.values(b.rates).map((v) => Math.abs(v ?? 0)));
      } else {
        // spread = max - min across exchanges
        const aVals = Object.values(a.rates).filter((v): v is number => v != null);
        const bVals = Object.values(b.rates).filter((v): v is number => v != null);
        av = aVals.length > 0 ? Math.max(...aVals) - Math.min(...aVals) : 0;
        bv = bVals.length > 0 ? Math.max(...bVals) - Math.min(...bVals) : 0;
      }
      return sortAsc ? av - bv : bv - av;
    });

    return out;
  }, [data, search, minRate, sortKey, sortAsc]);

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  // Funding Arb Screener: топ пар с самым высоким положительным funding
  const arbCandidates = useMemo(() => {
    return data
      .map((r) => {
        const entries = Object.entries(r.rates);
        const positive = entries.filter((pair): pair is [string, number] => pair[1] != null && pair[1] > 0);
        if (positive.length === 0) return null;
        const best = positive.reduce((a, b) => a[1] > b[1] ? a : b);
        const worst = positive.reduce((a, b) => a[1] < b[1] ? a : b);
        return {
          symbol: r.symbol,
          bestExchange: best[0],
          bestRate: best[1],
          worstExchange: worst[0],
          worstRate: worst[1],
          apy: annualize(best[1]),
          spread: best[1] - worst[1],
        };
      })
      .filter((r): r is NonNullable<typeof r> => r != null)
      .sort((a, b) => b.apy - a.apy)
      .slice(0, 20);
  }, [data]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-3 md:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink">
            Funding Rate Heatmap
          </h1>
          <p className="mt-1 text-xs text-ink-muted">
            Funding rates по всем биржам. Положительный = longs платят shorts. Annualized = ставка × 3 × 365.
          </p>
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

      {Object.keys(errors).length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          Часть бирж не ответила: {Object.entries(errors).map(([ex, msg]) => `${EXCHANGE_LABELS[ex as Exchange] ?? ex}: ${msg}`).join(", ")}
        </div>
      )}

      {/* Funding Arb Screener */}
      {arbCandidates.length > 0 && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
          <h2 className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">
            Funding Arb Screener — Топ-20 возможностей
          </h2>
          <p className="mt-1 text-xs text-ink-muted">
            Long spot + Short perp на бирже с самым высоким funding. APY = ставка × 3 × 365.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line text-ink-muted">
                  <th className="px-2 py-1.5 text-left">#</th>
                  <th className="px-2 py-1.5 text-left">Символ</th>
                  <th className="px-2 py-1.5 text-left">Лучшая биржа</th>
                  <th className="px-2 py-1.5 text-right">Funding</th>
                  <th className="px-2 py-1.5 text-right">APY</th>
                  <th className="px-2 py-1.5 text-right">Спред ставок</th>
                </tr>
              </thead>
              <tbody>
                {arbCandidates.map((r, i) => (
                  <tr key={r.symbol} className="border-b border-line/40 hover:bg-emerald-500/5">
                    <td className="px-2 py-1.5 text-ink-muted">{i + 1}</td>
                    <td className="px-2 py-1.5 font-mono font-medium text-ink">{r.symbol}</td>
                    <td className="px-2 py-1.5 text-ink">{EXCHANGE_LABELS[r.bestExchange as Exchange] ?? r.bestExchange}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-emerald-500">{fmtFunding(r.bestRate)}</td>
                    <td className="px-2 py-1.5 text-right font-mono font-semibold text-emerald-600 dark:text-emerald-400">{r.apy.toFixed(1)}%</td>
                    <td className="px-2 py-1.5 text-right font-mono text-violet-500">{fmtFunding(r.spread)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск символа (BTC, ETH…)"
          className="w-56 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
        />
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          Мин. |rate| (%)
          <input
            type="number"
            min={0}
            step={0.01}
            value={minRate || ""}
            onChange={(e) => setMinRate(Number(e.target.value) || 0)}
            className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </label>
        <span className="text-xs text-ink-muted">
          Пар: <span className="font-mono">{filtered.length}</span>
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-line bg-surface-elevated">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-surface-elevated text-[10px] uppercase tracking-wide text-ink-muted">
            <tr>
              <th
                className="cursor-pointer px-3 py-2 hover:text-ink"
                onClick={() => toggleSort("symbol")}
              >
                Символ {sortKey === "symbol" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              {FUTURES_EXCHANGES.map((ex) => (
                <th key={ex} className="px-3 py-2 text-center">
                  {EXCHANGE_LABELS[ex] ?? ex}
                </th>
              ))}
              <th
                className="cursor-pointer px-3 py-2 text-center hover:text-ink"
                onClick={() => toggleSort("max")}
              >
                Макс |rate| {sortKey === "max" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
              <th
                className="cursor-pointer px-3 py-2 text-center hover:text-ink"
                onClick={() => toggleSort("spread")}
              >
                Спред ставок {sortKey === "spread" ? (sortAsc ? "↑" : "↓") : ""}
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 500).map((r) => {
              const rates = Object.values(r.rates).filter((v): v is number => v != null);
              const maxAbs = Math.max(...rates.map(Math.abs), 0);
              const spread = rates.length > 0 ? Math.max(...rates) - Math.min(...rates) : 0;
              return (
                <tr key={r.symbol} className="border-b border-line/40 hover:bg-accent/5">
                  <td className="px-3 py-2 font-mono font-medium text-ink">{r.symbol}</td>
                  {FUTURES_EXCHANGES.map((ex) => {
                    const v = r.rates[ex];
                    return (
                      <td key={ex} className={`px-3 py-2 text-center font-mono ${fundingColor(v)} ${heatmapBg(v)}`}>
                        {v != null ? (
                          <span title={`Annualized: ${annualize(v).toFixed(1)}%`}>
                            {fmtFunding(v)}
                          </span>
                        ) : "—"}
                      </td>
                    );
                  })}
                  <td className={`px-3 py-2 text-center font-mono ${maxAbs >= 0.001 ? "text-amber-500 font-semibold" : "text-ink-muted"}`}>
                    {fmtFunding(maxAbs)}
                  </td>
                  <td className={`px-3 py-2 text-center font-mono ${spread >= 0.001 ? "text-violet-500 font-semibold" : "text-ink-muted"}`}>
                    {fmtFunding(spread)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length === 0 && !loading && (
          <div className="flex h-32 items-center justify-center text-sm text-ink-muted">
            Нет данных. Нажмите «Обновить» или подождите загрузки с бирж.
          </div>
        )}
      </div>
    </div>
  );
}
