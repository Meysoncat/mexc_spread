import { Fragment, useCallback, useEffect, useState } from "react";
import { ArrowDown, ChevronDown, ChevronRight } from "lucide-react";
import { v1Get } from "../setups/api";
import { fmtNum, fmtPrice, fmtSigned, fmtUsd } from "../setups/format";

interface ScreenerRow {
  base: string;
  venues: number;
  best_bid: { venue: string; price: number };
  best_ask: { venue: string; price: number };
  cross_spread_bps: number;
  funding_low: { venue: string; apr_pct: number } | null;
  funding_high: { venue: string; apr_pct: number } | null;
  funding_spread_apr_pct: number;
  volume_24h_usd: number;
  open_interest_usd: number;
  spot_venues: number;
  max_basis_bps: number | null;
  active_setups: number;
}

interface MarketPerp {
  venue: string;
  symbol: string;
  bid: number;
  ask: number;
  bid_qty: number | null;
  ask_qty: number | null;
  spread_bps: number;
  age_sec: number;
  indicative: boolean;
  funding_apr_pct: number | null;
  funding_interval_h: number;
  next_funding_ms: number | null;
  open_interest_usd: number | null;
  volume_24h_usd: number | null;
}

interface MarketSpot {
  venue: string;
  symbol: string;
  bid: number;
  ask: number;
  age_sec: number;
}

type SortKey = "cross_spread_bps" | "funding_spread_apr_pct" | "volume_24h_usd" | "max_basis_bps";

const POLL_MS = 3000;

