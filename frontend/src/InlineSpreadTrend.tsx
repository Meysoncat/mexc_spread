import { useEffect, useRef, useState, memo } from "react";

interface SpreadHistoryTick {
  spread_bps: number | null;
}

interface InlineSpreadTrendProps {
  symbol: string;
  exchange?: string;
  /** Seconds to wait before allowing re-fetch */
  refreshSec?: number;
}

/**
 * Tiny 48×16px SVG sparkline showing recent spread trend.
 * Fetches from /api/spread/history on mount + interval.
 * Only renders when the host element is in viewport (lazy).
 */
export const InlineSpreadTrend = memo(function InlineSpreadTrend({
  symbol,
  refreshSec = 30,
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
          `/api/spread/history?symbol=${encodeURIComponent(symbol)}&max_points=20`,
        );
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        const vals: number[] = (data.ticks || [])
          .map((t: SpreadHistoryTick) => t.spread_bps)
          .filter((v: number | null) => v != null);
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
  }, [visible, symbol, refreshSec]);

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
  const w = 48;
  const h = 16;
  const step = w / (ticks.length - 1);

  const points = ticks
    .map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / range) * h).toFixed(1)}`)
    .join(" ");

  const trend = ticks[ticks.length - 1] >= ticks[0];
  const color = trend ? "#10b981" : "#f43f5e";

  return (
    <td ref={hostRef} className="px-4 py-2.5">
      <svg width={w} height={h} className="inline-block align-middle">
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </td>
  );
});
