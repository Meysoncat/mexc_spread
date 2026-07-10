import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { apiUrl } from "../config";
import { EXCHANGE_GROUPS } from "../ExchangeSwitcher";
import type { Exchange, MarketRow, SnapshotResponse } from "../types";

/** Все биржи из переключателя (CEX + DEX). */
const ALL_EXCHANGES: { value: Exchange; label: string }[] =
  EXCHANGE_GROUPS.flatMap((g) => g.exchanges);

const EXCHANGE_LABELS: Record<string, string> = Object.fromEntries(
  ALL_EXCHANGES.map((e) => [e.value, e.label]),
);

/** BTCUSDT / BTC_USDT / BTCUSD → BTC (общий ключ сопоставления между биржами). */
function baseFromSymbol(symbol: string): string | null {
  const s = symbol.trim().toUpperCase().replace(/[_\-/]/g, "");
  for (const q of ["USDT", "USDC", "USD"]) {
    if (s.endsWith(q) && s.length > q.length) return s.slice(0, -q.length);
  }
  return null;
}

interface ExchangeQuote {
  exchange: Exchange;
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread_bps: number | null;
  volume_24h_quote: number;
  funding_rate: number | null;
}

interface CompareRow {
  base: string;
  quotes: ExchangeQuote[];
  bestBid: ExchangeQuote;
  bestAsk: ExchangeQuote;
  /** Купить по лучшему ask, продать по лучшему bid на другой бирже (bps). */
  crossSpreadBps: number | null;
}

/** 0.6: технические ошибки → понятное объяснение для пользователя. */
function humanizeError(msg: string): string {
  const m = msg.trim();
  if (/HTTP 403/i.test(m))
    return "биржа отклонила запрос (403) — возможна гео-блокировка, попробуйте VPN";
  if (/HTTP 429/i.test(m))
    return "слишком много запросов (429) — подождите минуту и обновите";
  if (/HTTP 5\d\d/i.test(m))
    return `биржа временно недоступна (${m}) — повторите позже`;
  if (/HTTP 4\d\d/i.test(m)) return `запрос отклонён (${m})`;
  if (/failed to fetch|networkerror|load failed/i.test(m))
    return "нет связи с бэкендом — проверьте, что сервер запущен";
  if (/timeout|timed?\s?out/i.test(m))
    return "биржа не ответила вовремя — попробуйте обновить";
  if (/нет данных/i.test(m))
    return "биржа вернула пустой ответ — возможно, рынок не поддерживается";
  return m;
}

function fmt(n: number | null | undefined, digits = 4): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

