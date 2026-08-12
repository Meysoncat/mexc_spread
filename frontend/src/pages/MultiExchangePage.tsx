import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { apiUrl } from "../config";
import { EXCHANGE_GROUPS } from "../ExchangeSwitcher";
import {
  EXCHANGE_LABELS,
  type Exchange,
  type MarketRow,
} from "../types";
import { WithdrawalFeeCalculator } from "../WithdrawalFeeCalculator";
import { TableEmptyState } from "../components/ui/EmptyState";
import { baseFromSymbol, humanizeError } from "../lib/symbol";

/** Все биржи из переключателя (CEX + DEX). */
const ALL_EXCHANGES: { value: Exchange; label: string }[] =
  EXCHANGE_GROUPS.flatMap((g) => g.exchanges);

/** Метка биржи по строковому ключу (безопасно для произвольных строк). */
function label(ex: string): string {
  return EXCHANGE_LABELS[ex as Exchange] ?? ex;
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
  /** Кросс-спред за вычетом тейкер-комиссии обеих ног (bps). */
  netCrossSpreadBps: number | null;
  /** Меньший 24ч-объём из двух ног (bottleneck ликвидности), USDT. */
  minLegVolume: number;
}

function fmt(n: number | null | undefined, digits = 4): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

/** Одна сеть монеты на конкретной бирже. */
interface NetInfo {
  network: string;
  deposit: boolean;
  withdraw: boolean;
}
/** base → exchange → список сетей. */
type CoinNetworks = Record<string, Record<string, NetInfo[]>>;

/** Биржи с публичным currency-API (см. backend/coin_networks.py). */
const NET_SUPPORTED = new Set<Exchange>(["gateio", "bitget"]);

type TransferStatus = "ok" | "none" | "partial" | "unknown";
interface TransferInfo {
  status: TransferStatus;
  networks: string[];
  note?: string;
}

/**
 * Переводимость монеты по маршруту сделки: купить на bestAsk-бирже (вывести
 * оттуда) → продать на bestBid-бирже (внести туда). «Переводимо» = есть общая
 * сеть, где вывод с источника и депозит на приёмник одновременно доступны.
 * Для бирж без публичных данных (Binance/Bybit/OKX/MEXC) показываем сети
 * известной ноги и помечаем маршрут как непроверенный.
 */
