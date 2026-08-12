import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { apiUrl } from "../config";
import {
  EXCHANGE_LABELS,
  type Exchange,
  type MarketRow,
  type ChartInterval,
} from "../types";
import { TradingChart } from "../components/charts/TradingChart";
import { baseFromSymbol, humanizeError } from "../lib/symbol";

const HUB_EXCHANGES: Exchange[] = [
  "mexc",
  "binance",
  "bybit",
  "okx",
  "gateio",
  "bitget",
];

const INTERVALS: { value: ChartInterval; label: string }[] = [
  { value: "15m", label: "15м" },
  { value: "1h", label: "1ч" },
  { value: "4h", label: "4ч" },
  { value: "1d", label: "1д" },
];

interface Quote {
  exchange: Exchange;
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread_bps: number | null;
  volume_24h_quote: number;
  funding_rate: number | null;
}

function label(ex: string): string {
  return EXCHANGE_LABELS[ex as Exchange] ?? ex;
}

function fmt(n: number | null | undefined, digits = 4): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

/** One KPI tile in the header row. */
function Kpi({
  title,
  value,
  sub,
  tone = "default",
}: {
  title: string;
  value: string;
  sub?: string;
  tone?: "default" | "up" | "down";
}) {
  const valueColor =
    tone === "up"
      ? "text-emerald-500"
      : tone === "down"
        ? "text-red-500"
        : "text-ink";
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-line bg-surface-elevated px-4 py-3">
      <span className="text-[10px] font-medium uppercase tracking-wide text-ink-muted">
        {title}
      </span>
      <span className={`font-mono text-lg font-semibold ${valueColor}`}>
        {value}
      </span>
      {sub && <span className="text-[11px] text-ink-muted">{sub}</span>}
    </div>
  );
}

