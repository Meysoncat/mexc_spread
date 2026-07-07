import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
} from "lightweight-charts";
import type { ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { X } from "lucide-react";
import { apiUrl } from "./config";
import { readStoredVisual } from "./chartPreferences";
import type {
  ChartInterval,
  ChartVisualType,
  KlinesResponse,
  Market,
} from "./types";

const INTERVALS: { value: ChartInterval; label: string }[] = [
  { value: "5m", label: "5м" },
  { value: "15m", label: "15м" },
  { value: "1h", label: "1ч" },
  { value: "4h", label: "4ч" },
  { value: "1d", label: "1д" },
];

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

interface ChartModalProps {
  open: boolean;
  onClose: () => void;
  market: Market;
  symbol: string | null;
  isDark: boolean;
}

export function ChartModal({
  open,
  onClose,
  market,
  symbol,
  isDark,
}: ChartModalProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [visual, setVisual] = useState<ChartVisualType>(readStoredVisual);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const setVisualPersist = (v: ChartVisualType) => {
    setVisual(v);
    try {
      localStorage.setItem("mexc-ui-chart-visual", v);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useLayoutEffect(() => {
    if (!open || !symbol) return;
    const el = wrapRef.current;
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
        scaleMargins: { top: 0.12, bottom: 0.12 },
        entireTextOnly: false,
      },
      timeScale: { borderColor: grid },
      width: el.clientWidth,
      height: el.clientHeight,
    });

    const ro = new ResizeObserver(() => {
      if (!wrapRef.current) return;
      chart.applyOptions({
        width: wrapRef.current.clientWidth,
        height: wrapRef.current.clientHeight,
      });
    });
    ro.observe(el);

    let cancelled = false;
    setLoading(true);
    setErr(null);

    const klinesMarket = market === "cross" ? "spot" : market;
    const q = new URLSearchParams({
      market: klinesMarket,
      symbol,
      interval,
    });

    fetch(apiUrl(`/api/klines?${q}`))
      .then((r) => r.json() as Promise<KlinesResponse>)
      .then((data) => {
        if (cancelled) return;
        if (!data.ok) {
          setErr(data.error ?? "Ошибка загрузки");
          return;
        }
        const raw = data.candles ?? [];
        if (raw.length === 0) {
          setErr("Нет свечей");
          return;
        }

        const lastClose = raw[raw.length - 1]?.close ?? raw[0].close;
        const priceFmt = priceFormatFromSample(lastClose);

        if (visual === "line") {
          const series: ISeriesApi<"Line"> = chart.addSeries(LineSeries, {
            color: "#26a69a",
            lineWidth: 2,
            priceFormat: priceFmt,
            priceLineVisible: true,
            lastValueVisible: true,
          });
          const lineData = raw.map((c) => ({
            time: c.time as UTCTimestamp,
            value: c.close,
          }));
          series.setData(lineData);
        } else {
          const series: ISeriesApi<"Candlestick"> = chart.addSeries(
            CandlestickSeries,
            {
              upColor: "#26a69a",
              downColor: "#ef5350",
              borderVisible: false,
              wickUpColor: "#26a69a",
              wickDownColor: "#ef5350",
              priceFormat: priceFmt,
            },
          );
          const candleData = raw.map((c) => ({
            time: c.time as UTCTimestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }));
          series.setData(candleData);
        }

        chart.timeScale().fitContent();
        chart.priceScale("right").applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.12, bottom: 0.12 },
        });
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      ro.disconnect();
      chart.remove();
    };
  }, [open, symbol, market, interval, isDark, visual]);

  if (!open || !symbol) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="chart-modal-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-2xl border border-line bg-surface-elevated shadow-xl dark:shadow-panel-dark"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2
            id="chart-modal-title"
            className="text-lg font-semibold text-ink"
          >
            {symbol}{" "}
            <span className="text-sm font-normal text-ink-muted">
              (
              {market === "cross"
                ? "спот (график для ноги спота)"
                : market === "spot"
                  ? "спот"
                  : "фьючерсы"}
              , MEXC)
            </span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-ink-muted">
              <span className="shrink-0">Тип</span>
              <select
                value={visual}
                onChange={(e) =>
                  setVisualPersist(e.target.value as ChartVisualType)
                }
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="candle">Свечи</option>
                <option value="line">Линия (close)</option>
              </select>
            </label>
            <select
              value={interval}
              onChange={(e) =>
                setInterval(e.target.value as ChartInterval)
              }
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
            >
              {INTERVALS.map((x) => (
                <option key={x.value} value={x.value}>
                  {x.label}
                </option>
              ))}
            </select>
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
        <div className="relative p-2">
          {err && (
            <p className="mb-2 px-2 text-sm text-red-600 dark:text-red-400">
              {err}
            </p>
          )}
          {loading && (
            <p className="absolute left-4 top-4 z-10 text-sm text-ink-muted">
              Загрузка…
            </p>
          )}
          <div
            ref={wrapRef}
            className="h-[min(55vh,520px)] w-full min-h-[320px]"
          />
        </div>
      </div>
    </div>
  );
}