function computeTransfer(row: CompareRow, nets: CoinNetworks): TransferInfo {
  const src = row.bestAsk.exchange; // откуда выводим (где купили)
  const dst = row.bestBid.exchange; // куда вносим (где продаём)
  const per = nets[row.base];
  if (!per) return { status: "unknown", networks: [] };

  const srcData = NET_SUPPORTED.has(src) ? per[src] : undefined;
  const dstData = NET_SUPPORTED.has(dst) ? per[dst] : undefined;
  const srcNets = (srcData ?? [])
    .filter((n) => n.withdraw)
    .map((n) => n.network);
  const dstNets = (dstData ?? []).filter((n) => n.deposit).map((n) => n.network);

  if (srcData && dstData) {
    const common = srcNets.filter((n) => dstNets.includes(n));
    return common.length
      ? { status: "ok", networks: common }
      : { status: "none", networks: [] };
  }
  if (srcData) {
    return {
      status: "partial",
      networks: srcNets,
      note: `Вывод с ${label(src)}; депозит на ${label(dst)} не проверен`,
    };
  }
  if (dstData) {
    return {
      status: "partial",
      networks: dstNets,
      note: `Депозит на ${label(dst)}; вывод с ${label(src)} не проверен`,
    };
  }
  return { status: "unknown", networks: [] };
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
  const [minVolM, setMinVolM] = useState(0);
  const [takerFeeBps, setTakerFeeBps] = useState(2);
  const [onlyTransferable, setOnlyTransferable] = useState(false);
  const [coinNetworks, setCoinNetworks] = useState<CoinNetworks>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(
    (exchanges: Exchange[], signal: AbortSignal) => {
      setLoading((prev) => {
        const next = { ...prev };
        for (const ex of exchanges) next[ex] = true;
        return next;
      });

      // Один запрос ко всем биржам параллельно на бэкенде
      const q = new URLSearchParams({
        exchanges: exchanges.join(","),
        market: "futures",
      });
      fetch(apiUrl(`/api/snapshot/multi?${q}`), { signal, ...({ signal } as RequestInit) })
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok || !data.results) return;
          for (const ex of exchanges) {
            const payload = data.results[ex];
            if (!payload) continue;
            if (payload.ok && Array.isArray(payload.rows)) {
              const rows = (payload.rows as MarketRow[])
                .filter((row) => row.bid > 0 && row.ask > 0)
                .map((row) => ({
                  exchange: ex,
                  symbol: row.symbol,
                  bid: row.bid,
                  ask: row.ask,
                  mid: row.mid,
                  spread_bps: row.spread_bps,
                  volume_24h_quote: row.volume_24h_quote,
                  funding_rate: row.funding_rate,
                }));
              setByExchange((prev) => ({ ...prev, [ex]: rows }));
              setErrors((prev) => ({ ...prev, [ex]: undefined }));
            } else {
              setErrors((prev) => ({ ...prev, [ex]: payload.error ?? "Ошибка" }));
            }
            setLoading((prev) => ({ ...prev, [ex]: false }));
          }
        })
        .catch((e: unknown) => {
          if (e instanceof DOMException && e.name === "AbortError") return;
          for (const ex of exchanges) {
            setErrors((prev) => ({ ...prev, [ex]: e instanceof Error ? e.message : String(e) }));
            setLoading((prev) => ({ ...prev, [ex]: false }));
          }
        });
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
      // Обе ноги пересекаем тейкером → вычитаем 2× комиссию (buy ask + sell bid).
      const netCrossSpreadBps =
        crossSpreadBps == null ? null : crossSpreadBps - 2 * takerFeeBps;
      // Узкое место ликвидности — меньший объём из двух исполняемых ног.
      const minLegVolume = Math.min(
        bestBid.volume_24h_quote || 0,
        bestAsk.volume_24h_quote || 0,
      );
      out.push({
        base,
        quotes,
        bestBid,
        bestAsk,
        crossSpreadBps,
        netCrossSpreadBps,
        minLegVolume,
      });
    }
    out.sort(
      (a, b) =>
        (b.netCrossSpreadBps ?? -Infinity) - (a.netCrossSpreadBps ?? -Infinity),
    );
    return out;
  }, [byExchange, selected, takerFeeBps]);

  // Список монет, для которых имеет смысл тянуть сети: обе ноги — разные биржи
  // и хотя бы одна из них поддерживается (иначе результат заведомо «?»).
  const netBasesKey = useMemo(() => {
    const bases = rows
      .filter(
        (r) =>
          r.bestAsk.exchange !== r.bestBid.exchange &&
          (NET_SUPPORTED.has(r.bestAsk.exchange) ||
            NET_SUPPORTED.has(r.bestBid.exchange)),
      )
      .slice(0, 300)
      .map((r) => r.base);
    return Array.from(new Set(bases)).sort().join(",");
  }, [rows]);

  useEffect(() => {
    if (!netBasesKey) return;
    const ac = new AbortController();
    const q = new URLSearchParams({ coins: netBasesKey });
    fetch(apiUrl(`/api/coin-networks?${q}`), { signal: ac.signal })
      .then((r) => r.json())
      .then((data) => {
        if (data?.ok && data.coins) setCoinNetworks(data.coins as CoinNetworks);
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
      });
    return () => ac.abort();
  }, [netBasesKey]);

  const transferByBase = useMemo(() => {
    const map = new Map<string, TransferInfo>();
    for (const r of rows) map.set(r.base, computeTransfer(r, coinNetworks));
    return map;
  }, [rows, coinNetworks]);

  const filtered = useMemo(() => {
    const s = search.trim().toUpperCase();
    const minVol = minVolM * 1_000_000;
    return rows.filter(
      (r) =>
        (!s || r.base.includes(s)) &&
        (minCrossBps <= 0 || (r.crossSpreadBps ?? -Infinity) >= minCrossBps) &&
        (minVol <= 0 || r.minLegVolume >= minVol) &&
        (!onlyTransferable ||
          transferByBase.get(r.base)?.status === "ok"),
    );
  }, [rows, search, minCrossBps, minVolM, onlyTransferable, transferByBase]);

  const anyLoading = selected.some((ex) => loading[ex]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-3 md:p-6">
      <div>
        <h1 className="text-lg font-semibold text-ink">
          Кросс-биржевой скринер
        </h1>
        <p className="mt-1 text-xs text-ink-muted">
          Фьючерсные пары минимум на двух биржах, отсортированные по net
          кросс-спреду. Кросс-спред = купить по лучшему ask и продать п�� лучшему
          bid на другой бирже; net = за вычетом тейкер-комиссии обеих ног.
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
                  ? "bg-accent text-accent-foreground shadow"
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
                  <span className="font-medium">{label(ex)}</span>
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
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          Мин. объём ноги ($M)
          <input
            type="number"
            min={0}
            step={1}
            value={minVolM || ""}
            onChange={(e) => setMinVolM(Number(e.target.value) || 0)}
            className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          Тейкер-комиссия (bps/сторона)
          <input
            type="number"
            min={0}
            step={0.5}
            value={takerFeeBps || ""}
            onChange={(e) => setTakerFeeBps(Number(e.target.value) || 0)}
            className="w-20 rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={onlyTransferable}
            onChange={(e) => setOnlyTransferable(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          Только переводимые
        </label>
        <span className="text-xs text-ink-muted">
          Совпадений: <span className="font-mono">{filtered.length}</span>
        </span>
      </div>

      <WithdrawalFeeCalculator />

      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-line bg-surface-elevated">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-surface-elevated text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-3 py-2">Символ</th>
              <th className="px-3 py-2">Бирж</th>
              <th className="px-3 py-2">Лучший bid</th>
              <th className="px-3 py-2">Лучший ask</th>
              <th className="px-3 py-2">Объём ноги</th>
              <th className="px-3 py-2">Кросс-спред (bps)</th>
              <th className="px-3 py-2">Net (bps)</th>
              <th className="px-3 py-2">Переводимо: сеть</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 300).map((r) => (
              <MultiExchangeRow
                key={r.base}
                row={r}
                transfer={transferByBase.get(r.base)}
                expanded={expanded === r.base}
                onToggle={() =>
                  setExpanded((prev) => (prev === r.base ? null : r.base))
                }
                onOpenHub={() => navigate(`/coin/${r.base}`)}
              />
            ))}
            {filtered.length === 0 &&
              (anyLoading ? (
                <TableEmptyState
                  colSpan={8}
                  variant="loading"
                  title="Загрузка…"
                  description="Собираем котировки с выбранных бирж."
                  compact
                />
              ) : rows.length === 0 ? (
                <TableEmptyState
                  colSpan={8}
                  title="Нет общих пар"
                  description="Ни одна пара не присутствует минимум на двух выбранных биржах. Добавьте биржи выше или нажмите «Обновить»."
                  compact
                />
              ) : (
                <TableEmptyState
                  colSpan={8}
                  variant="empty"
                  title="Ничего не найдено"
                  description="Под фильтры не попала ни одна пара — ослабьте поиск, мин. кросс-спред или снимите «Только переводимые»."
                  compact
                />
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmtVol(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function MultiExchangeRow({
  row,
  transfer,
  expanded,
  onToggle,
  onOpenHub,
}: {
  row: CompareRow;
  transfer?: TransferInfo;
  expanded: boolean;
  onToggle: () => void;
  onOpenHub: () => void;
}) {
  const positive = (row.crossSpreadBps ?? 0) > 0;
  const netPositive = (row.netCrossSpreadBps ?? 0) > 0;
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-t border-line/60 transition hover:bg-accent/5"
      >
        <td className="px-3 py-2 font-mono font-medium text-ink">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenHub();
            }}
            className="hover:text-accent"
            title="Открыть страницу монеты"
          >
            {row.base}
          </button>
        </td>
        <td className="px-3 py-2 font-mono text-ink-muted">
          {row.quotes.length}
        </td>
        <td className="px-3 py-2 font-mono">
          {fmt(row.bestBid.bid)}{" "}
          <span className="text-xs text-ink-muted">
            {label(row.bestBid.exchange)}
          </span>
        </td>
        <td className="px-3 py-2 font-mono">
          {fmt(row.bestAsk.ask)}{" "}
          <span className="text-xs text-ink-muted">
            {label(row.bestAsk.exchange)}
          </span>
        </td>
        <td className="px-3 py-2 font-mono text-ink-muted">
          {fmtVol(row.minLegVolume)}
        </td>
        <td
          className={`px-3 py-2 font-mono ${
            positive ? "text-ink" : "text-ink-muted"
          }`}
        >
          {fmt(row.crossSpreadBps, 2)}
        </td>
        <td
          className={`px-3 py-2 font-mono font-semibold ${
            netPositive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-red-600 dark:text-red-400"
          }`}
        >
          {fmt(row.netCrossSpreadBps, 2)}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-line/40 bg-surface">
          <td colSpan={7} className="px-3 py-2">
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
                        {label(q.exchange)}
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