export function CoinHubPage() {
  const { base = "" } = useParams<{ base: string }>();
  const navigate = useNavigate();
  const baseUpper = base.toUpperCase();

  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [errors, setErrors] = useState<Partial<Record<Exchange, string>>>({});
  const [loading, setLoading] = useState(false);
  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [nonce, setNonce] = useState(0);
  const [positionUsd, setPositionUsd] = useState(1000);
  const [takerFeeBps, setTakerFeeBps] = useState(2);

  const load = useCallback(
    (signal: AbortSignal) => {
      setLoading(true);
      const q = new URLSearchParams({
        exchanges: HUB_EXCHANGES.join(","),
        market: "futures",
      });
      fetch(apiUrl(`/api/snapshot/multi?${q}`), { signal })
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok || !data.results) return;
          const collected: Quote[] = [];
          const errs: Partial<Record<Exchange, string>> = {};
          for (const ex of HUB_EXCHANGES) {
            const payload = data.results[ex];
            if (!payload) continue;
            if (payload.ok && Array.isArray(payload.rows)) {
              for (const row of payload.rows as MarketRow[]) {
                if (row.bid <= 0 || row.ask <= 0) continue;
                if (baseFromSymbol(row.symbol) !== baseUpper) continue;
                collected.push({
                  exchange: ex,
                  symbol: row.symbol,
                  bid: row.bid,
                  ask: row.ask,
                  mid: row.mid,
                  spread_bps: row.spread_bps,
                  volume_24h_quote: row.volume_24h_quote,
                  funding_rate: row.funding_rate,
                });
              }
            } else if (payload.error) {
              errs[ex] = payload.error;
            }
          }
          setQuotes(collected);
          setErrors(errs);
        })
        .catch((e: unknown) => {
          if (e instanceof DOMException && e.name === "AbortError") return;
          setErrors({ mexc: e instanceof Error ? e.message : String(e) });
        })
        .finally(() => setLoading(false));
    },
    [baseUpper],
  );

  useEffect(() => {
    const ac = new AbortController();
    load(ac.signal);
    return () => ac.abort();
  }, [load, nonce]);

  // Best bid (highest) and best ask (lowest) across exchanges → cross-spread.
  const stats = useMemo(() => {
    if (quotes.length === 0) return null;
    let bestBid = quotes[0];
    let bestAsk = quotes[0];
    let totalVol = 0;
    for (const q of quotes) {
      if (q.bid > bestBid.bid) bestBid = q;
      if (q.ask < bestAsk.ask) bestAsk = q;
      totalVol += q.volume_24h_quote || 0;
    }
    const crossSpreadBps =
      bestAsk.ask > 0 && bestBid.exchange !== bestAsk.exchange
        ? (10_000 * (bestBid.bid - bestAsk.ask)) / bestAsk.ask
        : null;
    return { bestBid, bestAsk, crossSpreadBps, totalVol };
  }, [quotes]);

  // Trade-card economics: buy on best-ask venue, sell on best-bid venue.
  // Net edge = gross cross-spread minus taker fee on both legs (bps).
  const trade = useMemo(() => {
    if (!stats || stats.crossSpreadBps == null) return null;
    const grossBps = stats.crossSpreadBps;
    const feeBps = 2 * takerFeeBps;
    const netBps = grossBps - feeBps;
    const notional = Math.max(0, positionUsd);
    const grossPnl = (notional * grossBps) / 10_000;
    const feeCost = (notional * feeBps) / 10_000;
    const netPnl = (notional * netBps) / 10_000;
    return {
      buyVenue: stats.bestAsk.exchange,
      buyPrice: stats.bestAsk.ask,
      sellVenue: stats.bestBid.exchange,
      sellPrice: stats.bestBid.bid,
      grossBps,
      feeBps,
      netBps,
      notional,
      grossPnl,
      feeCost,
      netPnl,
      qty: stats.bestAsk.ask > 0 ? notional / stats.bestAsk.ask : 0,
    };
  }, [stats, positionUsd, takerFeeBps]);

  // Chart uses the most liquid venue's symbol; fall back to BASEUSDT.
  const chartSymbol = useMemo(() => {
    if (quotes.length === 0) return `${baseUpper}USDT`;
    const mostLiquid = [...quotes].sort(
      (a, b) => (b.volume_24h_quote || 0) - (a.volume_24h_quote || 0),
    )[0];
    // Normalize MEXC futures underscore form for the klines endpoint.
    return mostLiquid.symbol.replace(/[_\-/]/g, "");
  }, [quotes, baseUpper]);

  const sortedQuotes = useMemo(
    () => [...quotes].sort((a, b) => b.bid - a.bid),
    [quotes],
  );

  const activeErrors = Object.entries(errors).filter(([, msg]) => msg);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-3 md:p-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-ink-muted transition hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
          Назад
        </button>
        <h1 className="font-mono text-2xl font-semibold text-ink">
          {baseUpper}
        </h1>
        <span className="text-xs text-ink-muted">
          Фьючерсы · {quotes.length}{" "}
          {quotes.length === 1 ? "биржа" : "бирж"}
        </span>
        <button
          onClick={() => setNonce((n) => n + 1)}
          disabled={loading}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-surface-elevated disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Обновить
        </button>
      </div>

      {/* KPI row */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Kpi
            title="Лучший bid"
            value={fmt(stats.bestBid.bid)}
            sub={label(stats.bestBid.exchange)}
            tone="up"
          />
          <Kpi
            title="Лучший ask"
            value={fmt(stats.bestAsk.ask)}
            sub={label(stats.bestAsk.exchange)}
            tone="down"
          />
          <Kpi
            title="Кросс-спред"
            value={
              stats.crossSpreadBps == null
                ? "—"
                : `${stats.crossSpreadBps.toFixed(2)} bps`
            }
            sub={
              stats.crossSpreadBps != null
                ? `${label(stats.bestAsk.exchange)} → ${label(stats.bestBid.exchange)}`
                : "одна биржа"
            }
            tone={
              stats.crossSpreadBps != null && stats.crossSpreadBps > 0
                ? "up"
                : "default"
            }
          />
          <Kpi title="Объём 24ч" value={fmtUsd(stats.totalVol)} sub="сумма бирж" />
          <Kpi
            title="Медиана mid"
            value={fmt(
              [...quotes].map((q) => q.mid).sort((a, b) => a - b)[
                Math.floor(quotes.length / 2)
              ],
            )}
            sub={`${quotes.length} котировок`}
          />
        </div>
      )}

      {/* Errors */}
      {activeErrors.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <p className="font-medium">Часть бирж не ответила:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {activeErrors.map(([ex, msg]) => (
              <li key={ex}>
                <span className="font-medium">{label(ex)}</span>:{" "}
                {humanizeError(String(msg))}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Trade card — cross-exchange arb economics */}
      {trade && (
        <div className="rounded-xl border border-line bg-surface-elevated p-4">
          <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2">
            <h2 className="text-sm font-semibold text-ink">Карточка сделки</h2>
            <label className="flex items-center gap-2 text-xs text-ink-muted">
              Размер позиции ($)
              <input
                type="number"
                min={0}
                step={100}
                value={positionUsd || ""}
                onChange={(e) => setPositionUsd(Number(e.target.value) || 0)}
                className="w-28 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-ink-muted">
              Тейкер (bps/сторона)
              <input
                type="number"
                min={0}
                step={0.5}
                value={takerFeeBps || ""}
                onChange={(e) => setTakerFeeBps(Number(e.target.value) || 0)}
                className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {/* Legs */}
            <div className="flex flex-col gap-2 rounded-lg border border-line bg-surface p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-emerald-500">Купить (long)</span>
                <span className="text-ink-muted">{label(trade.buyVenue)}</span>
              </div>
              <div className="flex items-center justify-between font-mono">
                <span className="text-ink">{fmt(trade.buyPrice)}</span>
                <span className="text-ink-muted">
                  ≈ {fmt(trade.qty, 4)} {baseUpper}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between border-t border-line/60 pt-2">
                <span className="text-red-500">Продать (short)</span>
                <span className="text-ink-muted">{label(trade.sellVenue)}</span>
              </div>
              <div className="flex items-center justify-between font-mono">
                <span className="text-ink">{fmt(trade.sellPrice)}</span>
                <span className="text-ink-muted">
                  {fmtUsd(trade.notional)}
                </span>
              </div>
            </div>

            {/* Economics */}
            <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-surface p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-ink-muted">Грязный спред</span>
                <span className="font-mono text-ink">
                  {trade.grossBps.toFixed(2)} bps ·{" "}
                  {fmtUsd(trade.grossPnl)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-ink-muted">
                  Комиссия ({trade.feeBps.toFixed(1)} bps)
                </span>
                <span className="font-mono text-red-500">
                  −{fmtUsd(trade.feeCost)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between border-t border-line/60 pt-2">
                <span className="font-medium text-ink">Чистый PnL</span>
                <span
                  className={`font-mono text-base font-semibold ${
                    trade.netPnl >= 0 ? "text-emerald-500" : "text-red-500"
                  }`}
                >
                  {trade.netPnl >= 0 ? "+" : ""}
                  {fmtUsd(trade.netPnl)} · {trade.netBps.toFixed(2)} bps
                </span>
              </div>
              {trade.netBps <= 0 && (
                <p className="text-[11px] text-red-500">
                  Комиссия съедает спред — сделка нерентабельна при этой ставке.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Chart */}
      <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface-elevated p-3">
        <div className="flex items-center justify-between">
          <span className="font-mono text-sm text-ink">{chartSymbol}</span>
          <div className="flex rounded-lg border border-line">
            {INTERVALS.map((iv) => (
              <button
                key={iv.value}
                onClick={() => setInterval(iv.value)}
                className={`px-2.5 py-1 text-xs font-medium transition ${
                  interval === iv.value
                    ? "bg-accent text-accent-foreground"
                    : "text-ink-muted hover:bg-surface"
                }`}
              >
                {iv.label}
              </button>
            ))}
          </div>
        </div>
        <div className="h-[300px]">
          <TradingChart
            symbol={chartSymbol}
            market="futures"
            interval={interval}
            visual="candle"
            className="h-full w-full"
          />
        </div>
      </div>

      {/* Per-exchange table */}
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-line bg-surface-elevated">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Биржа</th>
              <th className="px-3 py-2 font-medium">Символ</th>
              <th className="px-3 py-2 text-right font-medium">Bid</th>
              <th className="px-3 py-2 text-right font-medium">Ask</th>
              <th className="px-3 py-2 text-right font-medium">Спред bps</th>
              <th className="px-3 py-2 text-right font-medium">Объём 24ч</th>
              <th className="px-3 py-2 text-right font-medium">Funding</th>
            </tr>
          </thead>
          <tbody>
            {sortedQuotes.map((q) => (
              <tr
                key={`${q.exchange}:${q.symbol}`}
                className="border-t border-line/60 transition hover:bg-accent/5"
              >
                <td className="px-3 py-2 font-medium text-ink">
                  {label(q.exchange)}
                  {stats?.bestBid.exchange === q.exchange && (
                    <span className="ml-1.5 text-emerald-500">bid★</span>
                  )}
                  {stats?.bestAsk.exchange === q.exchange && (
                    <span className="ml-1.5 text-sky-500">ask★</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-ink-muted">{q.symbol}</td>
                <td className="px-3 py-2 text-right font-mono text-ink">
                  {fmt(q.bid)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-ink">
                  {fmt(q.ask)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-ink-muted">
                  {fmt(q.spread_bps, 2)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-ink-muted">
                  {fmtUsd(q.volume_24h_quote)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-ink-muted">
                  {q.funding_rate != null
                    ? `${(q.funding_rate * 100).toFixed(4)}%`
                    : "—"}
                </td>
              </tr>
            ))}
            {quotes.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-10 text-center text-ink-muted"
                >
                  {loading
                    ? "Загрузка котировок…"
                    : `Ни одна из бирж не торгует ${baseUpper} фьючерсами (или данные недоступны).`}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
