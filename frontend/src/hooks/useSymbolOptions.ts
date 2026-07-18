import { useEffect, useRef, useState } from "react";
import { apiUrl } from "../config";
import type { Exchange, Market } from "../types";

/**
 * Загружает список доступных символов из снимка биржи /api/snapshot.
 *
 * Источник — публичный снимок выбранный exchange+market. Список нужен только
 * для автодополнения в SymbolPicker: ввод произвольного символа остаётся
 * разрешённым (на случай пустого снимка, например MEXC 403).
 *
 * Кэшируется в модульном Map по ключу "exchange:market" с TTL, чтобы несколько
 * компонентов на одной странице не дёргали бэкенд повторно.
 */

interface CacheEntry {
  symbols: string[];
  fetchedAt: number;
}

const CACHE_TTL_MS = 60_000; // 1 минута
const cache = new Map<string, CacheEntry>();

function cacheKey(exchange: Exchange, market: Market): string {
  return `${exchange}:${market}`;
}

/** Извлекает символы из rows снимка (spot/futures → symbol, cross → symbol_spot). */
function extractSymbols(rows: unknown[]): string[] {
  const out: string[] = [];
  for (const r of rows) {
    if (typeof r !== "object" || r === null) continue;
    const sym =
      (r as { symbol?: unknown }).symbol ??
      (r as { symbol_spot?: unknown }).symbol_spot;
    if (typeof sym === "string" && sym.length > 0) out.push(sym);
  }
  return out;
}

export function useSymbolOptions(
  exchange: Exchange,
  market: Market,
): { symbols: string[]; loading: boolean } {
  const [symbols, setSymbols] = useState<string[]>(() => {
    const entry = cache.get(cacheKey(exchange, market));
    return entry && Date.now() - entry.fetchedAt < CACHE_TTL_MS
      ? entry.symbols
      : [];
  });
  const [loading, setLoading] = useState(false);
  const reqSeq = useRef(0);

  useEffect(() => {
    const key = cacheKey(exchange, market);
    const entry = cache.get(key);
    if (entry && Date.now() - entry.fetchedAt < CACHE_TTL_MS) {
      setSymbols(entry.symbols);
      setLoading(false);
      return;
    }

    const seq = ++reqSeq.current;
    setLoading(true);
    const url = apiUrl(
      `/api/snapshot?market=${encodeURIComponent(market)}&exchange=${encodeURIComponent(exchange)}`,
    );
    let aborted = false;
    const ctrl = new AbortController();

    fetch(url, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (aborted || seq !== reqSeq.current) return;
        const rows = Array.isArray(data?.rows) ? data.rows : [];
        const next = extractSymbols(rows);
        cache.set(key, { symbols: next, fetchedAt: Date.now() });
        setSymbols(next);
      })
      .catch(() => {
        if (aborted || seq !== reqSeq.current) return;
        // Не показываем ошибку — автодополнение опционально.
        setSymbols([]);
      })
      .finally(() => {
        if (!aborted && seq === reqSeq.current) setLoading(false);
      });

    return () => {
      aborted = true;
      ctrl.abort();
    };
  }, [exchange, market]);

  return { symbols, loading };
}
