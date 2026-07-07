/**
 * Batch klines fetcher — collects individual symbol requests and fires
 * a single /api/klines/batch call. Provides instant cache hits on subsequent renders.
 */

import { apiUrl } from "./config";
import type { Exchange, KlineCandle, Market } from "./types";

interface PendingRequest {
  resolve: (candles: KlineCandle[]) => void;
  reject: (err: Error) => void;
}

const BATCH_DELAY_MS = 50; // Wait 50ms to collect requests before firing
const CACHE_TTL_MS = 60_000; // Client-side cache: 60s

// In-memory cache
const cache = new Map<string, { expires: number; candles: KlineCandle[] }>();

// Pending batch queue
let pendingQueue: Map<string, PendingRequest[]> = new Map();
let batchTimer: ReturnType<typeof setTimeout> | null = null;
let currentMarket: string = "spot";
let currentInterval: string = "1h";
let currentLimit: number = 96;
let currentExchange: string = "mexc";

function cacheKey(market: string, symbol: string, interval: string, limit: number, exchange: string): string {
  return `${exchange}:${market}:${symbol}:${interval}:${limit}`;
}

function fireBatch() {
  batchTimer = null;
  const queue = pendingQueue;
  pendingQueue = new Map();

  if (queue.size === 0) return;

  const symbols = Array.from(queue.keys());
  const params = new URLSearchParams({
    market: currentMarket,
    symbols: symbols.join(","),
    interval: currentInterval,
    limit: String(currentLimit),
    exchange: currentExchange,
  });

  fetch(apiUrl(`/api/klines/batch?${params}`))
    .then((r) => r.json())
    .then((data: { ok: boolean; results: Record<string, KlineCandle[]> }) => {
      if (!data.ok) {
        for (const [, callbacks] of queue) {
          for (const cb of callbacks) cb.resolve([]);
        }
        return;
      }
      const now = Date.now();
      for (const [sym, callbacks] of queue) {
        const candles: KlineCandle[] = data.results[sym] ?? [];
        // Cache the result
        const key = cacheKey(currentMarket, sym, currentInterval, currentLimit, currentExchange);
        cache.set(key, { expires: now + CACHE_TTL_MS, candles });
        for (const cb of callbacks) cb.resolve(candles);
      }
    })
    .catch((err) => {
      for (const [, callbacks] of queue) {
        for (const cb of callbacks) cb.reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
}

/**
 * Request klines for a symbol. Returns from cache if available,
 * otherwise batches the request with other pending requests.
 */
export function fetchKlinesBatched(
  market: Market,
  symbol: string,
  interval: string = "1h",
  limit: number = 96,
  exchange: Exchange = "mexc",
): Promise<KlineCandle[]> {
  const klinesMarket = market === "cross" ? "spot" : market;
  const key = cacheKey(klinesMarket, symbol, interval, limit, exchange);

  // Check cache
  const cached = cache.get(key);
  if (cached && cached.expires > Date.now()) {
    return Promise.resolve(cached.candles);
  }

  // Update batch params (all items in a batch share the same market/interval/limit/exchange)
  currentMarket = klinesMarket;
  currentInterval = interval;
  currentLimit = limit;
  currentExchange = exchange;

  return new Promise<KlineCandle[]>((resolve, reject) => {
    const existing = pendingQueue.get(symbol);
    if (existing) {
      existing.push({ resolve, reject });
    } else {
      pendingQueue.set(symbol, [{ resolve, reject }]);
    }

    // Schedule batch fire
    if (batchTimer === null) {
      batchTimer = setTimeout(fireBatch, BATCH_DELAY_MS);
    }
  });
}

/** Clear the client-side klines cache (e.g. on exchange switch). */
export function clearKlinesCache(): void {
  cache.clear();
}
