import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListOrdered, X } from "lucide-react";
import { apiUrl } from "./config";
import type { DepthResponse, DomMarket, OrderbookLevel } from "./types";

const LIMIT_OPTIONS = [50, 100, 200, 500] as const;

function fmtNum(n: number, frac: number): string {
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: frac,
  });
}

function median(nums: number[]): number {
  if (nums.length === 0) return 0;
  const s = [...nums].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

interface DomModalProps {
  open: boolean;
  onClose: () => void;
  market: DomMarket;
  symbol: string | null;
}

export function DomModal({ open, onClose, market, symbol }: DomModalProps) {
  const [limit, setLimit] = useState<number>(100);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<DepthResponse | null>(null);
  const [minNotional, setMinNotional] = useState<string>("50000");
  const [highlightPrice, setHighlightPrice] = useState<number | null>(null);
  const bidWrapRef = useRef<HTMLDivElement | null>(null);
  const askWrapRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(
    async (nocache: boolean) => {
      if (!symbol) return;
      setLoading(true);
      setErr(null);
      try {
        const q = new URLSearchParams({
          market,
          symbol,
          limit: String(limit),
        });
        if (nocache) q.set("nocache", "true");
        const r = await fetch(apiUrl(`/api/depth?${q}`));
        const j: unknown = await r.json();
        const d = j as DepthResponse;
        if (!d.ok) {
          setErr(d.error ?? "Ошибка стакана");
          setData(null);
        } else {
          setData(d);
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [market, symbol, limit],
  );

  useEffect(() => {
    if (!open || !symbol) return;
    void load(false);
  }, [open, symbol, market, limit, load]);

  useEffect(() => {
    if (!open || !symbol || !autoRefresh) return;
    const t = window.setInterval(() => void load(false), 2500);
    return () => window.clearInterval(t);
  }, [open, symbol, autoRefresh, load]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const threshold = useMemo(() => {
    const x = Number.parseFloat(minNotional.replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(x) && x > 0 ? x : 0;
  }, [minNotional]);

  const densityHits = useMemo(() => {
    if (!data?.ok || !data.bids?.length || !data.asks?.length) return [];
    const hits: { side: "bid" | "ask"; level: OrderbookLevel }[] = [];
    for (const level of data.bids) {
      if (level.notional >= threshold) hits.push({ side: "bid", level });
    }
    for (const level of data.asks) {
      if (level.notional >= threshold) hits.push({ side: "ask", level });
    }
    hits.sort((a, b) => b.level.notional - a.level.notional);
    return hits;
  }, [data, threshold]);

  const maxBidNotional = useMemo(() => {
    if (!data?.bids?.length) return 0;
    return Math.max(...data.bids.map((l) => l.notional), 1e-12);
  }, [data]);

  const maxAskNotional = useMemo(() => {
    if (!data?.asks?.length) return 0;
    return Math.max(...data.asks.map((l) => l.notional), 1e-12);
  }, [data]);

  const applyMedianMultiplier = () => {
    if (!data?.bids?.length && !data?.asks?.length) return;
    const all = [
      ...data.bids.map((l) => l.notional),
      ...data.asks.map((l) => l.notional),
    ];
    const med = median(all);
    const next = med * 2;
    setMinNotional(next >= 1 ? String(Math.round(next)) : String(next));
  };

  const scrollToPrice = (side: "bid" | "ask", price: number) => {
    setHighlightPrice(price);
    const wrap = side === "bid" ? bidWrapRef.current : askWrapRef.current;
    const el = wrap?.querySelector(`[data-ob-price="${price}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    window.setTimeout(() => setHighlightPrice(null), 1400);
  };

  if (!open || !symbol) return null;

  const mLabel = market === "spot" ? "спот" : "фьючерсы";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dom-modal-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2
            id="dom-modal-title"
            className="flex items-center gap-2 text-lg font-semibold text-ink"
          >
            <ListOrdered className="h-5 w-5 shrink-0 text-accent" strokeWidth={2} />
            {symbol}
            <span className="text-sm font-normal text-ink-muted">
              ({mLabel}, DOM · MEXC)
            </span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-ink-muted">
              <span>Глубина</span>
              <select
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              >
                {LIMIT_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-line text-accent focus:ring-accent"
              />
              Авто 2.5 с
            </label>
            <button
              type="button"
              onClick={() => void load(true)}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs font-medium text-ink transition hover:bg-accent/10"
            >
              Обновить
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-line p-2 text-ink transition hover:bg-surface"
              aria-label="Закрыть"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {err && (
            <p className="mb-3 text-sm text-red-600 dark:text-red-400">{err}</p>
          )}
          {loading && !data && (
            <p className="text-sm text-ink-muted">Загрузка стакана…</p>
          )}
          {data?.ok && (
            <>
              <div className="mb-3 flex flex-wrap gap-4 text-xs font-mono text-ink-muted">
                <span>
                  Bid:{" "}
                  <span className="text-emerald-600 dark:text-emerald-400">
                    {data.best_bid != null ? fmtNum(data.best_bid, 8) : "—"}
                  </span>
                </span>
                <span>
                  Ask:{" "}
                  <span className="text-rose-600 dark:text-rose-400">
                    {data.best_ask != null ? fmtNum(data.best_ask, 8) : "—"}
                  </span>
                </span>
                <span>Mid: {data.mid != null ? fmtNum(data.mid, 8) : "—"}</span>
                {data.cache_hit ? (
                  <span className="text-ink-muted/70">кэш API</span>
                ) : null}
              </div>

              {data.vwap && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-2 text-xs text-ink-muted">
                  <span className="font-semibold text-ink">VWAP:</span>
                  {data.vwap.vwap_buy_price != null && (
                    <span>
                      Buy{" "}
                      <span className="font-mono text-rose-500">
                        {fmtNum(data.vwap.vwap_buy_price, 6)}
                      </span>
                      {data.vwap.slippage_buy_bps != null && (
                        <span className="ml-1 text-red-400">
                          ({data.vwap.slippage_buy_bps.toFixed(1)}bps)
                        </span>
                      )}
                    </span>
                  )}
                  {data.vwap.vwap_sell_price != null && (
                    <span>
                      Sell{" "}
                      <span className="font-mono text-emerald-500">
                        {fmtNum(data.vwap.vwap_sell_price, 6)}
                      </span>
                      {data.vwap.slippage_sell_bps != null && (
                        <span className="ml-1 text-red-400">
                          ({data.vwap.slippage_sell_bps.toFixed(1)}bps)
                        </span>
                      )}
                    </span>
                  )}
                  {data.vwap.depth_levels > 0 && (
                    <span>L{data.vwap.depth_levels}</span>
                  )}
                </div>
              )}

              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <p className="mb-1 text-center text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    Покупка (bids)
                  </p>
                  <div
                    ref={bidWrapRef}
                    className="max-h-[min(52vh,420px)] overflow-y-auto scroll-thin rounded-xl border border-line bg-surface"
                  >
                    <table className="w-full font-mono text-[11px]">
                      <thead className="sticky top-0 bg-surface-elevated text-ink-muted">
                        <tr>
                          <th className="px-2 py-1.5 text-left font-medium">Цена</th>
                          <th className="px-2 py-1.5 text-right font-medium">Qty</th>
                          <th className="px-2 py-1.5 text-right font-medium">USDT≈</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.bids.map((l) => {
                          const dense =
                            threshold > 0 && l.notional >= threshold;
                          const hot =
                            highlightPrice != null &&
                            Math.abs(l.price - highlightPrice) < 1e-12;
                          return (
                            <tr
                              key={`b-${l.price}`}
                              data-ob-price={l.price}
                              className={`border-t border-line/40 transition ${
                                hot
                                  ? "bg-accent/25"
                                  : dense
                                    ? "bg-emerald-500/15"
                                    : "hover:bg-accent/5"
                              }`}
                            >
                              <td className="relative px-2 py-1 text-emerald-700 dark:text-emerald-300">
                                <span
                                  className="absolute inset-y-0 left-0 bg-emerald-500/25"
                                  style={{
                                    width: `${Math.min(100, (l.notional / maxBidNotional) * 100)}%`,
                                  }}
                                />
                                <span className="relative">{fmtNum(l.price, 8)}</span>
                              </td>
                              <td className="relative px-2 py-1 text-right text-ink">
                                <span className="relative">{fmtNum(l.qty, 6)}</span>
                              </td>
                              <td className="relative px-2 py-1 text-right text-ink-muted">
                                <span className="relative">{fmtNum(l.notional, 0)}</span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-center text-xs font-medium text-rose-600 dark:text-rose-400">
                    Продажа (asks)
                  </p>
                  <div
                    ref={askWrapRef}
                    className="max-h-[min(52vh,420px)] overflow-y-auto scroll-thin rounded-xl border border-line bg-surface"
                  >
                    <table className="w-full font-mono text-[11px]">
                      <thead className="sticky top-0 bg-surface-elevated text-ink-muted">
                        <tr>
                          <th className="px-2 py-1.5 text-left font-medium">Цена</th>
                          <th className="px-2 py-1.5 text-right font-medium">Qty</th>
                          <th className="px-2 py-1.5 text-right font-medium">USDT≈</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.asks.map((l) => {
                          const dense =
                            threshold > 0 && l.notional >= threshold;
                          const hot =
                            highlightPrice != null &&
                            Math.abs(l.price - highlightPrice) < 1e-12;
                          return (
                            <tr
                              key={`a-${l.price}`}
                              data-ob-price={l.price}
                              className={`border-t border-line/40 transition ${
                                hot
                                  ? "bg-accent/25"
                                  : dense
                                    ? "bg-rose-500/15"
                                    : "hover:bg-accent/5"
                              }`}
                            >
                              <td className="relative px-2 py-1 text-rose-700 dark:text-rose-300">
                                <span
                                  className="absolute inset-y-0 left-0 bg-rose-500/25"
                                  style={{
                                    width: `${Math.min(100, (l.notional / maxAskNotional) * 100)}%`,
                                  }}
                                />
                                <span className="relative">{fmtNum(l.price, 8)}</span>
                              </td>
                              <td className="relative px-2 py-1 text-right text-ink">
                                <span className="relative">{fmtNum(l.qty, 6)}</span>
                              </td>
                              <td className="relative px-2 py-1 text-right text-ink-muted">
                                <span className="relative">{fmtNum(l.notional, 0)}</span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <section
                className="mt-6 rounded-xl border-2 border-accent/25 bg-surface px-3 py-4 shadow-sm dark:border-accent/30 dark:bg-surface/80"
                aria-labelledby="dom-density-heading"
              >
                <h3
                  id="dom-density-heading"
                  className="mb-1 text-sm font-semibold text-ink"
                >
                  Плотности
                </h3>
                <p className="mb-3 text-[11px] leading-relaxed text-ink-muted">
                  Отдельный блок: поиск уровней с крупной нотацией (price × qty, USDT≈).
                  Подсветка в таблицах стакана выше и переход к цене по клику в списке.
                  Для фьючерсов qty — как в API MEXC (контракты).
                </p>
                <div className="flex flex-wrap items-end gap-2">
                  <div>
                    <label
                      className="mb-0.5 block text-[10px] font-medium text-ink-muted"
                      htmlFor="dom-min-notional"
                    >
                      Мин. нотация уровня (USDT≈)
                    </label>
                    <input
                      id="dom-min-notional"
                      type="text"
                      inputMode="decimal"
                      value={minNotional}
                      onChange={(e) => setMinNotional(e.target.value)}
                      className="w-40 rounded-lg border border-line bg-surface-elevated px-2 py-1.5 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={applyMedianMultiplier}
                    className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs font-medium text-ink transition hover:bg-accent/10"
                    title="Порог = 2 × медиана нотации по видимым уровням стакана"
                  >
                    Порог 2× медианы
                  </button>
                </div>
                <p className="mt-3 text-[10px] font-medium uppercase tracking-wide text-ink-muted">
                  Найденные уровни
                </p>
                {densityHits.length > 0 ? (
                  <ul className="mt-1.5 max-h-36 space-y-1 overflow-y-auto scroll-thin rounded-lg border border-line bg-surface-elevated p-2">
                    {densityHits.map(({ side, level }) => (
                      <li key={`${side}-${level.price}`}>
                        <button
                          type="button"
                          onClick={() => scrollToPrice(side, level.price)}
                          className="w-full rounded px-1.5 py-1 text-left font-mono text-[11px] text-ink transition hover:bg-accent/10"
                        >
                          <span
                            className={
                              side === "bid"
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-rose-600 dark:text-rose-400"
                            }
                          >
                            {side === "bid" ? "BID" : "ASK"}
                          </span>{" "}
                          {fmtNum(level.price, 8)} · {fmtNum(level.notional, 0)} USDT≈
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : threshold > 0 && data ? (
                  <p className="mt-1.5 text-[11px] text-ink-muted">
                    Нет уровней ≥ {fmtNum(threshold, 0)} — снизьте порог или увеличьте
                    глубину стакана.
                  </p>
                ) : (
                  <p className="mt-1.5 text-[11px] text-ink-muted">
                    Задайте порог &gt; 0 или нажмите «Порог 2× медианы» после загрузки
                    стакана.
                  </p>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
