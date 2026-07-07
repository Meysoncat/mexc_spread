import type { Market, MarketRow, SnapshotRow } from "./types";
import { isCrossMarketRow } from "./filters";

const STORAGE_FAV = "mexc-ui-favorites-v1";
const STORAGE_SCOPE = "mexc-ui-favorites-scope";

export type FavoritesScope = "all" | "favorites_only";

type FavoritesBlob = {
  spot: string[];
  futures: string[];
  cross: string[];
};

function readBlob(): FavoritesBlob {
  if (typeof window === "undefined") {
    return { spot: [], futures: [], cross: [] };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_FAV);
    if (!raw) return { spot: [], futures: [], cross: [] };
    const j = JSON.parse(raw) as unknown;
    if (!j || typeof j !== "object") return { spot: [], futures: [], cross: [] };
    const o = j as Record<string, unknown>;
    const spot = Array.isArray(o.spot) ? o.spot.filter((x) => typeof x === "string") : [];
    const futures = Array.isArray(o.futures)
      ? o.futures.filter((x) => typeof x === "string")
      : [];
    const cross = Array.isArray(o.cross)
      ? o.cross.filter((x) => typeof x === "string")
      : [];
    return { spot, futures, cross };
  } catch {
    return { spot: [], futures: [], cross: [] };
  }
}

function writeBlob(b: FavoritesBlob) {
  try {
    window.localStorage.setItem(STORAGE_FAV, JSON.stringify(b));
  } catch {
    /* ignore */
  }
}

export function favoriteKeyForRow(market: Market, row: SnapshotRow): string {
  if (market === "cross" && isCrossMarketRow(row)) {
    return `${row.symbol_spot}::${row.symbol_futures}`;
  }
  return (row as MarketRow).symbol;
}

export function readFavoriteSet(market: Market): Set<string> {
  const b = readBlob();
  const arr = market === "spot" ? b.spot : market === "futures" ? b.futures : b.cross;
  return new Set(arr);
}

export function readFavoritesSorted(market: Market): string[] {
  return [...readFavoriteSet(market)].sort((a, b) => a.localeCompare(b));
}

export function toggleFavoriteKey(market: Market, key: string): void {
  const k = key.trim();
  if (!k) return;
  const b = readBlob();
  const field = market === "spot" ? "spot" : market === "futures" ? "futures" : "cross";
  const cur = new Set(b[field]);
  if (cur.has(k)) cur.delete(k);
  else cur.add(k);
  b[field] = [...cur].sort((a, b) => a.localeCompare(b));
  writeBlob(b);
}

export function removeFavoriteKey(market: Market, key: string): void {
  const k = key.trim();
  if (!k) return;
  const b = readBlob();
  const field = market === "spot" ? "spot" : market === "futures" ? "futures" : "cross";
  b[field] = b[field].filter((x) => x !== k);
  writeBlob(b);
}

export function clearFavoritesMarket(market: Market): void {
  const b = readBlob();
  const field = market === "spot" ? "spot" : market === "futures" ? "futures" : "cross";
  b[field] = [];
  writeBlob(b);
}

export function readFavoritesScope(): FavoritesScope {
  if (typeof window === "undefined") return "all";
  try {
    const v = window.localStorage.getItem(STORAGE_SCOPE);
    return v === "favorites_only" ? "favorites_only" : "all";
  } catch {
    return "all";
  }
}

export function writeFavoritesScope(scope: FavoritesScope) {
  try {
    window.localStorage.setItem(STORAGE_SCOPE, scope);
  } catch {
    /* ignore */
  }
}
