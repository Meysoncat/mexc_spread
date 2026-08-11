import { useEffect, useRef, useState, memo } from "react";

interface SpreadHistoryTick {
  spread_bps: number | null;
  bid?: number;
  ask?: number;
}

interface InlineSpreadTrendProps {
  symbol: string;
  exchange?: string;
  refreshSec?: number;
  /** Show price trend instead of spread trend */
  mode?: "spread" | "price";
  width?: number;
  height?: number;
}

/**
 * Compact inline sparkline showing recent spread or price trend.
 * 48×16px by default, lazy-loaded via IntersectionObserver.
 */
export const InlineSpreadTrend = memo(function InlineSpreadTrend({
  symbol,
  refreshSec = 30,
  mode = "spread",
  width = 48,
  height = 16,
}: InlineSpreadTrendProps) {
  const hostRef = useRef<HTMLTableCellElement | null>(null);
  const [ticks, setTicks] = useState<number[]>([]);
  const [visible, setVisible] = useState(false);

  // Lazy load via IntersectionObserver
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ob = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          ob.disconnect();
        }
      },
      { rootMargin: "100px 0px", threshold: 0.01 },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, []);

  // Fetch spread history
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;

    const fetchTicks = async () => {
      try {
        const r = await fetch(
          `/api/spread/history?symbol=${encodeURIComponent(symbol)}&max_points=30`,
        );
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        const ticks = data.ticks || [];
        let vals: number[];
        if (mode === "price") {
          vals = ticks
            .map((t: SpreadHistoryTick) => t.bid != null && t.ask != null ? (t.bid + t.ask) / 2 : null)
            .filter((v: number | null) => v != null);
        } else {
          vals = ticks
            .map((t: SpreadHistoryTick) => t.spread_bps)
            .filter((v: number | null) => v != null);
        }
        setTicks(vals);
      } catch {
        /* best-effort */
      }
    };

    fetchTicks();
    const id = setInterval(fetchTicks, refreshSec * 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [visible, symbol, refreshSec, mode]);

  // Render sparkline
  if (!visible || ticks.length < 3) {
    return (
      <td ref={hostRef} className="px-4 py-2.5">
        <span className="text-ink-muted/30">—</span>
      </td>
    );
  }

  const min = Math.min(...ticks);
  const max = Math.max(...ticks);
  const range = max - min || 1;
  const w = width;
  const h = height;
  const pad = 1;

  // Build SVG polyline
  const points = ticks
    .map((v, i) => {
      const x = pad + (i / (ticks.length - 1)) * (w - 2 * pad);
      const y = pad + (1 - (v - min) / range) * (h - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  // Color based on trend (last vs first)
  const trend = ticks[ticks.length - 1] - ticks[0];
  const color =
    mode === "spread"
      ? trend > 0
        ? "#22c55e"
        : trend < 0
          ? "#ef4444"
          : "#94a3b8"
      : trend >= 0
        ? "#22c55e"
        : "#ef4444";

  return (
    <td ref={hostRef} className="px-4 py-2.5">
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        className="block"
        style={{ minWidth: w, minHeight: h }}
      >
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </td>
  );
});
