import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { apiUrl } from "./config";

interface BasisChartProps {
  symbol: string;
  exchangeCombo: string;
  entryThresholdBps?: number;
  exitThresholdBps?: number;
}

type Interval = "1h" | "4h" | "24h" | "7d";

const INTERVAL_HOURS: Record<Interval, number> = {
  "1h": 1,
  "4h": 4,
  "24h": 24,
  "7d": 168,
};

export function BasisChart({
  symbol,
  exchangeCombo,
  entryThresholdBps = 30,
  exitThresholdBps = 5,
}: BasisChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [interval, setInterval] = useState<Interval>("24h");
  const [loading, setLoading] = useState(false);

  // Create chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 300,
      layout: {
        background: { color: "#1a1a2e" },
        textColor: "#a0a0b0",
      },
      grid: {
        vertLines: { color: "#2a2a3e" },
        horzLines: { color: "#2a2a3e" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 0,
      },
    });

    const series = chart.addSeries(LineSeries, {
      color: "#4fc3f7",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(1) + " bps" },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Fetch data and update chart
  const fetchData = useCallback(async () => {
    if (!seriesRef.current) return;
    setLoading(true);

    const hours = INTERVAL_HOURS[interval];
    const since = new Date(Date.now() - hours * 3600 * 1000).toISOString();

    try {
      const url = apiUrl(
        `/api/futures-arb/basis-history?symbol=${symbol}&exchange_combo=${exchangeCombo}&since=${since}&limit=2000`
      );
      const r = await fetch(url);
      if (!r.ok) return;
      const d = await r.json();
      if (!d.ok) return;

      const rows: { timestamp: string; basis_bps: number }[] = d.rows ?? [];

      // Convert to chart data (sorted by time ascending)
      const data = rows
        .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
        .map(row => ({
          time: (new Date(row.timestamp).getTime() / 1000) as UTCTimestamp,
          value: row.basis_bps,
        }));

      seriesRef.current.setData(data);

      // Add threshold lines
      if (chartRef.current) {
        seriesRef.current.createPriceLine({
          price: entryThresholdBps,
          color: "#4caf50",
          lineWidth: 1,
          lineStyle: 2, // Dashed
          axisLabelVisible: true,
          title: "Entry",
        });
        seriesRef.current.createPriceLine({
          price: exitThresholdBps,
          color: "#ff9800",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: "Exit",
        });
      }

      chartRef.current?.timeScale().fitContent();
    } catch (e) {
      console.error("BasisChart fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [symbol, exchangeCombo, interval, entryThresholdBps, exitThresholdBps]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="space-y-2">
      {/* Interval selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">Интервал:</span>
        {(["1h", "4h", "24h", "7d"] as Interval[]).map(iv => (
          <button
            key={iv}
            onClick={() => setInterval(iv)}
            className={`px-2 py-0.5 rounded text-xs ${
              interval === iv
                ? "bg-blue-600 text-white"
                : "bg-gray-700 text-gray-300 hover:bg-gray-600"
            }`}
          >
            {iv}
          </button>
        ))}
        {loading && <span className="text-xs text-gray-500 ml-2">Loading...</span>}
      </div>

      {/* Chart container */}
      <div ref={containerRef} className="w-full rounded border border-gray-700" />

      {/* Legend */}
      <div className="flex gap-4 text-xs text-gray-400">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-cyan-400 inline-block" /> Basis (bps)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-green-500 inline-block border-dashed" /> Entry threshold
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-orange-400 inline-block border-dashed" /> Exit threshold
        </span>
      </div>
    </div>
  );
}
