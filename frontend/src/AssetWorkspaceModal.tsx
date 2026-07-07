import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
} from "lightweight-charts";
import type {
  IChartApi,
  IPriceLine,
  ISeriesApi,
  UTCTimestamp,
} from "lightweight-charts";
import { X } from "lucide-react";
import { apiUrl } from "./config";
import { readStoredVisual } from "./chartPreferences";
import type {
  ChartInterval,
  ChartVisualType,
  DepthResponse,
  DomMarket,
  KlinesResponse,
  Market,
  OrderbookLevel,
} from "./types";

const INTERVALS: { value: ChartInterval; label: string }[] = [
  { value: "5m", label: "5м" },
  { value: "15m", label: "15м" },
  { value: "1h", label: "1ч" },
  { value: "4h", label: "4ч" },
  { value: "1d", label: "1д" },
];

const DEPTH_LIMITS = [100, 200, 500] as const;
const MAX_PRICE_LINES_PER_SIDE = 10;

function priceFormatFromSample(sample: number) {
  const p = Math.abs(sample);
  if (!Number.isFinite(p) || p === 0) {
    return { type: "price" as const, precision: 4, minMove: 0.0001 };
  }
  if (p >= 10_000) return { type: "price" as const, precision: 2, minMove: 0.01 };
  if (p >= 100) return { type: "price" as const, precision: 2, minMove: 0.01 };
  if (p >= 1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (p >= 0.01) return { type: "price" as const, precision: 6, minMove: 1e-6 };
  return { type: "price" as const, precision: 8, minMove: 1e-8 };
}

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

function topHitsBySide(
  bids: OrderbookLevel[],
  asks: OrderbookLevel[],
  threshold: number,
  maxPerSide: number,
): { side: "bid" | "ask"; level: OrderbookLevel }[] {
  const bh = bids
    .filter((l) => l.notional >= threshold)
    .sort((a, b) => b.notional - a.notional)
    .slice(0, maxPerSide);
  const ah = asks
    .filter((l) => l.notional >= threshold)
    .sort((a, b) => b.notional - a.notional)
    .slice(0, maxPerSide);
  const out: { side: "bid" | "ask"; level: OrderbookLevel }[] = [
    ...bh.map((level) => ({ side: "bid" as const, level })),
    ...ah.map((level) => ({ side: "ask" as const, level })),
  ];
  out.sort((a, b) => b.level.notional - a.level.notional);
  return out;
}

export type WorkspaceOpenContext = {
  chartSymbol: string;
  domSymbol: string;
  domMarket: DomMarket;
  crossFutSymbol: string | null;
};

interface AssetWorkspaceModalProps {
  open: boolean;
  onClose: () => void;
  appMarket: Market;
  ctx: WorkspaceOpenContext | null;
  isDark: boolean;
}

function clearPriceLines(lines: IPriceLine[]) {
  for (const pl of lines) {
    try {
      pl.remove();
    } catch {
      /* ignore */
    }
  }
}

export function AssetWorkspaceModal({
  open,
  onClose,
  appMarket,
  ctx,
  isDark,
}: AssetWorkspaceModalProps) {
  const chartWrapRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<
    ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null
  >(null);
  const densityLinesRef = useRef<IPriceLine[]>([]);
  const [chartReady, setChartReady] = useState(false);

  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [visual, setVisual] = useState<ChartVisualType>(readStoredVisual);
  const [depthLimit, setDepthLimit] = useState<number>(200);
  const [minNotional, setMinNotional] = useState<string>("50000");
  const [showDensityLines, setShowDensityLines] = useState(true);
  const [domAutoRefresh, setDomAutoRefresh] = useState(true);
  const [domLeg, setDomLeg] = useState<DomMarket>("spot");
  const [klinesLoading, setKlinesLoading] = useState(false);
  const [klinesErr, setKlinesErr] = useState<string | null>(null);
  const [depthData, setDepthData] = useState<DepthResponse | null>(null);
  const [depthErr, setDepthErr] = useState<string | null>(null);
  const [depthLoading, setDepthLoading] = useState(false);

  const chartSymbol = ctx?.chartSymbol ?? "";
  const klinesMarket = appMarket === "cross" ? "spot" : appMarket;

  const effDomMarket: DomMarket = useMemo(() => {
    if (!ctx) return "spot";
    return appMarket === "cross" ? domLeg : ctx.domMarket;
  }, [appMarket, ctx, domLeg]);

  const effDomSymbol = useMemo(() => {
    if (!ctx) return "";
    if (appMarket === "cross") {
      return domLeg === "spot"
        ? ctx.chartSymbol
        : (ctx.crossFutSymbol ?? ctx.chartSymbol);
    }
    return ctx.domSymbol;
  }, [appMarket, ctx, domLeg]);

  useEffect(() => {
    if (open && ctx) {
      setDomLeg(ctx.domMarket);
    }
  }, [open, ctx?.domMarket, ctx?.chartSymbol, ctx?.domSymbol]);

  const threshold = useMemo(() => {
    const x = Number.parseFloat(
      minNotional.replace(/\s/g, "").replace(",", "."),
    );
    return Number.isFinite(x) && x > 0 ? x : 0;
  }, [minNotional]);

  const densityList = useMemo(() => {
    if (!depthData?.ok || !depthData.bids?.length || !depthData.asks?.length)
      return [];
    if (threshold <= 0) return [];
    return topHitsBySide(
      depthData.bids,
      depthData.asks,
      threshold,
      MAX_PRICE_LINES_PER_SIDE,
    );
  }, [depthData, threshold]);

  const setVisualPersist = (v: ChartVisualType) => {
    setVisual(v);
    try {
      localStorage.setItem("mexc-ui-chart-visual", v);
    } catch {
      /* ignore */
    }
  };

  const applyLinesToSeries = useCallback(
    (
      series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">,
      hits: { side: "bid" | "ask"; level: OrderbookLevel }[],
    ) => {
      clearPriceLines(densityLinesRef.current);
      densityLinesRef.current = [];
      if (!showDensityLines || hits.length === 0) return;
      const bidC = "#10b981";
      const askC = "#f43f5e";
      for (const { side, level } of hits) {
        const title = `${side === "bid" ? "B" : "A"} ${fmtNum(level.notional, 0)}`;
        const pl = series.createPriceLine({
          price: level.price,
          color: side === "bid" ? bidC : askC,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: title.length > 18 ? title.slice(0, 17) + "…" : title,
        });
        densityLinesRef.current.push(pl);
      }
    },
    [showDensityLines],
  );

  useLayoutEffect(() => {
    if (!open || !chartSymbol || !ctx) return;
    const el = chartWrapRef.current;
    if (!el) return;

    const bg = isDark ? "#1e293b" : "#ffffff";
    const fg = isDark ? "#e2e8f0" : "#0f172a";
    const grid = isDark ? "#334155" : "#e2e8f0";

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: bg },
        textColor: fg,
      },
      grid: {
        vertLines: { color: grid },
        horzLines: { color: grid },
      },
      rightPriceScale: {
        borderColor: grid,
        autoScale: true,
        scaleMargins: { top: 0.08, bottom: 0.08 },
        entireTextOnly: false,
      },
      timeScale: { borderColor: grid },
      width: el.clientWidth,
      height: el.clientHeight,
    });
    chartRef.current = chart;
    seriesRef.current = null;
    setChartReady(false);

    const ro = new ResizeObserver(() => {
      if (!chartWrapRef.current) return;
      chart.applyOptions({
        width: chartWrapRef.current.clientWidth,
        height: chartWrapRef.current.clientHeight,
      });
    });
    ro.observe(el);

    let cancelled = false;
    setKlinesLoading(true);
    setKlinesErr(null);

    const q = new URLSearchParams({
      market: klinesMarket,
      symbol: chartSymbol,
      interval,
    });

    fetch(apiUrl(`/api/klines?${q}`))
      .then((r) => r.json() as Promise<KlinesResponse>)
      .then((data) => {
        if (cancelled) return;
        if (!data.ok) {
          setKlinesErr(data.error ?? "Ошибка свечей");
          return;
        }
        const raw = data.candles ?? [];
        if (raw.length === 0) {
          setKlinesErr("Нет свечей");
          return;
        }
        const lastClose = raw[raw.length - 1]?.close ?? raw[0].close;
        const priceFmt = priceFormatFromSample(lastClose);

        let series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;
        if (visual === "line") {
          series = chart.addSeries(LineSeries, {
            color: "#26a69a",
            lineWidth: 2,
            priceFormat: priceFmt,
            priceLineVisible: true,
            lastValueVisible: true,
          });
          series.setData(
            raw.map((c) => ({
              time: c.time as UTCTimestamp,
              value: c.close,
            })),
          );
        } else {
          series = chart.addSeries(CandlestickSeries, {
            upColor: "#26a69a",
            downColor: "#ef5350",
            borderVisible: false,
            wickUpColor: "#26a69a",
            wickDownColor: "#ef5350",
            priceFormat: priceFmt,
          });
          series.setData(
            raw.map((c) => ({
              time: c.time as UTCTimestamp,
              open: c.open,
              high: c.high,
              low: c.low,
              close: c.close,
            })),
          );
        }
        seriesRef.current = series;
        chart.timeScale().fitContent();
        chart.priceScale("right").applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.08, bottom: 0.08 },
        });
        setChartReady(true);
      })
      .catch((e) => {
        if (!cancelled) setKlinesErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setKlinesLoading(false);
      });

    return () => {
      cancelled = true;
      ro.disconnect();
      clearPriceLines(densityLinesRef.current);
      densityLinesRef.current = [];
      seriesRef.current = null;
      chartRef.current = null;
      chart.remove();
      setChartReady(false);
    };
  }, [
    open,
    chartSymbol,
    ctx,
    interval,
    isDark,
    klinesMarket,
    visual,
  ]);

  useEffect(() => {
    if (!chartReady || !seriesRef.current) return;
    applyLinesToSeries(seriesRef.current, densityList);
  }, [chartReady, densityList, applyLinesToSeries]);

  const fetchDepth = useCallback(
    async (nocache: boolean) => {
      if (!open || !effDomSymbol) return;
      setDepthLoading(true);
      setDepthErr(null);
      try {
        const q = new URLSearchParams({
          market: effDomMarket,
          symbol: effDomSymbol,
          limit: String(depthLimit),
        });
        if (nocache) q.set("nocache", "true");
        const r = await fetch(apiUrl(`/api/depth?${q}`));
        const j: unknown = await r.json();
        const d = j as DepthResponse;
        if (!d.ok) {
          setDepthErr(d.error ?? "Ошибка стакана");
          setDepthData(null);
        } else {
          setDepthData(d);
        }
      } catch (e) {
        setDepthErr(e instanceof Error ? e.message : String(e));
        setDepthData(null);
      } finally {
        setDepthLoading(false);
      }
    },
    [open, effDomMarket, effDomSymbol, depthLimit],
  );

  useEffect(() => {
    if (!open || !effDomSymbol) return;
    void fetchDepth(false);
  }, [open, effDomSymbol, effDomMarket, depthLimit, fetchDepth]);

  useEffect(() => {
    if (!open || !effDomSymbol || !domAutoRefresh) return;
    const t = window.setInterval(() => void fetchDepth(false), 2500);
    return () => window.clearInterval(t);
  }, [open, effDomSymbol, domAutoRefresh, fetchDepth]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const applyMedianMultiplier = () => {
    if (!depthData?.bids?.length && !depthData?.asks?.length) return;
    const all = [
      ...(depthData?.bids ?? []).map((l) => l.notional),
      ...(depthData?.asks ?? []).map((l) => l.notional),
    ];
    const med = median(all);
    const next = med * 2;
    setMinNotional(next >= 1 ? String(Math.round(next)) : String(next));
  };

  if (!open || !ctx) return null;

  const titleSuffix =
    appMarket === "cross"
      ? "базис · график спота"
      : appMarket === "spot"
        ? "спот"
        : "фьючерсы";

  const maxBidN = depthData?.bids?.length
    ? Math.max(...depthData.bids.map((l) => l.notional), 1e-12)
    : 1;
  const maxAskN = depthData?.asks?.length
    ? Math.max(...depthData.asks.map((l) => l.notional), 1e-12)
    : 1;

  return (
    <div
      className="fixed inset-0 z-[70] flex flex-col bg-surface-elevated text-ink"
      role="dialog"
      aria-modal="true"
      aria-labelledby="workspace-title"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-line px-4 py-3">
        <h1 id="workspace-title" className="min-w-0 text-lg font-semibold">
          <span className="font-mono">{chartSymbol}</span>{" "}
          <span className="text-sm font-normal text-ink-muted">
            ({titleSuffix} · рабочее место)
          </span>
        </h1>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {appMarket === "cross" && ctx.crossFutSymbol ? (
            <div className="flex rounded-lg bg-surface p-0.5 ring-1 ring-line">
              <button
                type="button"
                onClick={() => setDomLeg("spot")}
                className={`rounded-md px-2 py-1 text-xs font-medium ${
                  domLeg === "spot"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                Стакан спот
              </button>
              <button
                type="button"
                onClick={() => setDomLeg("futures")}
                className={`rounded-md px-2 py-1 text-xs font-medium ${
                  domLeg === "futures"
                    ? "bg-accent text-white shadow"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                Стакан фьюч
              </button>
            </div>
          ) : null}
          <label className="flex items-center gap-1 text-xs text-ink-muted">
            График
            <select
              value={visual}
              onChange={(e) =>
                setVisualPersist(e.target.value as ChartVisualType)
              }
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="candle">Свечи</option>
              <option value="line">Линия</option>
            </select>
          </label>
          <select
            value={interval}
            onChange={(e) => setInterval(e.target.value as ChartInterval)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
          >
            {INTERVALS.map((x) => (
              <option key={x.value} value={x.value}>
                {x.label}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer items-center gap-1 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={showDensityLines}
              onChange={(e) => setShowDensityLines(e.target.checked)}
              className="rounded border-line text-accent focus:ring-accent"
            />
            Линии плотностей
          </label>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line p-2 text-ink transition hover:bg-surface"
            aria-label="Закрыть"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col lg:flex-row">
        <div className="relative min-h-[42vh] flex-1 border-b border-line lg:min-h-0 lg:border-b-0 lg:border-r">
          {klinesErr && (
            <p className="absolute left-3 top-3 z-10 max-w-[90%] rounded bg-surface-elevated/95 px-2 py-1 text-sm text-red-600 dark:text-red-400">
              {klinesErr}
            </p>
          )}
          {klinesLoading && (
            <p className="absolute left-3 top-3 z-10 text-sm text-ink-muted">
              Загрузка графика…
            </p>
          )}
          <div ref={chartWrapRef} className="absolute inset-0" />
        </div>

        <aside className="flex max-h-[50vh] w-full shrink-0 flex-col overflow-hidden bg-surface lg:max-h-none lg:w-[460px] lg:shrink-0">
          <div className="min-h-0 flex flex-1 flex-col overflow-hidden">
          <div className="shrink-0 space-y-2 border-b border-line p-3">
            <p className="text-xs font-semibold text-ink-muted">
              Стакан{" "}
              <span className="font-mono text-ink">
                {effDomSymbol}
              </span>{" "}
              <span className="text-ink-muted">({effDomMarket})</span>
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={depthLimit}
                onChange={(e) => setDepthLimit(Number(e.target.value))}
                className="rounded-lg border border-line bg-surface-elevated px-2 py-1 text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
              >
                {DEPTH_LIMITS.map((n) => (
                  <option key={n} value={n}>
                    Глубина {n}
                  </option>
                ))}
              </select>
              <label className="flex cursor-pointer items-center gap-1 text-[11px] text-ink-muted">
                <input
                  type="checkbox"
                  checked={domAutoRefresh}
                  onChange={(e) => setDomAutoRefresh(e.target.checked)}
                  className="rounded border-line text-accent focus:ring-accent"
                />
                Авто 2.5 с
              </label>
              <button
                type="button"
                onClick={() => void fetchDepth(true)}
                className="rounded border border-line bg-surface px-2 py-1 text-[11px] font-medium hover:bg-accent/10"
              >
                Обновить DOM
              </button>
            </div>
            {depthErr && (
              <p className="text-xs text-red-600 dark:text-red-400">{depthErr}</p>
            )}
            {depthData?.ok && (
              <p className="font-mono text-[11px] text-ink-muted">
                Bid {depthData.best_bid != null ? fmtNum(depthData.best_bid, 6) : "—"}{" "}
                · Ask{" "}
                {depthData.best_ask != null ? fmtNum(depthData.best_ask, 6) : "—"} · Mid{" "}
                {depthData.mid != null ? fmtNum(depthData.mid, 6) : "—"}
              </p>
            )}
          </div>

          <section className="shrink-0 border-b border-line bg-surface-elevated/50 p-3">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Плотности на графике
            </h2>
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <label
                  className="mb-0.5 block text-[10px] text-ink-muted"
                  htmlFor="ws-min-notional"
                >
                  Мин. USDT≈ уровня
                </label>
                <input
                  id="ws-min-notional"
                  type="text"
                  inputMode="decimal"
                  value={minNotional}
                  onChange={(e) => setMinNotional(e.target.value)}
                  className="w-32 rounded-lg border border-line bg-surface px-2 py-1 font-mono text-xs text-ink outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <button
                type="button"
                onClick={applyMedianMultiplier}
                className="rounded-lg border border-line bg-surface px-2 py-1 text-[11px] font-medium hover:bg-accent/10"
              >
                2× медианы
              </button>
            </div>
            <p className="mt-2 text-[10px] text-ink-muted">
              До {MAX_PRICE_LINES_PER_SIDE} уровней на сторону; пунктир на графике.
              {appMarket === "cross" && domLeg === "futures"
                ? " Цены фьючерса на графике спота — для ориентира по базису."
                : null}
            </p>
            {densityList.length > 0 ? (
              <ul className="mt-2 max-h-24 space-y-0.5 overflow-y-auto scroll-thin font-mono text-[10px] text-ink">
                {densityList.map(({ side, level }) => (
                  <li key={`${side}-${level.price}-${level.notional}`}>
                    <span
                      className={
                        side === "bid"
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      }
                    >
                      {side === "bid" ? "B" : "A"}
                    </span>{" "}
                    {fmtNum(level.price, 8)} · {fmtNum(level.notional, 0)}
                  </li>
                ))}
              </ul>
            ) : threshold > 0 && depthData?.ok ? (
              <p className="mt-2 text-[10px] text-ink-muted">
                Нет уровней ≥ порога — снизьте значение или увеличьте глубину.
              </p>
            ) : null}
            {depthLoading && (
              <p className="mt-1 text-[10px] text-ink-muted">Стакан…</p>
            )}
          </section>

          <div className="min-h-0 flex-1 overflow-hidden p-2">
            {depthData?.ok ? (
              <div className="grid h-full min-h-[200px] grid-cols-2 gap-2 overflow-hidden">
                <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-surface">
                  <p className="shrink-0 bg-surface-elevated py-1 text-center text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    Bids
                  </p>
                  <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
                    <table className="w-full font-mono text-[10px]">
                      <tbody>
                        {depthData.bids.map((l) => {
                          const hi =
                            threshold > 0 && l.notional >= threshold;
                          return (
                            <tr
                              key={`b-${l.price}`}
                              className={
                                hi ? "bg-emerald-500/15" : "hover:bg-accent/5"
                              }
                            >
                              <td className="relative px-1 py-0.5 text-emerald-700 dark:text-emerald-300">
                                <span
                                  className="absolute inset-y-0 left-0 bg-emerald-500/20"
                                  style={{
                                    width: `${Math.min(100, (l.notional / maxBidN) * 100)}%`,
                                  }}
                                />
                                <span className="relative">{fmtNum(l.price, 8)}</span>
                              </td>
                              <td className="relative px-1 py-0.5 text-right text-ink-muted">
                                <span className="relative">
                                  {fmtNum(l.notional, 0)}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-surface">
                  <p className="shrink-0 bg-surface-elevated py-1 text-center text-[10px] font-medium text-rose-600 dark:text-rose-400">
                    Asks
                  </p>
                  <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
                    <table className="w-full font-mono text-[10px]">
                      <tbody>
                        {depthData.asks.map((l) => {
                          const hi =
                            threshold > 0 && l.notional >= threshold;
                          return (
                            <tr
                              key={`a-${l.price}`}
                              className={
                                hi ? "bg-rose-500/15" : "hover:bg-accent/5"
                              }
                            >
                              <td className="relative px-1 py-0.5 text-rose-700 dark:text-rose-300">
                                <span
                                  className="absolute inset-y-0 left-0 bg-rose-500/20"
                                  style={{
                                    width: `${Math.min(100, (l.notional / maxAskN) * 100)}%`,
                                  }}
                                />
                                <span className="relative">{fmtNum(l.price, 8)}</span>
                              </td>
                              <td className="relative px-1 py-0.5 text-right text-ink-muted">
                                <span className="relative">
                                  {fmtNum(l.notional, 0)}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            ) : (
              <p className="p-4 text-center text-xs text-ink-muted">
                Нет данных стакана
              </p>
            )}
          </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
