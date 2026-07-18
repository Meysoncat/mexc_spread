import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { readStoredVisual } from "./chartPreferences";
import { PriceChart } from "./components/charts";
import type {
  ChartInterval,
  ChartVisualType,
  Market,
} from "./types";

const INTERVALS: { value: ChartInterval; label: string }[] = [
  { value: "5m", label: "5м" },
  { value: "15m", label: "15м" },
  { value: "1h", label: "1ч" },
  { value: "4h", label: "4ч" },
  { value: "1d", label: "1д" },
];

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
}: ChartModalProps) {
  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [visual, setVisual] = useState<ChartVisualType>(readStoredVisual);

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
          <PriceChart
            symbol={symbol}
            market={market}
            interval={interval}
            visual={visual}
            className="h-[min(55vh,520px)] w-full min-h-[320px]"
          />
        </div>
      </div>
    </div>
  );
}
