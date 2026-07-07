import {
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import {
  Activity,
  ArrowUpDown,
  ChartCandlestick,
  ChevronDown,
  ChevronUp,
  Download,
  ChartLine,
  LayoutGrid,
  LayoutList,
  List,
  Maximize2,
  Moon,
  RefreshCw,
  Star,
  Sun,
  X,
  Zap,
} from "lucide-react";
import {
  AssetWorkspaceModal,
  type WorkspaceOpenContext,
} from "../AssetWorkspaceModal";
import { ChartModal } from "../ChartModal";
import { DomModal } from "../DomModal";
import { MetricsHelpPanel } from "../MetricsHelpPanel";
import { SpreadChartModal } from "../SpreadChartModal";
import { apiUrl } from "../config";
import { MiniSparkline } from "../MiniSparkline";
import { InlineSpreadTrend } from "../InlineSpreadTrend";
import { applyMarketFilters } from "../filters";
import {
  clearFavoritesMarket,
  favoriteKeyForRow,
  readFavoriteSet,
  readFavoritesScope,
  readFavoritesSorted,
  removeFavoriteKey,
  toggleFavoriteKey,
  writeFavoritesScope,
  type FavoritesScope,
} from "../favorites";
import type {
  CrossMarketRow,
  DomMarket,
  Exchange,
  Market,
  MarketRow,
  SnapshotResponse,
  SnapshotRow,
} from "../types";
import { ExchangeSwitcher, MULTI_MARKET_EXCHANGES } from "../ExchangeSwitcher";
import { useVirtualRows } from "../useVirtualRows";
import { useNavigationState } from "../hooks/useNavigationState";
import { SkeletonTableRows, SkeletonCard } from "../components/ui/Skeleton";

// ─── Constants ─────────────────────────────────────────────────────────────────

/** Human-readable exchange display names for error/status messages. */
const EXCHANGE_DISPLAY_NAMES: Record<Exchange, string> = {
  mexc: "MEXC",
  asterdex: "AsterDEX",
  lighter: "Lighter",
  binance: "Binance",
  bybit: "Bybit",
  okx: "OKX",
  gateio: "Gate.io",
  htx: "HTX",
  bitget: "Bitget",
  dydx: "dYdX",
  hyperliquid: "Hyperliquid",
};

const SORT_OPTIONS_SPOT_FUT: { value: string; label: string }[] = [
  { value: "spread_bps", label: "Спред (bps)" },
  { value: "net_spread_bps", label: "Чистый спред (bps)" },
  { value: "spread_abs", label: "Спред (абс.)" },
  { value: "l1_max_notional_quote", label: "L1 max USDT≈" },
  { value: "volume_24h_quote", label: "Объём 24h (котировка)" },
  { value: "volume_24h_base", label: "Объём 24h (база / контракты)" },
  { value: "funding_rate", label: "Funding" },
  { value: "symbol", label: "Символ" },
  { value: "mid", label: "Mid" },
  { value: "bid", label: "Bid" },
  { value: "ask", label: "Ask" },
];

const SORT_OPTIONS_CROSS: { value: string; label: string }[] = [
  { value: "basis_mid_bps", label: "Базис (bps)" },
  { value: "basis_mid_abs", label: "Базис (абс.)" },
  { value: "spot_mid", label: "Mid спот" },
  { value: "fut_mid", label: "Mid фьюч" },
  { value: "spot_spread_bps", label: "Спред спот (bps)" },
  { value: "fut_spread_bps", label: "Спред фьюч (bps)" },
  { value: "volume_24h_quote_spot", label: "Объём 24h (спот, кот.)" },
  { value: "volume_24h_quote_fut", label: "Оборот 24h (фьюч)" },
  { value: "funding_rate", label: "Funding" },
  { value: "symbol_spot", label: "Спот" },
  { value: "symbol_futures", label: "Фьючерс" },
];

const TABLE_ROW_PX = 40;
const VIRTUAL_OVERSCAN = 10;

type DisplayMode = "list" | "tiles";
type TilesVariant = "cards" | "charts";

const TILES_VARIANT_STORAGE_KEY = "mexc-ui-tiles-variant";
const TILES_MAX_SYMBOLS_STORAGE_KEY = "mexc-ui-tiles-max-symbols";
const TILES_MAX_SYMBOLS_DEFAULT = 48;
const TILES_MAX_SYMBOLS_MIN = 1;
const TILES_MAX_SYMBOLS_MAX = 2000;

/** В режиме быстрого поиска спреда — отдельный лимит плиток (меньше запросов /api/klines). */
const SPREAD_QUICK_MAX_TILES_STORAGE_KEY = "mexc-ui-spread-quick-max-tiles";
const SPREAD_QUICK_MAX_TILES_DEFAULT = 24;

const DISPLAY_STORAGE_KEY = "mexc-ui-display";

// ─── Helpers ───────────────────────────────────────────────────────────────────

function clampTilesMaxSymbols(n: number): number {
  if (!Number.isFinite(n)) return TILES_MAX_SYMBOLS_DEFAULT;
  return Math.min(
    TILES_MAX_SYMBOLS_MAX,
    Math.max(TILES_MAX_SYMBOLS_MIN, Math.floor(n)),
  );
}

function readTilesMaxSymbolsFromStorage(): number {
  if (typeof window === "undefined") return TILES_MAX_SYMBOLS_DEFAULT;
  try {
    const raw = window.localStorage.getItem(TILES_MAX_SYMBOLS_STORAGE_KEY);
    if (raw == null) return TILES_MAX_SYMBOLS_DEFAULT;
    return clampTilesMaxSymbols(Number.parseInt(raw, 10));
  } catch {
    return TILES_MAX_SYMBOLS_DEFAULT;
  }
}

function readSpreadQuickMaxTilesFromStorage(): number {
  if (typeof window === "undefined") return SPREAD_QUICK_MAX_TILES_DEFAULT;
  try {
    const raw = window.localStorage.getItem(SPREAD_QUICK_MAX_TILES_STORAGE_KEY);
    if (raw == null) return SPREAD_QUICK_MAX_TILES_DEFAULT;
    return clampTilesMaxSymbols(Number.parseInt(raw, 10));
  } catch {
    return SPREAD_QUICK_MAX_TILES_DEFAULT;
  }
}

/** Длинные котировки первыми, чтобы корректно отрезать суффикс (USDT vs USD). */
const SPOT_QUOTE_SUFFIXES = [
  "USDT",
  "USDC",
  "BUSD",
  "TUSD",
  "FDUSD",
  "DAI",
  "BTC",
  "ETH",
  "EUR",
  "USD",
].sort((a, b) => b.length - a.length);

/** Спот: BASE/QUOTE; фьючерсы: BTCUSDT (без подчёркиваний); базис: спот · перп. */
function formatPairForClipboard(
  market: Market,
  symbol: string,
  symbolFut?: string,
): string {
  if (market === "cross" && symbolFut) {
    const s = symbol.trim().toUpperCase();
    for (const q of SPOT_QUOTE_SUFFIXES) {
      if (s.endsWith(q) && s.length > q.length) {
        return `${s.slice(0, -q.length)}/${q} · ${symbolFut.trim().toUpperCase()}`;
      }
    }
    return `${symbol.trim()} · ${symbolFut.trim()}`;
  }
  const s = symbol.trim().toUpperCase();
  if (market === "futures") {
    return s.replace(/_/g, "");
  }
  for (const q of SPOT_QUOTE_SUFFIXES) {
    if (s.endsWith(q) && s.length > q.length) {
      return `${s.slice(0, -q.length)}/${q}`;
    }
  }
  return symbol.trim();
}

function fmt(n: number | null | undefined, digits: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

const SNAPSHOT_LOG = import.meta.env.DEV;

async function fetchSnapshot(
  market: Market,
  options?: { signal?: AbortSignal; nocache?: boolean; exchange?: Exchange },
): Promise<SnapshotResponse> {
  const q = new URLSearchParams({ market });
  if (options?.exchange) q.set("exchange", options.exchange);
  if (options?.nocache) q.set("nocache", "true");
  const url = apiUrl(`/api/snapshot?${q}`);
  if (SNAPSHOT_LOG) console.debug("[MEXC UI] snapshot GET", url);
  const r = await fetch(url, { signal: options?.signal });
  const text = await r.text();
  if (!r.ok) {
    const snippet = text.length > 800 ? `${text.slice(0, 800)}…` : text;
    console.error("[MEXC UI] snapshot HTTP", r.status, snippet);
    throw new Error(
      `HTTP ${r.status}${snippet ? `: ${snippet}` : ""}`,
    );
  }
  let data: SnapshotResponse;
  try {
    data = JSON.parse(text) as SnapshotResponse;
  } catch (e) {
    console.error("[MEXC UI] snapshot JSON parse", e, text.slice(0, 500));
    throw new Error("Некорректный JSON от /api/snapshot");
  }
  if (SNAPSHOT_LOG) {
    console.debug(
      "[MEXC UI] snapshot",
      data.market,
      "ok=",
      data.ok,
      "count=",
      data.count,
      data.error ?? "",
    );
  }
  return data;
}

function downloadCsv(rows: SnapshotRow[], market: Market) {
  const bom = "\uFEFF";
  let head: string;
  let lines: string[];
  let name: string;
  if (market === "cross") {
    const cols: (keyof CrossMarketRow)[] = [
      "symbol_spot",
      "symbol_futures",
      "spot_bid",
      "spot_ask",
      "spot_mid",
      "spot_spread_bps",
      "fut_bid",
      "fut_ask",
      "fut_mid",
      "fut_spread_bps",
      "basis_mid_abs",
      "basis_mid_bps",
      "funding_rate",
      "volume_24h_base_spot",
      "volume_24h_quote_spot",
      "volume_24h_base_fut",
      "volume_24h_quote_fut",
      "observed_at",
    ];
    head = cols.join(",");
    lines = (rows as CrossMarketRow[]).map((row) =>
      cols
        .map((c) => {
          const v = row[c];
          if (v == null) return "";
          if (typeof v === "string") return `"${v.replace(/"/g, '""')}"`;
          return String(v);
        })
        .join(","),
    );
    name = "mexc_cross_basis.csv";
  } else {
    const cols: (keyof MarketRow)[] = [
      "symbol",
      "bid",
      "ask",
      "spread_abs",
      "spread_bps",
      "net_spread_bps",
      "fee_round_trip_bps",
      "mid",
      "l1_max_notional_quote",
      "volume_24h_base",
      "volume_24h_quote",
      "funding_rate",
      "bid_qty",
      "ask_qty",
      "observed_at",
    ];
    head = cols.join(",");
    lines = (rows as MarketRow[]).map((row) =>
      cols
        .map((c) => {
          const v = row[c];
          if (v == null) return "";
          if (typeof v === "string") return `"${v.replace(/"/g, '""')}"`;
          return String(v);
        })
        .join(","),
    );
    name = `mexc_${market}_spread.csv`;
  }
  const blob = new Blob([bom + [head, ...lines].join("\n")], {
    type: "text/csv;charset=utf-8",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function handleCtrlLeftCopy(
  e: MouseEvent,
  symbol: string,
  copy: (spot: string, fut?: string) => void,
  symbolFut?: string,
) {
  if (e.button !== 0 || !e.ctrlKey) return;
  e.preventDefault();
  copy(symbol, symbolFut);
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function TileSortChips({
  market,
  sortBy,
  ascending,
  onSort,
}: {
  market: Market;
  sortBy: string;
  ascending: boolean;
  onSort: (key: string) => void;
}) {
  const chips = useMemo(() => {
    if (market === "cross") {
      return [
        { key: "symbol_spot", label: "Спот" },
        { key: "basis_mid_bps", label: "Базис bps" },
        { key: "basis_mid_abs", label: "Базис" },
        { key: "spot_mid", label: "Mid спот" },
        { key: "fut_mid", label: "Mid фьюч" },
        { key: "spot_spread_bps", label: "bps спот" },
        { key: "fut_spread_bps", label: "bps фьюч" },
        { key: "volume_24h_quote_spot", label: "Объём спот" },
        { key: "volume_24h_quote_fut", label: "Объём фьюч" },
        { key: "funding_rate", label: "Funding" },
      ] as const;
    }
    return [
      { key: "symbol", label: "Символ" },
      { key: "spread_bps", label: "bps" },
      { key: "net_spread_bps", label: "Net bps" },
      { key: "spread_abs", label: "Спред" },
      { key: "bid", label: "Bid" },
      { key: "ask", label: "Ask" },
      { key: "mid", label: "Mid" },
      { key: "volume_24h_quote", label: "Объём 24h (кот.)" },
      { key: "volume_24h_base", label: "Объём 24h (база)" },
      { key: "funding_rate", label: "Funding" },
      { key: "bid_qty", label: "Bid qty" },
      { key: "ask_qty", label: "Ask qty" },
    ] as const;
  }, [market]);

  return (
    <div className="sticky top-0 z-[5] flex flex-wrap items-center gap-1.5 border-b border-line bg-surface-elevated/95 px-3 py-2.5 backdrop-blur-sm">
      <span className="mr-0.5 shrink-0 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
        Сортировка
      </span>
      {chips.map((c) => {
        const active = sortBy === c.key;
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => onSort(c.key)}
            title="Клик — колонка; повторный клик — направление"
            className={`inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition ${
              active
                ? "border-accent bg-accent/15 text-accent"
                : "border-line text-ink-muted hover:border-accent/35 hover:text-ink"
            }`}
          >
            {c.label}
            {active ? (
              ascending ? (
                <ChevronUp className="h-3 w-3 shrink-0" strokeWidth={2.5} />
              ) : (
                <ChevronDown className="h-3 w-3 shrink-0" strokeWidth={2.5} />
              )
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function SortableTh({
  sortKey,
  label,
  sortBy,
  ascending,
  onSort,
  className = "",
}: {
  sortKey: string;
  label: string;
  sortBy: string;
  ascending: boolean;
  onSort: (key: string) => void;
  className?: string;
}) {
  const active = sortBy === sortKey;
  return (
    <th
      scope="col"
      aria-sort={
        active ? (ascending ? "ascending" : "descending") : undefined
      }
      className={`px-4 py-3 font-semibold text-ink ${className} cursor-pointer select-none transition hover:bg-accent/10 active:bg-accent/20`}
      onClick={() => onSort(sortKey)}
      title="Клик: сортировка; повторный клик — по возрастанию / по убыванию"
    >
      <span className="inline-flex items-center gap-1.5">
        {label}
        {active ? (
          ascending ? (
            <ChevronUp
              className="h-3.5 w-3.5 shrink-0 text-accent"
              strokeWidth={2.5}
              aria-hidden
            />
          ) : (
            <ChevronDown
              className="h-3.5 w-3.5 shrink-0 text-accent"
              strokeWidth={2.5}
              aria-hidden
            />
          )
        ) : (
          <ArrowUpDown
            className="h-3 w-3 shrink-0 text-ink-muted/45"
            strokeWidth={2}
            aria-hidden
          />
        )}
      </span>
    </th>
  );
}

function spreadHeatmapClass(netBps: number | null | undefined): string {
  if (netBps == null) return "";
  if (netBps >= 20) return "bg-emerald-500/10";
  if (netBps >= 10) return "bg-emerald-500/5";
  if (netBps >= 3) return "bg-emerald-500/[0.03]";
  if (netBps <= -10) return "bg-rose-500/5";
  return "";
}

const MarketRowTr = memo(function MarketRowTr({
  r,
  onCtrlCopy,
  onOpenChart,
  onOpenSpreadChart,
  onOpenDom,
  onOpenWorkspace,
  onQuickCapture,
  domBookMarket,
  isFavorite,
  onToggleFavorite,
}: {
  r: MarketRow;
  onCtrlCopy: (e: MouseEvent, symbol: string, symbolFut?: string) => void;
  onOpenChart: (symbol: string) => void;
  onOpenSpreadChart: (symbol: string) => void;
  onOpenDom: (m: DomMarket, symbol: string) => void;
  onOpenWorkspace: (w: WorkspaceOpenContext) => void;
  onQuickCapture: (symbol: string) => void;
  domBookMarket: DomMarket;
  isFavorite: boolean;
  onToggleFavorite: () => void;
}) {
  return (
    <tr
      className={`cursor-default border-b border-line/60 transition hover:bg-accent/5 ${spreadHeatmapClass(r.net_spread_bps)}`}
      title="Ctrl+щелчок — копировать символ"
      onMouseDown={(e) => onCtrlCopy(e, r.symbol)}
    >
      <td className="px-4 py-2.5 font-sans font-medium text-ink">
        <div className="flex items-center justify-between gap-1">
          <span className="min-w-0 truncate">{r.symbol}</span>
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              className={`rounded-md p-1 transition hover:bg-accent/15 ${
                isFavorite
                  ? "text-amber-500"
                  : "text-ink-muted hover:text-amber-400"
              }`}
              title={isFavorite ? "Убрать из избранного" : "В избранное"}
              aria-label={
                isFavorite
                  ? `Убрать ${r.symbol} из избранного`
                  : `Добавить ${r.symbol} в избранное`
              }
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onToggleFavorite();
              }}
            >
              <Star
                className={`h-4 w-4 ${isFavorite ? "fill-current" : ""}`}
                strokeWidth={2}
              />
            </button>
            <button
              type="button"
              className="rounded-md p-1 text-accent transition hover:bg-accent/15"
              title="График свечей (MEXC)"
              aria-label={`График ${r.symbol}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onOpenChart(r.symbol);
              }}
            >
              <ChartCandlestick className="h-4 w-4" strokeWidth={2} />
            </button>
            <button
              type="button"
              className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-amber-500 dark:hover:text-amber-400"
              title="График спреда (real-time)"
              aria-label={`Спред ${r.symbol}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onOpenSpreadChart(r.symbol);
              }}
            >
              <Activity className="h-4 w-4" strokeWidth={2} />
            </button>
            <button
              type="button"
              className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-violet-500 dark:hover:text-violet-400"
              title="Рабочее место: график + стакан + плотности"
              aria-label={`Рабочее место ${r.symbol}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onOpenWorkspace({
                  chartSymbol: r.symbol,
                  domSymbol: r.symbol,
                  domMarket: domBookMarket,
                  crossFutSymbol: null,
                });
              }}
            >
              <Maximize2 className="h-4 w-4" strokeWidth={2} />
            </button>
            <button
              type="button"
              className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
              title="Стакан (DOM)"
              aria-label={`Стакан ${r.symbol}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onOpenDom(domBookMarket, r.symbol);
              }}
            >
              <LayoutList className="h-4 w-4" strokeWidth={2} />
            </button>
            {r.net_spread_bps != null && r.net_spread_bps >= 3 && (
              <button
                type="button"
                className="rounded-md p-1 text-amber-500 transition hover:bg-amber-500/15"
                title={`Быстрый захват: ${r.symbol}`}
                aria-label={`Захват спреда ${r.symbol}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onQuickCapture(r.symbol);
                }}
              >
                <Zap className="h-4 w-4" strokeWidth={2} />
              </button>
            )}
          </div>
        </div>
      </td>
      <td className="px-4 py-2.5">{fmt(r.bid, 8)}</td>
      <td className="px-4 py-2.5">{fmt(r.ask, 8)}</td>
      <td className="px-4 py-2.5">{fmt(r.spread_abs, 8)}</td>
      <td className="px-4 py-2.5 text-accent font-mono tabular-nums">
        {r.spread_bps == null ? "—" : fmt(r.spread_bps, 2)}
      </td>
      <td className={`px-4 py-2.5 font-mono tabular-nums font-semibold ${
        r.net_spread_bps == null ? "text-ink-muted"
        : r.net_spread_bps >= 10 ? "text-emerald-500"
        : r.net_spread_bps >= 0 ? "text-emerald-600 dark:text-emerald-400"
        : r.net_spread_bps <= -10 ? "text-rose-500"
        : "text-ink-muted"
      }`}>
        {r.net_spread_bps == null ? "—" : fmt(r.net_spread_bps, 2)}
      </td>
      <InlineSpreadTrend symbol={r.symbol} />
      <td className="px-4 py-2.5">{fmt(r.mid, 8)}</td>
      <td className="px-4 py-2.5">{fmt(r.l1_max_notional_quote ?? 0, 2)}</td>
      <td className="px-4 py-2.5">{fmt(r.volume_24h_base, 4)}</td>
      <td className="px-4 py-2.5">{fmt(r.volume_24h_quote, 2)}</td>
      <td className="px-4 py-2.5">
        {r.funding_rate == null ? "—" : fmt(r.funding_rate, 6)}
      </td>
      <td className="px-4 py-2.5">{fmt(r.bid_qty, 6)}</td>
      <td className="px-4 py-2.5">{fmt(r.ask_qty, 6)}</td>
    </tr>
  );
});

const CrossMarketRowTr = memo(function CrossMarketRowTr({
  r,
  onCtrlCopy,
  onOpenChart,
  onOpenSpreadChart,
  onOpenDom,
  onOpenWorkspace,
  isFavorite,
  onToggleFavorite,
}: {
  r: CrossMarketRow;
  onCtrlCopy: (e: MouseEvent, symbol: string, symbolFut?: string) => void;
  onOpenChart: (symbol: string) => void;
  onOpenSpreadChart: (symbol: string) => void;
  onOpenDom: (m: DomMarket, symbol: string) => void;
  onOpenWorkspace: (w: WorkspaceOpenContext) => void;
  isFavorite: boolean;
  onToggleFavorite: () => void;
}) {
  return (
    <tr
      className="cursor-default border-b border-line/60 transition hover:bg-accent/5"
      title="Ctrl+щелчок — копировать спот и перп"
      onMouseDown={(e) => onCtrlCopy(e, r.symbol_spot, r.symbol_futures)}
    >
      <td className="px-4 py-2.5 font-sans font-medium text-ink">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center justify-between gap-1">
            <span className="min-w-0 truncate font-mono text-xs">
              {r.symbol_spot}
            </span>
            <div className="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                className={`rounded-md p-1 transition hover:bg-accent/15 ${
                  isFavorite
                    ? "text-amber-500"
                    : "text-ink-muted hover:text-amber-400"
                }`}
                title={
                  isFavorite
                    ? "Убрать пару из избранного"
                    : "Пара спот+перп в избранное"
                }
                aria-label={
                  isFavorite
                    ? "Убрать из избранного"
                    : "Добавить пару в избранное"
                }
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleFavorite();
                }}
              >
                <Star
                  className={`h-4 w-4 ${isFavorite ? "fill-current" : ""}`}
                  strokeWidth={2}
                />
              </button>
              <button
                type="button"
                className="rounded-md p-1 text-accent transition hover:bg-accent/15"
                title="График свечей спота (MEXC)"
                aria-label={`График ${r.symbol_spot}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenChart(r.symbol_spot);
                }}
              >
                <ChartCandlestick className="h-4 w-4" strokeWidth={2} />
              </button>
              <button
                type="button"
                className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-amber-500 dark:hover:text-amber-400"
                title="График спреда спота"
                aria-label={`Спред ${r.symbol_spot}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenSpreadChart(r.symbol_spot);
                }}
              >
                <Activity className="h-4 w-4" strokeWidth={2} />
              </button>
              <button
                type="button"
                className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-violet-500 dark:hover:text-violet-400"
                title="Рабочее место: график спота + стакан + плотности"
                aria-label={`Рабочее место ${r.symbol_spot}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenWorkspace({
                    chartSymbol: r.symbol_spot,
                    domSymbol: r.symbol_spot,
                    domMarket: "spot",
                    crossFutSymbol: r.symbol_futures,
                  });
                }}
              >
                <Maximize2 className="h-4 w-4" strokeWidth={2} />
              </button>
              <button
                type="button"
                className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
                title="Стакан спота (DOM)"
                aria-label={`Стакан спот ${r.symbol_spot}`}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenDom("spot", r.symbol_spot);
                }}
              >
                <LayoutList className="h-4 w-4" strokeWidth={2} />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between gap-1 pl-0">
            <span className="truncate font-mono text-[11px] text-ink-muted">
              {r.symbol_futures}
            </span>
            <button
              type="button"
              className="shrink-0 rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
              title="Стакан фьючерса (DOM)"
              aria-label={`Стакан фьюч ${r.symbol_futures}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onOpenDom("futures", r.symbol_futures);
              }}
            >
              <LayoutList className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
        </div>
      </td>
      <td className="px-4 py-2.5 text-accent">
        {r.basis_mid_bps == null ? "—" : fmt(r.basis_mid_bps, 2)}
      </td>
      <td className="px-4 py-2.5">{fmt(r.basis_mid_abs, 8)}</td>
      <td className="px-4 py-2.5">{fmt(r.spot_mid, 8)}</td>
      <td className="px-4 py-2.5">{fmt(r.fut_mid, 8)}</td>
      <td className="px-4 py-2.5">
        {r.spot_spread_bps == null ? "—" : fmt(r.spot_spread_bps, 2)}
      </td>
      <td className="px-4 py-2.5">
        {r.fut_spread_bps == null ? "—" : fmt(r.fut_spread_bps, 2)}
      </td>
      <td className="px-4 py-2.5">{fmt(r.volume_24h_quote_spot, 2)}</td>
      <td className="px-4 py-2.5">{fmt(r.volume_24h_quote_fut, 2)}</td>
      <td className="px-4 py-2.5">
        {r.funding_rate == null ? "—" : fmt(r.funding_rate, 6)}
      </td>
    </tr>
  );
});

const MarketTile = memo(function MarketTile({
  r,
  volBaseLabel,
  volQuoteLabel,
  onCtrlCopy,
  onOpenChart,
  onOpenSpreadChart,
  onOpenDom,
  onOpenWorkspace,
  domBookMarket,
  isFavorite,
  onToggleFavorite,
  tilesVariant,
  market,
  isDark,
  exchange,
}: {
  r: MarketRow;
  volBaseLabel: string;
  volQuoteLabel: string;
  onCtrlCopy: (e: MouseEvent, symbol: string, symbolFut?: string) => void;
  onOpenChart: (symbol: string) => void;
  onOpenSpreadChart: (symbol: string) => void;
  onOpenDom: (m: DomMarket, symbol: string) => void;
  onOpenWorkspace: (w: WorkspaceOpenContext) => void;
  domBookMarket: DomMarket;
  isFavorite: boolean;
  onToggleFavorite: () => void;
  tilesVariant: TilesVariant;
  market: Market;
  isDark: boolean;
  exchange: Exchange;
}) {
  const compact = tilesVariant === "charts";
  return (
    <article
      title="Ctrl+щелчок — копировать символ"
      onMouseDown={(e) => onCtrlCopy(e, r.symbol)}
      className={`cursor-default rounded-xl border text-left shadow-sm outline-none ring-accent/0 transition hover:border-accent/40 hover:bg-accent/[0.03] ${
        compact ? "px-3 py-2.5" : "px-4 py-3"
      } ${
        r.net_spread_bps != null && r.net_spread_bps >= 10
          ? "border-emerald-500/30 bg-emerald-500/[0.03]"
          : r.net_spread_bps != null && r.net_spread_bps <= -10
          ? "border-rose-500/30 bg-rose-500/[0.03]"
          : "border-line bg-surface"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p
          className={`min-w-0 flex-1 font-mono font-semibold text-ink ${
            compact ? "text-xs" : "text-sm"
          }`}
        >
          {r.symbol}
        </p>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            className={`rounded-md p-1 transition hover:bg-accent/15 ${
              isFavorite
                ? "text-amber-500"
                : "text-ink-muted hover:text-amber-400"
            }`}
            title={isFavorite ? "Убрать из избранного" : "В избранное"}
            aria-label={
              isFavorite
                ? `Убрать ${r.symbol} из избранного`
                : `Добавить ${r.symbol} в избранное`
            }
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite();
            }}
          >
            <Star
              className={`h-4 w-4 ${isFavorite ? "fill-current" : ""}`}
              strokeWidth={2}
            />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-accent transition hover:bg-accent/15"
            title="График свечей"
            aria-label={`График ${r.symbol}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenChart(r.symbol);
            }}
          >
            <ChartCandlestick className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-amber-500 dark:hover:text-amber-400"
            title="График спреда"
            aria-label={`Спред ${r.symbol}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenSpreadChart(r.symbol);
            }}
          >
            <Activity className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-violet-500 dark:hover:text-violet-400"
            title="Рабочее место"
            aria-label={`Рабочее место ${r.symbol}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenWorkspace({
                chartSymbol: r.symbol,
                domSymbol: r.symbol,
                domMarket: domBookMarket,
                crossFutSymbol: null,
              });
            }}
          >
            <Maximize2 className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
            title="Стакан (DOM)"
            aria-label={`Стакан ${r.symbol}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenDom(domBookMarket, r.symbol);
            }}
          >
            <LayoutList className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
      </div>
      {tilesVariant === "charts" && (
        <MiniSparkline market={market} symbol={r.symbol} isDark={isDark} exchange={exchange} />
      )}
      <dl
        className={`grid grid-cols-2 gap-x-2 font-mono text-ink-muted ${
          compact
            ? "mt-1.5 gap-y-0.5 text-[10px] leading-tight"
            : "mt-2 gap-x-3 gap-y-1 text-xs"
        }`}
      >
        <div className="col-span-2 flex justify-between gap-2 text-ink">
          <span>Bid</span>
          <span>{fmt(r.bid, 8)}</span>
        </div>
        <div className="col-span-2 flex justify-between gap-2 text-ink">
          <span>Ask</span>
          <span>{fmt(r.ask, 8)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span>bps</span>
          <span className="text-accent">
            {r.spread_bps == null ? "—" : fmt(r.spread_bps, 2)}
          </span>
        </div>
        <div className="flex justify-between gap-2">
          <span>Net</span>
          <span className="text-emerald-600 dark:text-emerald-400">
            {r.net_spread_bps == null ? "—" : fmt(r.net_spread_bps, 2)}
          </span>
        </div>
        <div className="flex justify-between gap-2">
          <span>Mid</span>
          <span className="text-ink">{fmt(r.mid, 8)}</span>
        </div>
        <div className="col-span-2 border-t border-line/60 pt-1 text-[11px]">
          <span className="text-ink-muted">{volBaseLabel}: </span>
          {fmt(r.volume_24h_base, 4)}
        </div>
        <div className="col-span-2 text-[11px]">
          <span className="text-ink-muted">{volQuoteLabel}: </span>
          {fmt(r.volume_24h_quote, 2)}
        </div>
        <div className="col-span-2 text-[11px]">
          <span className="text-ink-muted">Funding: </span>
          {r.funding_rate == null ? "—" : fmt(r.funding_rate, 6)}
        </div>
      </dl>
    </article>
  );
});

const CrossMarketTile = memo(function CrossMarketTile({
  r,
  volSpotLabel,
  volFutLabel,
  onCtrlCopy,
  onOpenChart,
  onOpenSpreadChart,
  onOpenDom,
  onOpenWorkspace,
  isFavorite,
  onToggleFavorite,
  tilesVariant,
  isDark,
}: {
  r: CrossMarketRow;
  volSpotLabel: string;
  volFutLabel: string;
  onCtrlCopy: (e: MouseEvent, symbol: string, symbolFut?: string) => void;
  onOpenChart: (symbol: string) => void;
  onOpenSpreadChart: (symbol: string) => void;
  onOpenDom: (m: DomMarket, symbol: string) => void;
  onOpenWorkspace: (w: WorkspaceOpenContext) => void;
  isFavorite: boolean;
  onToggleFavorite: () => void;
  tilesVariant: TilesVariant;
  isDark: boolean;
}) {
  const compact = tilesVariant === "charts";
  return (
    <article
      title="Ctrl+щелчок — копировать спот и перп"
      onMouseDown={(e) => onCtrlCopy(e, r.symbol_spot, r.symbol_futures)}
      className={`cursor-default rounded-xl border border-line bg-surface text-left shadow-sm outline-none ring-accent/0 transition hover:border-accent/40 hover:bg-accent/[0.03] ${
        compact ? "px-3 py-2.5" : "px-4 py-3"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p
            className={`font-mono font-semibold text-ink ${
              compact ? "text-xs" : "text-sm"
            }`}
          >
            {r.symbol_spot}
          </p>
          <p className="truncate font-mono text-[11px] text-ink-muted">
            {r.symbol_futures}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            className={`rounded-md p-1 transition hover:bg-accent/15 ${
              isFavorite
                ? "text-amber-500"
                : "text-ink-muted hover:text-amber-400"
            }`}
            title={
              isFavorite
                ? "Убрать пару из избранного"
                : "Пара в избранное"
            }
            aria-label={
              isFavorite ? "Убрать из избранного" : "Добавить пару в избранное"
            }
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite();
            }}
          >
            <Star
              className={`h-4 w-4 ${isFavorite ? "fill-current" : ""}`}
              strokeWidth={2}
            />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-accent transition hover:bg-accent/15"
            title="График свечей спота"
            aria-label={`График ${r.symbol_spot}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenChart(r.symbol_spot);
            }}
          >
            <ChartCandlestick className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-amber-500 dark:hover:text-amber-400"
            title="График спреда"
            aria-label={`Спред ${r.symbol_spot}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenSpreadChart(r.symbol_spot);
            }}
          >
            <Activity className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-violet-500 dark:hover:text-violet-400"
            title="Рабочее место"
            aria-label={`Рабочее место ${r.symbol_spot}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenWorkspace({
                chartSymbol: r.symbol_spot,
                domSymbol: r.symbol_spot,
                domMarket: "spot",
                crossFutSymbol: r.symbol_futures,
              });
            }}
          >
            <Maximize2 className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
            title="Стакан спота"
            aria-label={`Стакан спот ${r.symbol_spot}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenDom("spot", r.symbol_spot);
            }}
          >
            <LayoutList className="h-4 w-4" strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rounded-md p-1 text-ink-muted transition hover:bg-accent/15 hover:text-accent"
            title="Стакан фьючерса"
            aria-label={`Стакан фьюч ${r.symbol_futures}`}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onOpenDom("futures", r.symbol_futures);
            }}
          >
            <LayoutList className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        </div>
      </div>
      {tilesVariant === "charts" && (
        <MiniSparkline market="cross" symbol={r.symbol_spot} isDark={isDark} />
      )}
      <dl
        className={`grid grid-cols-2 gap-x-2 font-mono text-ink-muted ${
          compact
            ? "mt-1.5 gap-y-0.5 text-[10px] leading-tight"
            : "mt-2 gap-x-3 gap-y-1 text-xs"
        }`}
      >
        <div className="col-span-2 flex justify-between gap-2 text-ink">
          <span>Базис bps</span>
          <span className="text-accent">
            {r.basis_mid_bps == null ? "—" : fmt(r.basis_mid_bps, 2)}
          </span>
        </div>
        <div className="flex justify-between gap-2">
          <span>Mid спот</span>
          <span className="text-ink">{fmt(r.spot_mid, 8)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span>Mid фьюч</span>
          <span className="text-ink">{fmt(r.fut_mid, 8)}</span>
        </div>
        <div className="col-span-2 border-t border-line/60 pt-1 text-[11px]">
          <span className="text-ink-muted">{volSpotLabel}: </span>
          {fmt(r.volume_24h_quote_spot, 2)}
        </div>
        <div className="col-span-2 text-[11px]">
          <span className="text-ink-muted">{volFutLabel}: </span>
          {fmt(r.volume_24h_quote_fut, 2)}
        </div>
        <div className="col-span-2 text-[11px]">
          <span className="text-ink-muted">Funding: </span>
          {r.funding_rate == null ? "—" : fmt(r.funding_rate, 6)}
        </div>
      </dl>
    </article>
  );
});

// ─── Main Page Component ───────────────────────────────────────────────────────

export function SpreadMonitorPage() {
  const { state: navState, setExchange, setMarket } = useNavigationState();
  const exchange = navState.exchange;
  const market = navState.market;

  const [dark, setDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains("dark")
      : false,
  );
  const [rows, setRows] = useState<SnapshotRow[]>([]);
  const [totalSnapshot, setTotalSnapshot] = useState(0);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);

  const [quoteRaw, setQuoteRaw] = useState("USDT");
  const [minSpreadBps, setMinSpreadBps] = useState(0);
  const [minVolQuote, setMinVolQuote] = useState(0);
  /** Режим быстрого отбора: спред + L1 «плотность» bid/ask (нотация USDT≈). */
  const [spreadQuickHunt, setSpreadQuickHunt] = useState(false);
  const [minBidL1NotionalQuote, setMinBidL1NotionalQuote] = useState(0);
  const [minAskL1NotionalQuote, setMinAskL1NotionalQuote] = useState(0);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [sortBy, setSortBy] = useState("spread_bps");
  const [ascending, setAscending] = useState(false);

  const [autoRefresh, setAutoRefresh] = useState(false);
  const [intervalSec, setIntervalSec] = useState(20);

  const [displayMode, setDisplayMode] = useState<DisplayMode>(() => {
    if (typeof window === "undefined") return "list";
    const v = window.localStorage.getItem(DISPLAY_STORAGE_KEY);
    return v === "tiles" ? "tiles" : "list";
  });
  const [tilesVariant, setTilesVariant] = useState<TilesVariant>(() => {
    if (typeof window === "undefined") return "cards";
    return window.localStorage.getItem(TILES_VARIANT_STORAGE_KEY) === "charts"
      ? "charts"
      : "cards";
  });
  const [tilesMaxSymbols, setTilesMaxSymbols] = useState(
    readTilesMaxSymbolsFromStorage,
  );
  const [spreadQuickHuntMaxTiles, setSpreadQuickHuntMaxTiles] = useState(
    readSpreadQuickMaxTilesFromStorage,
  );
  const [copyToast, setCopyToast] = useState<string | null>(null);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);
  const [domTarget, setDomTarget] = useState<{
    market: DomMarket;
    symbol: string;
  } | null>(null);
  const [workspaceCtx, setWorkspaceCtx] = useState<WorkspaceOpenContext | null>(
    null,
  );
  const [spreadChartSymbol, setSpreadChartSymbol] = useState<string | null>(null);
  const [favoritesTick, setFavoritesTick] = useState(0);
  const [favoritesScope, setFavoritesScopeState] = useState<FavoritesScope>(() =>
    readFavoritesScope(),
  );

  const abortRef = useRef<AbortController | null>(null);
  const fetchIdRef = useRef(0);
  const tableScrollRef = useRef<HTMLDivElement | null>(null);
  const copyToastTimerRef = useRef<number>(0);
  const beforeQuickHuntDisplayRef = useRef<DisplayMode | null>(null);
  const beforeQuickHuntTilesVariantRef = useRef<TilesVariant | null>(null);
  const spreadQuickHuntRef = useRef(false);
  spreadQuickHuntRef.current = spreadQuickHunt;

  useEffect(() => {
    const saved = localStorage.getItem("mexc-ui-theme");
    const isDark = saved === "dark";
    document.documentElement.classList.toggle("dark", isDark);
    setDark(isDark);
  }, []);

  const toggleTheme = () => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("mexc-ui-theme", next ? "dark" : "light");
    setDark(next);
  };

  const setDisplay = useCallback((mode: DisplayMode) => {
    setDisplayMode(mode);
    try {
      localStorage.setItem(DISPLAY_STORAGE_KEY, mode);
    } catch {
      /* ignore */
    }
  }, []);

  const setTilesVariantPersist = useCallback((v: TilesVariant) => {
    setTilesVariant(v);
    try {
      localStorage.setItem(TILES_VARIANT_STORAGE_KEY, v);
    } catch {
      /* ignore */
    }
  }, []);

  const setTilesMaxSymbolsPersist = useCallback((n: number) => {
    const c = clampTilesMaxSymbols(n);
    setTilesMaxSymbols(c);
    try {
      localStorage.setItem(TILES_MAX_SYMBOLS_STORAGE_KEY, String(c));
    } catch {
      /* ignore */
    }
  }, []);

  const setSpreadQuickHuntMaxTilesPersist = useCallback((n: number) => {
    const c = clampTilesMaxSymbols(n);
    setSpreadQuickHuntMaxTiles(c);
    try {
      localStorage.setItem(SPREAD_QUICK_MAX_TILES_STORAGE_KEY, String(c));
    } catch {
      /* ignore */
    }
  }, []);

  const restoreQuickHuntSavedView = useCallback(() => {
    const d = beforeQuickHuntDisplayRef.current;
    const v = beforeQuickHuntTilesVariantRef.current;
    if (d != null) {
      setDisplay(d);
      beforeQuickHuntDisplayRef.current = null;
    }
    if (v != null) {
      setTilesVariantPersist(v);
      beforeQuickHuntTilesVariantRef.current = null;
    }
  }, [setDisplay, setTilesVariantPersist]);

  const flashCopySymbol = useCallback((symbol: string, symbolFut?: string) => {
    const text = formatPairForClipboard(market, symbol, symbolFut);
    const run = async () => {
      try {
        await navigator.clipboard.writeText(text);
        setCopyToast(`Скопировано: ${text}`);
      } catch {
        setCopyToast("Не удалось скопировать (доступ к буферу)");
      }
      window.clearTimeout(copyToastTimerRef.current);
      copyToastTimerRef.current = window.setTimeout(() => {
        setCopyToast(null);
      }, 1600);
    };
    void run();
  }, [market]);

  const onPairCtrlCopy = useCallback(
    (e: MouseEvent, symbol: string, symbolFut?: string) => {
      handleCtrlLeftCopy(e, symbol, flashCopySymbol, symbolFut);
    },
    [flashCopySymbol],
  );

  const openChart = useCallback((symbol: string) => {
    setChartSymbol(symbol);
  }, []);

  const closeChart = useCallback(() => {
    setChartSymbol(null);
  }, []);

  const openSpreadChart = useCallback((symbol: string) => {
    setSpreadChartSymbol(symbol);
  }, []);

  const closeSpreadChart = useCallback(() => {
    setSpreadChartSymbol(null);
  }, []);

  const openDom = useCallback((m: DomMarket, sym: string) => {
    setDomTarget({ market: m, symbol: sym });
  }, []);

  const closeDom = useCallback(() => {
    setDomTarget(null);
  }, []);

  const openWorkspace = useCallback((w: WorkspaceOpenContext) => {
    setWorkspaceCtx(w);
  }, []);

  const closeWorkspace = useCallback(() => {
    setWorkspaceCtx(null);
  }, []);

  const bumpFavorites = useCallback(() => {
    setFavoritesTick((t) => t + 1);
  }, []);

  const setFavoritesScopePersist = useCallback((s: FavoritesScope) => {
    setFavoritesScopeState(s);
    writeFavoritesScope(s);
  }, []);

  const favoriteSet = useMemo(
    () => readFavoriteSet(market),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [market, favoritesTick],
  );

  const favoritesList = useMemo(
    () => readFavoritesSorted(market),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [market, favoritesTick],
  );

  const domBookMarket: DomMarket =
    market === "futures" ? "futures" : "spot";

  const toggleColumnSort = useCallback(
    (key: string) => {
      if (sortBy === key) {
        setAscending((a) => !a);
      } else {
        setSortBy(key);
        setAscending(
          key === "symbol" ||
            key === "symbol_spot" ||
            key === "symbol_futures",
        );
      }
    },
    [sortBy],
  );

  useEffect(() => {
    return () => window.clearTimeout(copyToastTimerRef.current);
  }, []);

  const load = useCallback(
    async (options?: { nocache?: boolean }) => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      const id = ++fetchIdRef.current;
      const m = exchange !== "mexc" ? "futures" as Market : market;
      setIsFetching(true);
      setError(null);
      try {
        const data = await fetchSnapshot(m, {
          signal: ac.signal,
          nocache: options?.nocache,
          exchange,
        });
        if (id !== fetchIdRef.current) return;
        if (!data.ok) {
          const msg = data.error ?? "Неизвестная ошибка";
          if (SNAPSHOT_LOG) console.warn("[MEXC UI] snapshot ok=false", m, msg);
          setError(`Ошибка загрузки данных ${EXCHANGE_DISPLAY_NAMES[exchange]}: ${msg}`);
          setRows([]);
          setTotalSnapshot(0);
          return;
        }
        const nextRows = Array.isArray(data.rows) ? data.rows : [];
        if (!Array.isArray(data.rows)) {
          console.error("[MEXC UI] snapshot rows не массив", typeof data.rows, m);
        }
        setRows(nextRows);
        setTotalSnapshot(
          typeof data.count === "number" ? data.count : nextRows.length,
        );
        setLoadedAt(data.loaded_at ?? null);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (id !== fetchIdRef.current) return;
        setError(`Ошибка загрузки данных ${EXCHANGE_DISPLAY_NAMES[exchange]}: ${e instanceof Error ? e.message : String(e)}`);
        setRows([]);
        setTotalSnapshot(0);
      } finally {
        if (id === fetchIdRef.current) {
          setIsFetching(false);
        }
      }
    },
    [market, exchange],
  );

  useEffect(() => {
    setRows([]);
    setTotalSnapshot(0);
    setLoadedAt(null);
    setError(null);
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  useEffect(() => {
    setQuoteRaw(market === "futures" ? "_USDT" : "USDT");
    setSortBy(market === "cross" ? "basis_mid_bps" : "spread_bps");
    setAscending(false);
    if (market === "cross") {
      if (spreadQuickHuntRef.current) {
        restoreQuickHuntSavedView();
      }
      setSpreadQuickHunt(false);
    }
  }, [market, restoreQuickHuntSavedView]);

  /** При смене биржи: сбросить quoteRaw на "USDT" для DEX-бирж. */
  const handleExchangeChange = useCallback(
    (next: Exchange) => {
      if (next === exchange) return;
      setExchange(next);
      if (next !== "mexc") {
        setQuoteRaw("USDT");
      }
    },
    [exchange, setExchange],
  );

  const sortSelectOptions = useMemo(
    () => (market === "cross" ? SORT_OPTIONS_CROSS : SORT_OPTIONS_SPOT_FUT),
    [market],
  );

  useEffect(() => {
    if (!autoRefresh) return;
    const t = window.setInterval(() => void load(), intervalSec * 1000);
    return () => window.clearInterval(t);
  }, [autoRefresh, intervalSec, load]);

  const rowUniverse = useMemo(() => {
    if (favoritesScope !== "favorites_only") return rows;
    return rows.filter((r) => favoriteSet.has(favoriteKeyForRow(market, r)));
  }, [rows, favoritesScope, favoriteSet, market]);

  const effectiveBidL1Nq =
    spreadQuickHunt && market !== "cross" ? minBidL1NotionalQuote : 0;
  const effectiveAskL1Nq =
    spreadQuickHunt && market !== "cross" ? minAskL1NotionalQuote : 0;

  const filtered = useMemo(
    () =>
      applyMarketFilters(rowUniverse, {
        market,
        quoteRaw,
        minSpreadBps: minSpreadBps,
        minVolQuote: minVolQuote,
        search: deferredSearch,
        sortBy,
        ascending,
        minBidL1NotionalQuote: effectiveBidL1Nq,
        minAskL1NotionalQuote: effectiveAskL1Nq,
      }),
    [
      rowUniverse,
      market,
      quoteRaw,
      minSpreadBps,
      minVolQuote,
      deferredSearch,
      sortBy,
      ascending,
      effectiveBidL1Nq,
      effectiveAskL1Nq,
    ],
  );

  const activeTilesCap = useMemo(
    () =>
      spreadQuickHunt && market !== "cross"
        ? spreadQuickHuntMaxTiles
        : tilesMaxSymbols,
    [spreadQuickHunt, market, spreadQuickHuntMaxTiles, tilesMaxSymbols],
  );

  const viewTilesVariant: TilesVariant =
    spreadQuickHunt && market !== "cross" ? "charts" : tilesVariant;

  const tilesVisibleRows = useMemo(
    () => filtered.slice(0, activeTilesCap),
    [filtered, activeTilesCap],
  );

  const { start, end, topPad, bottomPad } = useVirtualRows(
    tableScrollRef,
    displayMode === "list" ? filtered.length : 0,
    TABLE_ROW_PX,
    VIRTUAL_OVERSCAN,
  );

  const visibleRows = useMemo(
    () => filtered.slice(start, end),
    [filtered, start, end],
  );

  const hasRows = rows.length > 0;
  const showBlockingLoading = isFetching && !hasRows;
  const showStaleTable = isFetching && hasRows;

  const tableColSpan = market === "cross" ? 10 : 13;

  const volBaseLabel =
    market === "spot"
      ? "Объём 24h (база)"
      : market === "futures"
        ? "Объём 24h (контракты)"
        : "Объём 24h (спот, база)";
  const volQuoteLabel =
    market === "spot"
      ? "Объём 24h (котировка)"
      : market === "futures"
        ? "Оборот 24h (amount24)"
        : "Оборот 24h (фьюч)";
  const crossVolSpotLabel = "Объём 24h (спот, кот.)";
  const crossVolFutLabel = "Оборот 24h (фьюч)";

  return (
    <div className="flex h-full min-h-0 w-full">
      {/* Overlay modals */}
      <ChartModal
        open={chartSymbol != null}
        onClose={closeChart}
        market={market}
        symbol={chartSymbol}
        isDark={dark}
      />
      <DomModal
        open={domTarget != null}
        onClose={closeDom}
        market={domTarget?.market ?? "spot"}
        symbol={domTarget?.symbol ?? null}
      />
      <AssetWorkspaceModal
        open={workspaceCtx != null}
        onClose={closeWorkspace}
        appMarket={market}
        ctx={workspaceCtx}
        isDark={dark}
      />
      <SpreadChartModal
        open={spreadChartSymbol != null}
        onClose={closeSpreadChart}
        symbol={spreadChartSymbol}
        market={market === "futures" ? "futures" : "spot"}
        isDark={dark}
      />

      {/* Filters sidebar (page-local, not the navigation sidebar) */}
      <aside className="hidden w-80 shrink-0 flex-col border-r border-line bg-surface-elevated shadow-panel dark:shadow-panel-dark xl:flex">
        <div className="border-b border-line p-5">
          <p className="text-xs font-medium uppercase tracking-wider text-ink-muted">
            {exchange === "mexc" ? "MEXC" : exchange === "asterdex" ? "AsterDEX" : "Lighter"}
          </p>
          <h1 className="mt-1 text-lg font-semibold leading-tight text-ink">
            Spread Monitor
          </h1>
        </div>

        <div className="flex flex-1 flex-col gap-6 overflow-y-auto scroll-thin p-5">
          <section>
            <label className="mb-2 block text-xs font-medium text-ink-muted">
              Биржа
            </label>
            <ExchangeSwitcher
              active={exchange}
              onChange={handleExchangeChange}
              disabled={isFetching}
            />
          </section>

          {MULTI_MARKET_EXCHANGES.includes(exchange) && (
          <section>
            <label className="mb-2 block text-xs font-medium text-ink-muted">
              Рынок
            </label>
            <div className="flex gap-1 rounded-xl bg-surface p-1 ring-1 ring-line">
              <button
                type="button"
                onClick={() => setMarket("spot")}
                className={`min-w-0 flex-1 rounded-lg px-2 py-2.5 text-sm font-medium transition ${
                  market === "spot"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                Спот
              </button>
              <button
                type="button"
                onClick={() => setMarket("futures")}
                className={`min-w-0 flex-1 rounded-lg px-2 py-2.5 text-sm font-medium transition ${
                  market === "futures"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                Фьючерсы
              </button>
            </div>
            <button
              type="button"
              onClick={() => setMarket("cross")}
              className={`mt-2 w-full rounded-lg border px-3 py-2 text-xs font-medium transition ${
                market === "cross"
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-line text-ink-muted hover:border-accent/40 hover:text-ink"
              }`}
            >
              Базис (спот ↔ перп)
            </button>
            <p className="mt-2 text-xs leading-relaxed text-ink-muted">
              {market === "futures"
                ? "contract.mexc.com — bid1/ask1, объёмы, funding."
                : market === "cross"
                  ? "Два снимка: спот + фьючерсы, сопоставление BTCUSDT ↔ BTC_USDT, базис по mid."
                  : "api.mexc.com — bookTicker + 24h."}
            </p>
          </section>
          )}

          <section className="space-y-3 border-t border-line pt-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Избранное
            </h2>
            <p className="text-[11px] leading-relaxed text-ink-muted">
              Список общий для текущего рынка (спот / фьючерсы / базис) и виден в списке
              и плитках. Режим «Только избранные» сужает набор строк до отмеченных
              звёздочкой; затем к ним применяются фильтры ниже.
            </p>
            <label className="mb-1 block text-xs font-medium text-ink-muted">
              Набор для таблицы
            </label>
            <select
              value={favoritesScope}
              onChange={(e) =>
                setFavoritesScopePersist(
                  e.target.value === "favorites_only" ? "favorites_only" : "all",
                )
              }
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="all">Все активы снимка</option>
              <option value="favorites_only">Только избранные</option>
            </select>
            <div>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-ink-muted">
                  Список ({favoritesList.length})
                </span>
                {favoritesList.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      clearFavoritesMarket(market);
                      bumpFavorites();
                    }}
                    className="text-[10px] font-medium text-accent hover:underline"
                  >
                    Очистить рынок
                  </button>
                ) : null}
              </div>

              {favoritesList.length === 0 ? (
                <p className="rounded-lg border border-dashed border-line bg-surface px-2 py-3 text-center text-[11px] text-ink-muted">
                  Нет избранных. Нажмите ★ у пары в таблице или плитке.
                </p>
              ) : (
                <ul className="max-h-40 space-y-1 overflow-y-auto scroll-thin rounded-lg border border-line bg-surface p-2">
                  {favoritesList.map((key) => (
                    <li
                      key={key}
                      className="flex items-center justify-between gap-1 font-mono text-[11px] text-ink"
                    >
                      <span className="min-w-0 truncate" title={key}>
                        {key}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          removeFavoriteKey(market, key);
                          bumpFavorites();
                        }}
                        className="shrink-0 rounded p-0.5 text-ink-muted transition hover:bg-accent/15 hover:text-rose-600 dark:hover:text-rose-400"
                        aria-label={`Удалить ${key} из избранного`}
                      >
                        <X className="h-3.5 w-3.5" strokeWidth={2} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Фильтры
            </h2>
            <p className="text-[11px] leading-relaxed text-ink-muted">
              Котировка, спред, объём и поиск применяются к строкам после режима
              «Избранное» (ко всему снимку или только к отмеченным парам).
            </p>
            <div>
              <label className="mb-1 block text-xs text-ink-muted">
                Котировка (суффикс символа)
              </label>
              <input
                value={quoteRaw}
                onChange={(e) => setQuoteRaw(e.target.value)}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none ring-accent/0 transition focus:ring-2 focus:ring-accent"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-ink-muted">
                Мин. спред / |базис| (bps)
              </label>
              <input
                type="number"
                min={0}
                step={0.1}
                value={minSpreadBps || ""}
                onChange={(e) => setMinSpreadBps(Number(e.target.value) || 0)}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-ink-muted">
                Мин. объём 24h (котировка
                {market === "cross" ? ", обе ноги" : ""})
              </label>
              <input
                type="number"
                min={0}
                step={1000}
                value={minVolQuote || ""}
                onChange={(e) => setMinVolQuote(Number(e.target.value) || 0)}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-ink-muted">
                Поиск по символу
              </label>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="BTC…"
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              />
            </div>

            {market !== "cross" ? (
              <div className="rounded-lg border border-line/80 bg-surface/50 p-3">
                <label className="flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    checked={spreadQuickHunt}
                    onChange={(e) => {
                      const on = e.target.checked;
                      if (on && (market as string) !== "cross") {
                        beforeQuickHuntDisplayRef.current = displayMode;
                        beforeQuickHuntTilesVariantRef.current = tilesVariant;
                        setDisplay("tiles");
                        setTilesVariantPersist("charts");
                      } else if (!on) {
                        restoreQuickHuntSavedView();
                      }
                      setSpreadQuickHunt(on);
                    }}
                    className="mt-0.5 rounded border-line text-accent focus:ring-accent"
                  />
                  <span>
                    <span className="text-sm font-medium text-ink">
                      Быстрый поиск спреда
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-snug text-ink-muted">
                      Отбор пар с заданным gross-спредом и минимальной нотацией на
                      лучшем bid и ask (bidQty×bid и askQty×ask из снимка; для
                      USDT-пар это ≈ USDT). Включение переключает вид на плитки с
                      мини‑графиками и отдельный лимит числа плиток.
                    </span>
                  </span>
                </label>

                {spreadQuickHunt ? (
                  <div className="mt-3 space-y-2 border-t border-line/60 pt-3">
                    <div>
                      <label
                        className="mb-1 block text-[11px] text-ink-muted"
                        htmlFor="spread-quick-max-tiles"
                      >
                        Макс. плиток с графиками
                      </label>
                      <input
                        id="spread-quick-max-tiles"
                        type="number"
                        min={TILES_MAX_SYMBOLS_MIN}
                        max={TILES_MAX_SYMBOLS_MAX}
                        step={1}
                        value={spreadQuickHuntMaxTiles}
                        onChange={(e) =>
                          setSpreadQuickHuntMaxTilesPersist(
                            Number(e.target.value) || 0,
                          )
                        }
                        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                      />
                      <p className="mt-1 text-[10px] leading-relaxed text-ink-muted">
                        Первые N пар после фильтров и сортировки — только они
                        попадают в сетку; запросы /api/klines у мини‑графиков при
                        появлении в зоне видимости. Диапазон {TILES_MAX_SYMBOLS_MIN}–
                        {TILES_MAX_SYMBOLS_MAX}, значение сохраняется отдельно от
                        общего режима «Плитки».
                      </p>
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] text-ink-muted">
                        Мин. нотация L1 bid (USDT≈)
                      </label>
                      <input
                        type="number"
                        min={0}
                        step={50}
                        value={minBidL1NotionalQuote || ""}
                        onChange={(e) =>
                          setMinBidL1NotionalQuote(Number(e.target.value) || 0)
                        }
                        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] text-ink-muted">
                        Мин. нотация L1 ask (USDT≈)
                      </label>
                      <input
                        type="number"
                        min={0}
                        step={50}
                        value={minAskL1NotionalQuote || ""}
                        onChange={(e) =>
                          setMinAskL1NotionalQuote(Number(e.target.value) || 0)
                        }
                        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                      />
                    </div>

                    <button
                      type="button"
                      onClick={() => {
                        setMinSpreadBps((prev) => (prev > 0 ? prev : 5));
                        setMinBidL1NotionalQuote((prev) => (prev > 0 ? prev : 250));
                        setMinAskL1NotionalQuote((prev) => (prev > 0 ? prev : 250));
                        setSortBy("net_spread_bps");
                        setAscending(false);
                      }}
                      className="w-full rounded-lg border border-dashed border-accent/40 bg-accent/5 px-2 py-1.5 text-[11px] font-medium text-accent hover:bg-accent/10"
                    >
                      Пресет: 5 bps спреда, 250 USDT на bid и ask, сортировка по
                      чистому спреду
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Сортировка
            </h2>
            <p className="text-xs leading-relaxed text-ink-muted">
              «Список»: сортировка кликом по заголовку столбца. «Плитки»: чипы над
              сеткой (↑↓ как в таблице).
              {spreadQuickHunt && market !== "cross"
                ? " В режиме быстрого поиска список недоступен."
                : ""}
            </p>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              {sortSelectOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={ascending}
                onChange={(e) => setAscending(e.target.checked)}
                className="rounded border-line text-accent focus:ring-accent"
              />
              По возрастанию
            </label>
          </section>

          <section className="space-y-3 border-t border-line pt-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Вид
            </h2>
            <div className="flex rounded-xl bg-surface p-1 ring-1 ring-line">
              <button
                type="button"
                disabled={spreadQuickHunt && market !== "cross"}
                title={
                  spreadQuickHunt && market !== "cross"
                    ? "В режиме быстрого поиска доступны только плитки с графиками"
                    : undefined
                }
                onClick={() => setDisplay("list")}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-45 ${
                  displayMode === "list"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                <List className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                Список
              </button>
              <button
                type="button"
                onClick={() => setDisplay("tiles")}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-xs font-medium transition ${
                  displayMode === "tiles"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                <LayoutGrid className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                Плитки
              </button>
            </div>

            {spreadQuickHunt && market !== "cross" ? (
              <p className="text-[11px] leading-relaxed text-amber-800 dark:text-amber-400/95">
                Быстрый поиск: всегда плитки с мини‑графиками; число плиток и
                подгрузка свечей ограничены полем «Макс. плиток с графиками» в
                блоке фильтров выше.
              </p>
            ) : null}
            <p className="text-xs leading-relaxed text-ink-muted">
              <kbd className="rounded border border-line bg-surface px-1 py-0.5 font-mono text-[10px]">
                Ctrl
              </kbd>
              {
                " + щелчок — в буфер: спот как BASE/QUOTE, фьючерсы как BTCUSDT, базис — спот · перп."
              }
            </p>
            {displayMode === "tiles" &&
            !(spreadQuickHunt && market !== "cross") ? (
              <div className="space-y-2 pt-1">
                <label className="block text-xs font-medium text-ink-muted">
                  Плитки: вид
                </label>
                <div className="flex rounded-xl bg-surface p-1 ring-1 ring-line">
                  <button
                    type="button"
                    onClick={() => setTilesVariantPersist("cards")}
                    className={`flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-medium transition ${
                      tilesVariant === "cards"
                        ? "bg-accent text-white shadow"
                        : "text-ink-muted hover:text-ink"
                    }`}
                  >
                    <LayoutGrid className="h-3 w-3 shrink-0" strokeWidth={2} />
                    Карточки
                  </button>
                  <button
                    type="button"
                    onClick={() => setTilesVariantPersist("charts")}
                    className={`flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-medium transition ${
                      tilesVariant === "charts"
                        ? "bg-accent text-white shadow"
                        : "text-ink-muted hover:text-ink"
                    }`}
                  >
                    <ChartLine className="h-3 w-3 shrink-0" strokeWidth={2} />
                    Графики
                  </button>
                </div>

                <p className="text-[11px] leading-relaxed text-ink-muted">
                  «Графики»: мини-линия цены закрытия (1h), запрос свечей при
                  прокрутке к плитке.
                </p>
                <div>
                  <label
                    className="mb-1 block text-xs text-ink-muted"
                    htmlFor="tiles-max-symbols"
                  >
                    Макс. плиток на экране
                  </label>
                  <input
                    id="tiles-max-symbols"
                    type="number"
                    min={TILES_MAX_SYMBOLS_MIN}
                    max={TILES_MAX_SYMBOLS_MAX}
                    step={1}
                    value={tilesMaxSymbols}
                    onChange={(e) =>
                      setTilesMaxSymbolsPersist(Number(e.target.value) || 0)
                    }
                    className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                  />
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                    Первые N пар после фильтров и сортировки. Меньше N — меньше
                    узлов в сетке и запросов свечей в режиме «Графики». Диапазон:{" "}
                    {TILES_MAX_SYMBOLS_MIN}–{TILES_MAX_SYMBOLS_MAX}.
                  </p>
                </div>
              </div>
            ) : null}
          </section>

          <MetricsHelpPanel />

          <section className="space-y-3 border-t border-line pt-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Обновление
            </h2>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-line text-accent focus:ring-accent"
              />
              Авто каждые {intervalSec} с
            </label>
            <input
              type="range"
              min={10}
              max={120}
              step={5}
              disabled={!autoRefresh}
              value={intervalSec}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
              className="w-full accent-accent disabled:opacity-40"
            />
          </section>
        </div>
      </aside>


      {/* Main content area */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="relative flex flex-wrap items-center justify-between gap-4 border-b border-line bg-surface-elevated px-6 py-4">
          {showStaleTable && (
            <div
              className="pointer-events-none absolute inset-x-0 top-0 h-0.5 overflow-hidden bg-accent/20"
              aria-hidden
            >
              <div className="h-full w-1/3 animate-pulse bg-accent/60" />
            </div>
          )}
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent">
              <Activity className="h-5 w-5" strokeWidth={2} />
            </div>
            <div>
              <p className="text-sm font-medium text-ink">
                {spreadQuickHunt && market !== "cross" ? (
                  <>
                    Быстрый поиск: плитки‑графики, показано{" "}
                    <span className="font-mono text-accent">
                      {tilesVisibleRows.length}
                    </span>
                    {filtered.length > tilesVisibleRows.length ? (
                      <>
                        {" "}
                        из{" "}
                        <span className="font-mono">{filtered.length}</span> после
                        фильтров (лимит {activeTilesCap})
                      </>
                    ) : (
                      <> пар после фильтров</>
                    )}
                  </>
                ) : displayMode === "tiles" &&
                  filtered.length > tilesVisibleRows.length ? (
                  <>
                    Показано плиток:{" "}
                    <span className="font-mono text-accent">
                      {tilesVisibleRows.length}
                    </span>
                    {" из "}
                    <span className="font-mono">{filtered.length}</span>
                    {" после фильтров"}
                  </>
                ) : (
                  <>
                    Пары после фильтров:{" "}
                    <span className="font-mono text-accent">
                      {filtered.length}
                    </span>
                  </>
                )}
                {totalSnapshot > 0 && (
                  <span className="text-ink-muted">
                    {" "}
                    / {totalSnapshot} в снимке
                  </span>
                )}
              </p>
              <p className="text-xs text-ink-muted">
                {loadedAt ? (
                  <>
                    Обновлено (UTC):{" "}
                    {new Date(loadedAt).toLocaleString("ru-RU", {
                      timeZone: "UTC",
                    })}
                  </>
                ) : (
                  " "
                )}
                {showStaleTable && (
                  <span className="text-accent"> · обновляем данные…</span>
                )}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={toggleTheme}
              className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink transition hover:bg-surface-elevated"
              title="Тема"
            >
              {dark ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
            </button>
            <button
              type="button"
              onClick={() => downloadCsv(filtered, market)}
              disabled={filtered.length === 0}
              className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium text-ink transition hover:bg-surface-elevated disabled:opacity-40"
            >
              <Download className="h-4 w-4" />
              CSV
            </button>
            <button
              type="button"
              onClick={() => void load({ nocache: true })}
              disabled={isFetching && !hasRows}
              className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white shadow transition hover:opacity-90 disabled:opacity-50"
            >
              <RefreshCw
                className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
              />
              Обновить
            </button>
          </div>
        </header>

        <div className="relative flex-1 overflow-hidden p-4">
          {copyToast && (
            <div
              className="pointer-events-none fixed bottom-6 right-6 z-50 max-w-sm rounded-lg border border-line bg-surface-elevated px-4 py-2 text-sm text-ink shadow-lg dark:shadow-panel-dark"
              role="status"
            >
              {copyToast}
            </div>
          )}
          {error && (
            <div className="mb-4 flex items-center gap-3 rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-300">
              <span className="flex-1">{error}</span>
              <button
                type="button"
                onClick={() => void load({ nocache: true })}
                className="shrink-0 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-500/20 dark:text-red-300"
              >
                Повторить
              </button>
            </div>
          )}

          <div
            ref={tableScrollRef}
            className="relative h-[calc(100vh-8rem)] overflow-auto rounded-2xl border border-line bg-surface-elevated shadow-panel dark:shadow-panel-dark scroll-thin"
          >
            {displayMode === "list" ? (
              <table
                className={`w-full table-fixed border-collapse text-left text-sm ${
                  market === "cross" ? "min-w-[1180px]" : "min-w-[1360px]"
                }`}
              >
                <thead>
                  <tr className="sticky top-0 z-10 border-b border-line bg-surface-elevated/95 backdrop-blur-sm">
                    {market === "cross" ? (
                      <>
                        <SortableTh
                          className="w-[14%]"
                          sortKey="symbol_spot"
                          label="Пара"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[8%]"
                          sortKey="basis_mid_bps"
                          label="Базис bps"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="basis_mid_abs"
                          label="Базис abs"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="spot_mid"
                          label="Mid спот"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="fut_mid"
                          label="Mid фьюч"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[7%]"
                          sortKey="spot_spread_bps"
                          label="bps спот"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[7%]"
                          sortKey="fut_spread_bps"
                          label="bps фьюч"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[10%]"
                          sortKey="volume_24h_quote_spot"
                          label={crossVolSpotLabel}
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[10%]"
                          sortKey="volume_24h_quote_fut"
                          label={crossVolFutLabel}
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="funding_rate"
                          label="Funding"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                      </>
                    ) : (
                      <>
                        <SortableTh
                          className="w-[10%]"
                          sortKey="symbol"
                          label="Символ"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="bid"
                          label="Bid"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="ask"
                          label="Ask"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="spread_abs"
                          label="Спред"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[7%]"
                          sortKey="spread_bps"
                          label="bps"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[7%]"
                          sortKey="net_spread_bps"
                          label="Net"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <th className="w-[6%] px-4 py-2 text-xs font-medium text-ink-muted">
                          Trend
                        </th>
                        <SortableTh
                          className="w-[8%]"
                          sortKey="l1_max_notional_quote"
                          label="L1 USDT"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="mid"
                          label="Mid"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[10%]"
                          sortKey="volume_24h_base"
                          label={volBaseLabel}
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[10%]"
                          sortKey="volume_24h_quote"
                          label={volQuoteLabel}
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="funding_rate"
                          label="Funding"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="bid_qty"
                          label="Bid qty"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                        <SortableTh
                          className="w-[9%]"
                          sortKey="ask_qty"
                          label="Ask qty"
                          sortBy={sortBy}
                          ascending={ascending}
                          onSort={toggleColumnSort}
                        />
                      </>
                    )}
                  </tr>
                </thead>
                <tbody className="font-mono text-xs text-ink">
                  {showBlockingLoading && (
                    <SkeletonTableRows rows={10} colSpan={tableColSpan} />
                  )}
                  {topPad > 0 && (
                    <tr
                      aria-hidden
                      className="pointer-events-none border-0 hover:bg-transparent"
                    >
                      <td
                        colSpan={tableColSpan}
                        className="border-0 p-0"
                        style={{ height: topPad }}
                      />
                    </tr>
                  )}
                  {market === "cross"
                    ? (visibleRows as CrossMarketRow[]).map((r) => {
                        const fk = favoriteKeyForRow(market, r);
                        return (
                          <CrossMarketRowTr
                            key={fk}
                            r={r}
                            onCtrlCopy={onPairCtrlCopy}
                            onOpenChart={openChart}
                            onOpenSpreadChart={openSpreadChart}
                            onOpenDom={openDom}
                            onOpenWorkspace={openWorkspace}
                            isFavorite={favoriteSet.has(fk)}
                            onToggleFavorite={() => {
                              toggleFavoriteKey(market, fk);
                              bumpFavorites();
                            }}
                          />
                        );
                      })
                    : (visibleRows as MarketRow[]).map((r) => {
                        const fk = favoriteKeyForRow(market, r);
                        return (
                          <MarketRowTr
                            key={r.symbol}
                            r={r}
                            onCtrlCopy={onPairCtrlCopy}
                            onOpenChart={openChart}
                            onOpenSpreadChart={openSpreadChart}
                            onOpenDom={openDom}
                            onOpenWorkspace={openWorkspace}
                            onQuickCapture={(sym) => {
                              localStorage.setItem("capture_symbol", sym);
                              window.location.hash = "#/spread-capture";
                            }}
                            domBookMarket={domBookMarket}
                            isFavorite={favoriteSet.has(fk)}
                            onToggleFavorite={() => {
                              toggleFavoriteKey(market, fk);
                              bumpFavorites();
                            }}
                          />
                        );
                      })}
                  {bottomPad > 0 && (
                    <tr
                      aria-hidden
                      className="pointer-events-none border-0 hover:bg-transparent"
                    >
                      <td
                        colSpan={tableColSpan}
                        className="border-0 p-0"
                        style={{ height: bottomPad }}
                      />
                    </tr>
                  )}
                </tbody>
              </table>
            ) : (
              <div className="flex min-h-0 flex-col">
                <TileSortChips
                  market={market}
                  sortBy={sortBy}
                  ascending={ascending}
                  onSort={toggleColumnSort}
                />
                <div
                  className={
                    viewTilesVariant === "charts"
                      ? "grid gap-3 p-3 sm:grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
                      : "grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5"
                  }
                >
                  {showBlockingLoading &&
                    Array.from({ length: 12 }, (_, i) => (
                      <SkeletonCard key={i} />
                    ))}
                  {market === "cross"
                    ? (tilesVisibleRows as CrossMarketRow[]).map((r) => {
                        const fk = favoriteKeyForRow(market, r);
                        return (
                          <CrossMarketTile
                            key={fk}
                            r={r}
                            volSpotLabel={crossVolSpotLabel}
                            volFutLabel={crossVolFutLabel}
                            onCtrlCopy={onPairCtrlCopy}
                            onOpenChart={openChart}
                            onOpenSpreadChart={openSpreadChart}
                            onOpenDom={openDom}
                            onOpenWorkspace={openWorkspace}
                            isFavorite={favoriteSet.has(fk)}
                            onToggleFavorite={() => {
                              toggleFavoriteKey(market, fk);
                              bumpFavorites();
                            }}
                            tilesVariant={viewTilesVariant}
                            isDark={dark}
                          />
                        );
                      })
                    : (tilesVisibleRows as MarketRow[]).map((r) => {
                        const fk = favoriteKeyForRow(market, r);
                        return (
                          <MarketTile
                            key={r.symbol}
                            r={r}
                            volBaseLabel={volBaseLabel}
                            volQuoteLabel={volQuoteLabel}
                            onCtrlCopy={onPairCtrlCopy}
                            onOpenChart={openChart}
                            onOpenSpreadChart={openSpreadChart}
                            onOpenDom={openDom}
                            onOpenWorkspace={openWorkspace}
                            domBookMarket={domBookMarket}
                            isFavorite={favoriteSet.has(fk)}
                            onToggleFavorite={() => {
                              toggleFavoriteKey(market, fk);
                              bumpFavorites();
                            }}
                            tilesVariant={viewTilesVariant}
                            market={market}
                            isDark={dark}
                            exchange={exchange}
                          />
                        );
                      })}
                </div>
              </div>
            )}

            {!showBlockingLoading &&
              filtered.length === 0 &&
              !error &&
              !isFetching && (
                <p className="p-8 text-center text-ink-muted">
                  Нет данных для {EXCHANGE_DISPLAY_NAMES[exchange]}
                </p>
              )}
          </div>
        </div>
      </main>
    </div>
  );
}