async function fetchExchangeRows(
  exchange: Exchange,
  signal: AbortSignal,
): Promise<ExchangeQuote[]> {
  const q = new URLSearchParams({ market: "futures", exchange });
  const r = await fetch(apiUrl(`/api/snapshot?${q}`), { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = (await r.json()) as SnapshotResponse;
  if (!data.ok || !Array.isArray(data.rows)) {
    throw new Error(data.error ?? "нет данных");
  }
  return (data.rows as MarketRow[])
    .filter((row) => row.bid > 0 && row.ask > 0)
    .map((row) => ({
      exchange,
      symbol: row.symbol,
      bid: row.bid,
      ask: row.ask,
      mid: row.mid,
      spread_bps: row.spread_bps,
      volume_24h_quote: row.volume_24h_quote,
      funding_rate: row.funding_rate,
    }));
}

export function MultiExchangePage() {
  const [selected, setSelected] = useState<Exchange[]>([
    "mexc",
    "binance",
    "bybit",
    "okx",
    "gateio",
    "bitget",
  ]);
  const [byExchange, setByExchange] = useState<
    Partial<Record<Exchange, ExchangeQuote[]>>
  >({});
  const [errors, setErrors] = useState<Partial<Record<Exchange, string>>>({});
  const [loading, setLoading] = useState<Partial<Record<Exchange, boolean>>>(
    {},
  );
  const [search, setSearch] = useState("");
  const [minCrossBps, setMinCrossBps] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(
    (exchanges: Exchange[], signal: AbortSignal) => {
      setLoading((prev) => {
        const next = { ...prev };
        for (const ex of exchanges) next[ex] = true;
        return next;
      });
      for (const ex of exchanges) {
        fetchExchangeRows(ex, signal)
          .then((rows) => {
            setByExchange((prev) => ({ ...prev, [ex]: rows }));
            setErrors((prev) => ({ ...prev, [ex]: undefined }));
          })
          .catch((e: unknown) => {
            if (e instanceof DOMException && e.name === "AbortError") return;
            setErrors((prev) => ({
              ...prev,
              [ex]: e instanceof Error ? e.message : String(e),
            }));
          })
          .finally(() => {
            setLoading((prev) => ({ ...prev, [ex]: false }));
          });
      }
    },
    [],
  );

  useEffect(() => {
    const ac = new AbortController();
    load(selected, ac.signal);
    return () => ac.abort();
  }, [selected, load]);

  const rows = useMemo<CompareRow[]>(() => {
    const byBase = new Map<string, ExchangeQuote[]>();
    for (const ex of selected) {
      for (const q of byExchange[ex] ?? []) {
        const base = baseFromSymbol(q.symbol);
        if (!base) continue;
        const list = byBase.get(base);
        if (list) list.push(q);
        else byBase.set(base, [q]);
      }
    }
    const out: CompareRow[] = [];
    for (const [base, quotes] of byBase) {
      if (quotes.length < 2) continue;
      let bestBid = quotes[0];
      let bestAsk = quotes[0];
      for (const q of quotes) {
        if (q.bid > bestBid.bid) bestBid = q;
        if (q.ask < bestAsk.ask) bestAsk = q;
      }
      // Одинаковый тикер ≠ одинаковый актив: если mid расходится в разы —
      // это, скорее всего, разные токены, кросс-спред не считаем.
      let minMid = Infinity;
      let maxMid = 0;
      for (const q of quotes) {
        if (q.mid > 0) {
          if (q.mid < minMid) minMid = q.mid;
          if (q.mid > maxMid) maxMid = q.mid;
        }
      }
      const sameAsset = minMid > 0 && maxMid / minMid < 2;
      const crossSpreadBps =
        sameAsset && bestAsk.ask > 0 && bestBid.exchange !== bestAsk.exchange
          ? (10_000 * (bestBid.bid - bestAsk.ask)) / bestAsk.ask
          : null;
      out.push({ base, quotes, bestBid, bestAsk, crossSpreadBps });
    }
    out.sort(
      (a, b) => (b.crossSpreadBps ?? -Infinity) - (a.crossSpreadBps ?? -Infinity),
    );
    return out;
  }, [byExchange, selected]);

  const filtered = useMemo(() => {
    const s = search.trim().toUpperCase();
    return rows.filter(
      (r) =>
        (!s || r.base.includes(s)) &&
        (minCrossBps <= 0 || (r.crossSpreadBps ?? -Infinity) >= minCrossBps),
    );
  }, [rows, search, minCrossBps]);

  const anyLoading = selected.some((ex) => loading[ex]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
      <div>
        <h1 className="text-lg font-semibold text-ink">
          Мультибиржа: сравнение по символу
        </h1>
        <p className="mt-1 text-xs text-ink-muted">
          Фьючерсные пары, присутствующие минимум на двух биржах. Кросс-спред =
          купить по лучшему ask и продать по лучшему bid на другой бирже.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {ALL_EXCHANGES.map(({ value, label }) => {
          const isOn = selected.includes(value);
          return (
            <button
              key={value}
              type="button"
              onClick={() =>
                setSelected((prev) =>
                  isOn ? prev.filter((e) => e !== value) : [...prev, value],
                )
              }
              className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                isOn
                  ? "bg-accent text-white shadow"
                  : "bg-surface text-ink-muted ring-1 ring-line hover:text-ink"
              }`}
            >
              {label}
              {loading[value] ? "…" : ""}
              {errors[value] ? " ⚠" : ""}
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => load(selected, new AbortController().signal)}
          disabled={anyLoading}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-surface-elevated disabled:opacity-50"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${anyLoading ? "animate-spin" : ""}`}
          />
          Обновить
        </button>
      </div>

      {Object.entries(errors).filter(([ex, msg]) => msg && selected.includes(ex as Exchange))
        .length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <p className="font-medium">Часть бирж не ответила — сравнение построено без них:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {Object.entries(errors)
              .filter(([ex, msg]) => msg && selected.includes(ex as Exchange))
              .map(([ex, msg]) => (
                <li key={ex}>
                  <span className="font-medium">{EXCHANGE_LABELS[ex] ?? ex}</span>
                  : {humanizeError(String(msg))}
                </li>
              ))}
          </ul>
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
          Мин. кросс-спред (bps)
          <input
            type="number"
            min={0}
            step={0.5}
            value={minCrossBps || ""}
            onChange={(e) => setMinCrossBps(Number(e.target.value) || 0)}
            className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </label>
        <span className="text-xs text-ink-muted">
          Совпадений: <span className="font-mono">{filtered.length}</span>
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-line bg-surface-elevated">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-3 py-2">Символ</th>
              <th className="px-3 py-2">Бирж</th>
              <th className="px-3 py-2">Лучший bid</th>
              <th className="px-3 py-2">Лучший ask</th>
              <th className="px-3 py-2">Кросс-спред (bps)</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 300).map((r) => (
              <MultiExchangeRow
                key={r.base}
                row={r}
                expanded={expanded === r.base}
                onToggle={() =>
                  setExpanded((prev) => (prev === r.base ? null : r.base))
                }
              />
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-8 text-center text-ink-muted">
                  {anyLoading
                    ? "Загрузка…"
                    : rows.length === 0
                      ? "Нет пар, присутствующих минимум на двух из выбранных бирж — добавьте биржи выше или нажмите «Обновить»"
                      : "Под фильтры не попала ни одна пара — ослабьте поиск или мин. кросс-спред"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MultiExchangeRow({
  row,
  expanded,
  onToggle,
}: {
  row: CompareRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const positive = (row.crossSpreadBps ?? 0) > 0;
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-t border-line/60 transition hover:bg-accent/5"
      >
        <td className="px-3 py-2 font-mono font-medium text-ink">{row.base}</td>
        <td className="px-3 py-2 font-mono text-ink-muted">
          {row.quotes.length}
        </td>
        <td className="px-3 py-2 font-mono">
          {fmt(row.bestBid.bid)}{" "}
          <span className="text-xs text-ink-muted">
            {EXCHANGE_LABELS[row.bestBid.exchange]}
          </span>
        </td>
        <td className="px-3 py-2 font-mono">
          {fmt(row.bestAsk.ask)}{" "}
          <span className="text-xs text-ink-muted">
            {EXCHANGE_LABELS[row.bestAsk.exchange]}
          </span>
        </td>
        <td
          className={`px-3 py-2 font-mono font-semibold ${
            positive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-ink-muted"
          }`}
        >
          {fmt(row.crossSpreadBps, 2)}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-line/40 bg-surface">
          <td colSpan={5} className="px-3 py-2">
            <table className="w-full text-xs">
              <thead className="text-ink-muted">
                <tr>
                  <th className="px-2 py-1 text-left">Биржа</th>
                  <th className="px-2 py-1 text-left">Символ</th>
                  <th className="px-2 py-1 text-right">Bid</th>
                  <th className="px-2 py-1 text-right">Ask</th>
                  <th className="px-2 py-1 text-right">Спред (bps)</th>
                  <th className="px-2 py-1 text-right">Объём 24h</th>
                  <th className="px-2 py-1 text-right">Funding</th>
                </tr>
              </thead>
              <tbody>
                {[...row.quotes]
                  .sort((a, b) => b.bid - a.bid)
                  .map((q) => (
                    <tr key={q.exchange} className="border-t border-line/40">
                      <td className="px-2 py-1 font-medium text-ink">
                        {EXCHANGE_LABELS[q.exchange]}
                        {q.exchange === row.bestBid.exchange && (
                          <span className="ml-1 text-emerald-600 dark:text-emerald-400">
                            bid★
                          </span>
                        )}
                        {q.exchange === row.bestAsk.exchange && (
                          <span className="ml-1 text-sky-600 dark:text-sky-400">
                            ask★
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1 font-mono">{q.symbol}</td>
                      <td className="px-2 py-1 text-right font-mono">
                        {fmt(q.bid)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">
                        {fmt(q.ask)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">
                        {fmt(q.spread_bps, 2)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">
                        {fmt(q.volume_24h_quote, 0)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">
                        {q.funding_rate != null
                          ? `${fmt(q.funding_rate * 100, 4)}%`
                          : "—"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
