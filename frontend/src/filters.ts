/**
 * Дублирует логику mexc_monitor/filters.py для клиентской фильтрации без повторных запросов к API.
 */
import type {
  CrossMarketRow,
  Exchange,
  Market,
  MarketRow,
  SnapshotRow,
} from "./types";
import { EXCHANGE_DEFAULT_QUOTE } from "./types";

const UI_LOG = import.meta.env.DEV;

export function isCrossMarketRow(r: unknown): r is CrossMarketRow {
  if (!r || typeof r !== "object") return false;
  const o = r as Record<string, unknown>;
  return (
    typeof o.symbol_spot === "string" &&
    typeof o.symbol_futures === "string"
  );
}

export function quoteSuffixForFilter(
  market: Market,
  raw: string,
  exchange: Exchange = "mexc",
): string {
  const s = String(raw ?? "")
    .trim()
    .toUpperCase();
  // Подчёркнутый формат символа (BTC_USDT) — только у MEXC futures.
  const mexcFutures = exchange === "mexc" && market === "futures";
  if (!s) {
    if (mexcFutures) return "_USDT";
    return EXCHANGE_DEFAULT_QUOTE[exchange] ?? "USDT";
  }
  if (mexcFutures && !s.startsWith("_")) return `_${s}`;
  return s;
}

export interface FilterOptions {
  market: Market;
  /** Биржа снимка; влияет на дефолтный суффикс котировки. По умолчанию MEXC. */
  exchange?: Exchange;
  quoteRaw: string;
  minSpreadBps: number;
  minVolQuote: number;
  search: string;
  sortBy: string;
  ascending: boolean;
  /**
   * Мин. нотация на лучшем bid (bid_qty × bid), USDT≈. 0 — не фильтровать.
   * Для режима отбора пар под сбор спреда с «плотностью» на touch bid.
   */
  minBidL1NotionalQuote: number;
  /** Мин. нотация на лучшем ask (ask_qty × ask), USDT≈. */
  minAskL1NotionalQuote: number;
}

function num(v: number | null | undefined): number {
  if (v == null || Number.isNaN(v)) return 0;
  return v;
}

function effectiveCrossSortKey(
  rows: CrossMarketRow[],
  sortBy: string,
): keyof CrossMarketRow {
  if (!rows.length) return "basis_mid_bps";
  const k = sortBy as keyof CrossMarketRow;
  if (k in rows[0]) return k;
  return "basis_mid_bps";
}

function effectiveSpotFutSortKey(
  rows: MarketRow[],
  sortBy: string,
): keyof MarketRow {
  if (!rows.length) return "spread_bps";
  const k = sortBy as keyof MarketRow;
  if (k in rows[0]) return k;
  return "spread_bps";
}

export function applyCrossMarketFilters(
  rows: CrossMarketRow[],
  o: FilterOptions,
): CrossMarketRow[] {
  const suffix = quoteSuffixForFilter("spot", o.quoteRaw);
  const search = String(o.search ?? "")
    .trim()
    .toUpperCase();

  let out = rows.filter((r) => {
    const spot = (r.symbol_spot ?? "").toUpperCase();
    const fut = (r.symbol_futures ?? "").toUpperCase();
    if (suffix && !spot.endsWith(suffix)) return false;
    if (o.minSpreadBps > 0 && Math.abs(num(r.basis_mid_bps)) < o.minSpreadBps)
      return false;
    if (
      o.minVolQuote > 0 &&
      (num(r.volume_24h_quote_spot) < o.minVolQuote ||
        num(r.volume_24h_quote_fut) < o.minVolQuote)
    )
      return false;
    if (search && !spot.includes(search) && !fut.includes(search)) return false;
    return true;
  });

  const key = effectiveCrossSortKey(out, o.sortBy);
  const dir = o.ascending ? 1 : -1;

  out = [...out].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return o.ascending ? 1 : -1;
    if (bv == null) return o.ascending ? -1 : 1;
    if (typeof av === "number" && typeof bv === "number") {
      if (av === bv)
        return String(a.symbol_spot ?? "").localeCompare(
          String(b.symbol_spot ?? ""),
        );
      return av < bv ? -dir : dir;
    }
    const as = String(av);
    const bs = String(bv);
    if (as === bs) return 0;
    return as < bs ? -dir : dir;
  });

  return out;
}

export function applyMarketFilters(
  rows: SnapshotRow[],
  o: FilterOptions,
): SnapshotRow[] {
  if (o.market === "cross") {
    const valid = rows.filter(isCrossMarketRow);
    if (valid.length !== rows.length && rows.length > 0 && UI_LOG) {
      console.warn(
        `[MEXC UI] cross: отброшено ${rows.length - valid.length} строк (нет symbol_spot/symbol_futures)`,
      );
    }
    return applyCrossMarketFilters(valid, o);
  }

  const suffix = quoteSuffixForFilter(o.market, o.quoteRaw, o.exchange);
  const search = String(o.search ?? "")
    .trim()
    .toUpperCase();

  let out = (rows as MarketRow[]).filter((r) => {
    const sym = String(r.symbol ?? "")
      .trim()
      .toUpperCase();
    if (!sym) return false;
    if (suffix && !sym.endsWith(suffix)) return false;
    if (o.minSpreadBps > 0 && num(r.spread_bps) < o.minSpreadBps) return false;
    if (o.minVolQuote > 0 && num(r.volume_24h_quote) < o.minVolQuote)
      return false;
    if (o.minBidL1NotionalQuote > 0) {
      const bn = num(r.bid_qty) * num(r.bid);
      if (bn < o.minBidL1NotionalQuote) return false;
    }
    if (o.minAskL1NotionalQuote > 0) {
      const an = num(r.ask_qty) * num(r.ask);
      if (an < o.minAskL1NotionalQuote) return false;
    }
    if (search && !sym.includes(search)) return false;
    return true;
  });

  const key = effectiveSpotFutSortKey(out, o.sortBy);
  const dir = o.ascending ? 1 : -1;

  out = [...out].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return o.ascending ? 1 : -1;
    if (bv == null) return o.ascending ? -1 : 1;
    if (typeof av === "number" && typeof bv === "number") {
      if (av === bv)
        return String(a.symbol ?? "").localeCompare(String(b.symbol ?? ""));
      return av < bv ? -dir : dir;
    }
    const as = String(av);
    const bs = String(bv);
    return as === bs ? 0 : as < bs ? -dir : dir;
  });

  return out;
}
