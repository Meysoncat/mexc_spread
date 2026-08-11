import { useCallback, useEffect, useRef, useState } from "react";
import { Layers, Play, Square } from "lucide-react";
import { apiUrl } from "../../config";

interface HeatmapLevel {
  price: number;
  qty: number;
  notional: number;
}

interface HeatmapSnapshot {
  ok: boolean;
  timestamp_ms: number;
  mid: number;
  best_bid: number;
  best_ask: number;
  bids: HeatmapLevel[];
  asks: HeatmapLevel[];
}

interface HeatmapFrame {
  timestamp_ms: number;
  mid: number;
  /** price → notional */
  levels: Map<number, number>;
}

const MAX_FRAMES = 300; // ~5 мин при 1 сек
const POLL_INTERVAL_MS = 1000;

/** Конвертация нотации в цвет (зелёный для bid, красный для ask) */
function notionalToColor(notional: number, maxNotional: number, side: "bid" | "ask"): string {
  if (notional <= 0) return "transparent";
  const intensity = Math.min(1, notional / Math.max(maxNotional, 1));
  const alpha = 0.1 + intensity * 0.9;
  if (side === "bid") return `rgba(34, 197, 94, ${alpha})`;
  return `rgba(239, 68, 68, ${alpha})`;
}

export function DensityHeatmap({ symbol, market = "spot" }: { symbol: string; market?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const framesRef = useRef<HeatmapFrame[]>([]);
  const [running, setRunning] = useState(false);
  const [frameCount, setFrameCount] = useState(0);
  const [mid, setMid] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchSnapshot = useCallback(async () => {
    if (!symbol) return;
    try {
      const q = new URLSearchParams({ symbol, market, levels: "80" });
      const r = await fetch(apiUrl(`/api/density/heatmap?${q}`));
      const d: HeatmapSnapshot = await r.json();
      if (!d.ok) return;

      setMid(d.mid);

      // Конвертировать в frame
      const levels = new Map<number, number>();
      for (const b of d.bids) levels.set(b.price, b.notional);
      for (const a of d.asks) levels.set(a.price, a.notional);

      const frame: HeatmapFrame = {
        timestamp_ms: d.timestamp_ms,
        mid: d.mid,
        levels,
      };

      framesRef.current.push(frame);
      if (framesRef.current.length > MAX_FRAMES) {
        framesRef.current = framesRef.current.slice(-MAX_FRAMES);
      }
      setFrameCount(framesRef.current.length);

      renderHeatmap();
    } catch {
      /* ignore */
    }
  }, [symbol, market]);

  const renderHeatmap = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const frames = framesRef.current;
    if (frames.length === 0) return;

    const W = canvas.width;
    const H = canvas.height;

    // Очистить
    ctx.fillStyle = "#0f0f0f";
    ctx.fillRect(0, 0, W, H);

    // Найти диапазон цен и максимальную нотацию
    let minPrice = Infinity;
    let maxPrice = -Infinity;
    let maxNotional = 0;

    for (const frame of frames) {
      for (const [price, notional] of frame.levels) {
        if (price < minPrice) minPrice = price;
        if (price > maxPrice) maxPrice = price;
        if (notional > maxNotional) maxNotional = notional;
      }
    }

    if (minPrice >= maxPrice || maxNotional <= 0) return;

    // Добавить отступ 5%
    const priceRange = maxPrice - minPrice;
    minPrice -= priceRange * 0.05;
    maxPrice += priceRange * 0.05;

    const frameW = Math.max(1, W / frames.length);
    const priceToY = (price: number) => H - ((price - minPrice) / (maxPrice - minPrice)) * H;

    // Рисовать каждый фрейм
    for (let i = 0; i < frames.length; i++) {
      const frame = frames[i];
      const x = i * frameW;

      // Рисовать mid line
      const midY = priceToY(frame.mid);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(x, midY);
      ctx.lineTo(x + frameW, midY);
      ctx.stroke();

      // Рисовать уровни
      for (const [price, notional] of frame.levels) {
        const y = priceToY(price);
        const side = price < frame.mid ? "bid" : "ask";
        ctx.fillStyle = notionalToColor(notional, maxNotional, side);

        // Высота пикселя пропорциональна нотации
        const barH = Math.max(1, (notional / maxNotional) * 8);
        ctx.fillRect(x, y - barH / 2, Math.max(1, frameW - 0.5), barH);
      }
    }

    // Подписи цен справа
    ctx.fillStyle = "rgba(255, 255, 255, 0.5)";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    const steps = 8;
    for (let i = 0; i <= steps; i++) {
      const price = minPrice + (maxPrice - minPrice) * (i / steps);
      const y = priceToY(price);
      ctx.fillText(price.toFixed(2), W - 4, y + 3);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W - 40, y);
      ctx.stroke();
    }

    // Mid label
    if (frames.length > 0) {
      const lastFrame = frames[frames.length - 1];
      const midY = priceToY(lastFrame.mid);
      ctx.fillStyle = "rgba(255, 255, 255, 0.8)";
      ctx.font = "bold 10px monospace";
      ctx.textAlign = "right";
      ctx.fillText(`MID ${lastFrame.mid.toFixed(2)}`, W - 4, midY - 5);
    }
  }, []);

  const startStop = useCallback(() => {
    if (running) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      setRunning(false);
    } else {
      framesRef.current = [];
      setFrameCount(0);
      fetchSnapshot();
      intervalRef.current = setInterval(fetchSnapshot, POLL_INTERVAL_MS);
      setRunning(true);
    }
  }, [running, fetchSnapshot]);

  const clear = useCallback(() => {
    framesRef.current = [];
    setFrameCount(0);
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.fillStyle = "#0f0f0f";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      }
    }
  }, []);

  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  // Resize canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * window.devicePixelRatio;
      canvas.height = rect.height * window.devicePixelRatio;
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
      renderHeatmap();
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [renderHeatmap]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-violet-500" />
        <span className="text-sm font-medium text-ink">
          Density Heatmap — {symbol}
        </span>
        {mid > 0 && (
          <span className="font-mono text-xs text-ink-muted">
            MID: {mid.toFixed(2)}
          </span>
        )}
        <span className="text-xs text-ink-muted">
          {frameCount} снимков
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={startStop}
            className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${
              running
                ? "bg-red-500 text-white hover:bg-red-600"
                : "bg-emerald-500 text-white hover:bg-emerald-600"
            }`}
          >
            {running ? <Square className="h-3 w-3" /> : <Play className="h-3 w-3" />}
            {running ? "Стоп" : "Старт"}
          </button>
          <button
            onClick={clear}
            className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-surface"
          >
            Очистить
          </button>
        </div>
      </div>
      <div className="relative h-[400px] w-full overflow-hidden rounded-xl border border-line bg-black">
        <canvas
          ref={canvasRef}
          className="h-full w-full"
          style={{ imageRendering: "pixelated" }}
        />
        {!running && frameCount === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-ink-muted">Нажмите «Старт» для записи тепловой карты</p>
          </div>
        )}
      </div>
      <div className="flex items-center gap-4 text-[10px] text-ink-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" /> Bid (покупка)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-red-500" /> Ask (продажа)
        </span>
        <span>Яркость = нотация (USDT). X = время, Y = цена.</span>
      </div>
    </div>
  );
}
