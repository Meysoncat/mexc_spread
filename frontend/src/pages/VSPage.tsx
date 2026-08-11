import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Columns3, Plus, RefreshCw, Trash2, Grid3X3, LineChart, CandlestickChart } from "lucide-react";
import { apiUrl } from "../config";
import type { Exchange, ChartInterval } from "../types";
import { EXCHANGE_LABELS } from "../types";
import { ChartCore } from "../components/charts/ChartCore";
import { chartColors } from "../components/charts/chartTheme";
import { CandlestickSeries, LineSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";

interface CellConfig {
  symbol: string;
  exchange: Exchange;
  market: "spot" | "futures";
}

interface CellData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

const INTERVALS: { value: ChartInterval; label: string }[] = [
  { value: "5m", label: "5м" },
  { value: "15m", label: "15м" },
  { value: "1h", label: "1ч" },
  { value: "4h", label: "4ч" },
  { value: "1d", label: "1д" },
];

const EXCHANGES: Exchange[] = ["mexc", "binance", "bybit", "okx", "gateio", "bitget"];

function cellKey(c: CellConfig): string {
  return `${c.exchange}:${c.market}:${c.symbol}`;
}

/** Один мини-график в ячейке сетки */
function MiniChart({
  config,
  interval,
  visual,
  onRemove,
  onSelectSymbol,
}: {
  config: CellConfig;
  interval: ChartInterval;
  visual: "candles" | "line";
  onRemove: () => void;
  onSelectSymbol: () => void;
}) {
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [priceChange, setPriceChange] = useState<number | null>(null);

  const handleChartReady = useCallback(
    (chart: IChartApi) => {
      let series: ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;
      if (visual === "candles") {
        series = chart.addSeries(CandlestickSeries, {
          upColor: chartColors.up,
          downColor: chartColors.down,
          borderUpColor: chartColors.up,
          borderDownColor: chartColors.down,
          wickUpColor: chartColors.up,
          wickDownColor: chartColors.down,
        });
      } else {
        series = chart.addSeries(LineSeries, {
          color: chartColors.accent,
          lineWidth: 2,
        });
      }
      seriesRef.current = series;

      // Load data
      setLoading(true);
      const q = new URLSearchParams({
        market: config.market,
        symbols: config.symbol,
        interval,
        limit: "96",
        exchange: config.exchange,
      });
      fetch(apiUrl(`/api/klines?${q}`))
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok || !data.candles) return;
          const candles = data.candles;
          if (candles.length > 0) {
            if (visual === "candles") {
              (series as ISeriesApi<"Candlestick">).setData(
                candles.map((c: CellData) => ({
                  time: c.time as UTCTimestamp,
                  open: c.open,
                  high: c.high,
                  low: c.low,
                  close: c.close,
                })),
              );
            } else {
              (series as ISeriesApi<"Line">).setData(
                candles.map((c: CellData) => ({
                  time: c.time as UTCTimestamp,
                  value: c.close,
                })),
              );
            }
            const last = candles[candles.length - 1];
            setLastPrice(last.close);
            if (candles.length > 1) {
              const first = candles[0];
              setPriceChange(((last.close - first.open) / first.open) * 100);
            }
            chart.timeScale().fitContent();
          }
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    },
    [config, interval, visual],
  );

  return (
    <div className="group relative flex flex-col rounded-lg border border-line bg-surface-elevated overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1 border-b border-line bg-surface">
        <button
          onClick={onSelectSymbol}
          className="flex items-center gap-1 text-xs font-mono font-medium text-ink hover:text-accent transition"
        >
          <span className="text-[10px] text-ink-muted uppercase">{config.exchange}</span>
          <span>{config.symbol}</span>
          <span className="text-[10px] text-ink-muted">{config.market === "spot" ? "S" : "F"}</span>
        </button>
        <div className="flex items-center gap-1">
          {lastPrice != null && (
            <span className={`text-[10px] font-mono ${priceChange != null && priceChange >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
              {lastPrice >= 1000 ? lastPrice.toFixed(2) : lastPrice >= 1 ? lastPrice.toFixed(4) : lastPrice.toFixed(6)}
              {priceChange != null && (
                <span className="ml-1">{priceChange >= 0 ? "+" : ""}{priceChange.toFixed(2)}%</span>
              )}
            </span>
          )}
          <button
            onClick={onRemove}
            className="opacity-0 group-hover:opacity-100 transition p-0.5 rounded text-ink-muted hover:text-red-500"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>
      {/* Chart */}
      <div className="relative flex-1 min-h-[120px]">
        <ChartCore
          key={cellKey(config)}
          onChartReady={handleChartReady}
          className="h-full w-full"
          options={{
            timeScale: { visible: false },
            rightPriceScale: { visible: false },
            crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
            layout: { background: { color: "transparent" } },
            grid: { vertLines: { visible: false }, horzLines: { visible: false } },
          }}
        />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/50">
            <RefreshCw className="h-4 w-4 animate-spin text-ink-muted" />
          </div>
        )}
      </div>
    </div>
  );
}

/** Пикер символа для ячейки */
function SymbolPickerModal({
  open,
  onSelect,
  onClose,
}: {
  open: boolean;
  onSelect: (config: CellConfig) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [exchange, setExchange] = useState<Exchange>("binance");
  const [market, setMarket] = useState<"spot" | "futures">("futures");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    const q = new URLSearchParams({ market, exchange, limit: "200" });
    fetch(apiUrl(`/api/snapshot?${q}`))
      .then((r) => r.json())
      .then((d) => {
        if (d.ok && d.rows) {
          setSymbols(d.rows.map((r: { symbol: string }) => r.symbol).sort());
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, exchange, market]);

  if (!open) return null;

  const s = search.trim().toUpperCase();
  const filtered = symbols.filter((sym) => !s || sym.includes(s)).slice(0, 50);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-line bg-surface-elevated shadow-xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="border-b border-line px-4 py-3">
          <h3 className="text-sm font-semibold text-ink">Выберите актив</h3>
          <div className="mt-2 flex gap-2">
            <div className="flex rounded-lg border border-line">
              {EXCHANGES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setExchange(ex)}
                  className={`px-2 py-1 text-[10px] font-medium ${exchange === ex ? "bg-accent text-white" : "text-ink-muted"}`}
                >
                  {EXCHANGE_LABELS[ex] ?? ex}
                </button>
              ))}
            </div>
            <div className="flex rounded-lg border border-line">
              {(["spot", "futures"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMarket(m)}
                  className={`px-2 py-1 text-[10px] font-medium ${market === m ? "bg-accent text-white" : "text-ink-muted"}`}
                >
                  {m === "spot" ? "Спот" : "Фьюч"}
                </button>
              ))}
            </div>
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск BTC, ETH…"
            autoFocus
            className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <div className="max-h-60 overflow-y-auto">
          {loading ? (
            <div className="p-4 text-center text-xs text-ink-muted">Загрузка…</div>
          ) : filtered.length === 0 ? (
            <div className="p-4 text-center text-xs text-ink-muted">Нет символов</div>
          ) : (
            filtered.map((sym) => (
              <button
                key={sym}
                onClick={() => onSelect({ symbol: sym, exchange, market })}
                className="w-full px-4 py-2 text-left text-sm font-mono text-ink hover:bg-accent/10 transition"
              >
                {sym}
                <span className="ml-2 text-[10px] text-ink-muted">{exchange} · {market}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

const DEFAULT_COLS = 4;
const DEFAULT_ROWS = 3;

export function VSPage() {
  const [cols, setCols] = useState(DEFAULT_COLS);
  const [rows, setRows] = useState(DEFAULT_ROWS);
  const [interval, setInterval] = useState<ChartInterval>("1h");
  const [visual, setVisual] = useState<"candles" | "line">("line");
  const [cells, setCells] = useState<CellConfig[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerIndex, setPickerIndex] = useState<number | null>(null);

  // Initialize with some defaults
  useEffect(() => {
    if (cells.length === 0) {
      const defaults: CellConfig[] = [
        { symbol: "BTCUSDT", exchange: "binance", market: "futures" },
        { symbol: "ETHUSDT", exchange: "binance", market: "futures" },
        { symbol: "SOLUSDT", exchange: "binance", market: "futures" },
        { symbol: "BTCUSDT", exchange: "binance", market: "spot" },
        { symbol: "ETHUSDT", exchange: "okx", market: "futures" },
        { symbol: "DOGEUSDT", exchange: "binance", market: "futures" },
      ];
      setCells(defaults);
    }
  }, []);

  const totalCells = cols * rows;

  const addCell = useCallback((config: CellConfig) => {
    setCells((prev) => {
      if (pickerIndex !== null && pickerIndex < prev.length) {
        const next = [...prev];
        next[pickerIndex] = config;
        return next;
      }
      return [...prev, config];
    });
    setPickerOpen(false);
    setPickerIndex(null);
  }, [pickerIndex]);

  const removeCell = useCallback((index: number) => {
    setCells((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const openPicker = useCallback((index: number) => {
    setPickerIndex(index);
    setPickerOpen(true);
  }, []);

  // Fill cells to grid size with empty placeholders
  const gridCells = useMemo(() => {
    const result: (CellConfig | null)[] = [...cells];
    while (result.length < totalCells) {
      result.push(null);
    }
    return result.slice(0, totalCells);
  }, [cells, totalCells]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3 md:p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Columns3 className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-semibold text-ink">VS — Сравнение активов</h1>
          <span className="text-xs text-ink-muted">
            {cells.length} активов · {cols}×{rows} сетка
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-line">
            {INTERVALS.map((iv) => (
              <button
                key={iv.value}
                onClick={() => setInterval(iv.value)}
                className={`px-2 py-1 text-xs font-medium transition ${interval === iv.value ? "bg-accent text-white" : "text-ink-muted hover:bg-surface"}`}
              >
                {iv.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setVisual(visual === "candles" ? "line" : "candles")}
            className="rounded-md p-1.5 text-ink-muted hover:bg-surface"
            title={visual === "candles" ? "Линия" : "Свечи"}
          >
            {visual === "candles" ? <LineChart className="h-4 w-4" /> : <CandlestickChart className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {/* Grid controls */}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          <Grid3X3 className="h-3.5 w-3.5" />
          Колонки
          <input
            type="range"
            min={1}
            max={10}
            value={cols}
            onChange={(e) => setCols(Number(e.target.value))}
            className="w-20"
          />
          <span className="font-mono w-4">{cols}</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-muted">
          Строки
          <input
            type="range"
            min={1}
            max={10}
            value={rows}
            onChange={(e) => setRows(Number(e.target.value))}
            className="w-20"
          />
          <span className="font-mono w-4">{rows}</span>
        </label>
        <button
          onClick={() => setCells([])}
          className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-surface"
        >
          Очистить все
        </button>
      </div>

      {/* Chart Grid */}
      <div
        className="min-h-0 flex-1 gap-1.5 overflow-auto"
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${rows}, minmax(140px, 1fr))`,
        }}
      >
        {gridCells.map((config, i) =>
          config ? (
            <MiniChart
              key={cellKey(config)}
              config={config}
              interval={interval}
              visual={visual}
              onRemove={() => removeCell(i)}
              onSelectSymbol={() => openPicker(i)}
            />
          ) : (
            <button
              key={`empty-${i}`}
              onClick={() => openPicker(i)}
              className="flex items-center justify-center rounded-lg border border-dashed border-line bg-surface/50 text-ink-muted hover:border-accent hover:text-accent transition min-h-[140px]"
            >
              <Plus className="h-6 w-6" />
            </button>
          ),
        )}
      </div>

      <SymbolPickerModal
        open={pickerOpen}
        onSelect={addCell}
        onClose={() => { setPickerOpen(false); setPickerIndex(null); }}
      />
    </div>
  );
}