function MarketDetail({ base }: { base: string }) {
  const [data, setData] = useState<{ perps: MarketPerp[]; spots: MarketSpot[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      v1Get<{ perps: MarketPerp[]; spots: MarketSpot[] }>(`/market/${encodeURIComponent(base)}`)
        .then((d) => alive && setData(d))
        .catch((e: Error) => alive && setError(e.message));
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [base]);
  if (error && !data) return <p className="px-3 py-3 text-xs text-rose-500">{error}</p>;
  if (!data) return <p className="px-3 py-3 text-xs text-ink-muted">Загрузка…</p>;
  const bestBid = Math.max(...data.perps.map((p) => p.bid));
  const bestAsk = Math.min(...data.perps.map((p) => p.ask));
  return (
    <div className="overflow-x-auto px-3 py-2 scroll-thin">
      <table className="w-full min-w-[760px] text-xs">
        <thead className="text-left text-ink-muted">
          <tr>
            <th className="py-1 pr-2 font-medium">Биржа</th>
            <th className="py-1 pr-2 font-medium">Символ</th>
            <th className="py-1 pr-2 text-right font-medium">Bid</th>
            <th className="py-1 pr-2 text-right font-medium">Ask</th>
            <th className="py-1 pr-2 text-right font-medium">Спред</th>
            <th className="py-1 pr-2 text-right font-medium">L1 bid/ask</th>
            <th className="py-1 pr-2 text-right font-medium">Funding APR</th>
            <th className="py-1 pr-2 text-right font-medium">OI</th>
            <th className="py-1 text-right font-medium">Возраст</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {data.perps.map((p) => (
            <tr key={p.venue} className="border-t border-line/50">
              <td className="py-1 pr-2 font-sans">
                {p.venue}
                {p.indicative && <span className="ml-1 text-[10px] text-amber-500" title="impact-цены, не L1">~</span>}
              </td>
              <td className="py-1 pr-2 text-ink-muted">{p.symbol}</td>
              <td className={`py-1 pr-2 text-right ${p.bid === bestBid ? "font-semibold text-emerald-600 dark:text-emerald-400" : ""}`}>{fmtPrice(p.bid)}</td>
              <td className={`py-1 pr-2 text-right ${p.ask === bestAsk ? "font-semibold text-rose-600 dark:text-rose-400" : ""}`}>{fmtPrice(p.ask)}</td>
              <td className="py-1 pr-2 text-right">{fmtNum(p.spread_bps, 2)}</td>
              <td className="py-1 pr-2 text-right">
                {fmtUsd(p.bid_qty != null ? p.bid_qty * p.bid : null)} / {fmtUsd(p.ask_qty != null ? p.ask_qty * p.ask : null)}
              </td>
              <td className="py-1 pr-2 text-right">
                {fmtSigned(p.funding_apr_pct, 1, "%")} <span className="text-ink-muted">/{p.funding_interval_h}ч</span>
              </td>
              <td className="py-1 pr-2 text-right">{fmtUsd(p.open_interest_usd)}</td>
              <td className="py-1 text-right text-ink-muted">{p.age_sec.toFixed(1)}с</td>
            </tr>
          ))}
          {data.spots.map((s) => (
            <tr key={`spot-${s.venue}`} className="border-t border-line/50 text-ink-muted">
              <td className="py-1 pr-2 font-sans">{s.venue} spot</td>
              <td className="py-1 pr-2">{s.symbol}</td>
              <td className="py-1 pr-2 text-right">{fmtPrice(s.bid)}</td>
              <td className="py-1 pr-2 text-right">{fmtPrice(s.ask)}</td>
              <td colSpan={4} />
              <td className="py-1 text-right">{s.age_sec.toFixed(1)}с</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ k, label, sort, onSort }: { k: SortKey; label: string; sort: SortKey; onSort: (k: SortKey) => void }) {
  return (
    <th className="px-2 py-2 text-right font-medium">
      <button type="button" onClick={() => onSort(k)} className={`inline-flex items-center gap-1 ${sort === k ? "text-accent" : "hover:text-ink"}`}>
        {label}
        {sort === k && <ArrowDown className="h-3 w-3" />}
      </button>
    </th>
  );
}

export function ScreenerPage() {
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("cross_spread_bps");
  const [minVolume, setMinVolume] = useState(1_000_000);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    v1Get<{ rows: ScreenerRow[] }>("/screener", { sort, min_volume_usd: minVolume, limit: 500 })
      .then((d) => {
        setRows(d.rows);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [sort, minVolume]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const id = setInterval(load, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [load]);

  const q = search.trim().toUpperCase();
  const shown = q ? rows.filter((r) => r.base.includes(q)) : rows;

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Скринер</h1>
        <span className="text-xs text-ink-muted">все биржи в одной таблице · обновление каждые {POLL_MS / 1000} с</span>
        {error && <span className="text-xs text-rose-500">ошибка: {error}</span>}
        <div className="ml-auto flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1.5 text-ink-muted">
            объём ≥
            <select
              value={minVolume}
              onChange={(e) => setMinVolume(Number(e.target.value))}
              className="rounded border border-line bg-surface px-1.5 py-1 text-ink"
            >
              {[0, 100_000, 1_000_000, 10_000_000, 100_000_000].map((v) => (
                <option key={v} value={v}>
                  {v === 0 ? "любой" : fmtUsd(v)}
                </option>
              ))}
            </select>
          </label>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="монета"
            className="w-28 rounded border border-line bg-surface px-2 py-1 text-ink"
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface-elevated scroll-thin">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="border-b border-line text-left text-xs text-ink-muted">
            <tr>
              <th className="w-6 px-2 py-2" />
              <th className="px-2 py-2 font-medium">Монета</th>
              <th className="px-2 py-2 text-right font-medium">Бирж</th>
              <th className="px-2 py-2 font-medium">Лучший ask → bid</th>
              <Th k="cross_spread_bps" label="Кросс-спред, bps" sort={sort} onSort={setSort} />
              <Th k="funding_spread_apr_pct" label="Разброс funding, APR" sort={sort} onSort={setSort} />
              <Th k="max_basis_bps" label="Макс. базис, bps" sort={sort} onSort={setSort} />
              <Th k="volume_24h_usd" label="Объём 24ч" sort={sort} onSort={setSort} />
              <th className="px-2 py-2 text-right font-medium">OI</th>
              <th className="px-2 py-2 text-right font-medium">Сетапы</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-10 text-center text-sm text-ink-muted">
                  {error ? "Нет связи с бэкендом." : "Данные бирж ещё собираются…"}
                </td>
              </tr>
            )}
            {shown.map((r) => {
              const isOpen = open === r.base;
              return (
                <Fragment key={r.base}>
                  <tr onClick={() => setOpen(isOpen ? null : r.base)} className="cursor-pointer border-b border-line/60 hover:bg-accent/5">
                    <td className="px-2 py-1.5 text-ink-muted">
                      {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </td>
                    <td className="px-2 py-1.5 font-semibold">{r.base}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs">
                      {r.venues}
                      {r.spot_venues > 0 && <span className="text-ink-muted"> +{r.spot_venues}s</span>}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono text-xs">
                      {r.best_ask.venue} <span className="text-ink-muted">→</span> {r.best_bid.venue}
                    </td>
                    <td
                      className={`px-2 py-1.5 text-right font-mono text-xs tabular-nums ${r.cross_spread_bps > 0 ? "font-semibold text-emerald-600 dark:text-emerald-400" : "text-ink-muted"}`}
                    >
                      {fmtNum(r.cross_spread_bps, 1)}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums" title={r.funding_low && r.funding_high ? `${r.funding_low.venue} ${r.funding_low.apr_pct}% → ${r.funding_high.venue} ${r.funding_high.apr_pct}%` : ""}>
                      {fmtNum(r.funding_spread_apr_pct, 1, "%")}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums">{fmtSigned(r.max_basis_bps, 1)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs">{fmtUsd(r.volume_24h_usd)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs text-ink-muted">{fmtUsd(r.open_interest_usd)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs">
                      {r.active_setups > 0 ? <span className="rounded bg-accent/15 px-1.5 py-0.5 text-accent">{r.active_setups}</span> : ""}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-line bg-surface/60">
                      <td colSpan={10}>
                        <MarketDetail base={r.base} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
