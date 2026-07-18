import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ALL_EXCHANGES, type Exchange, type Market } from "../types";

// ─── Interfaces ────────────────────────────────────────────────────────────────

export interface FilterState {
  quote: string;
  minSpread: number;
  minVolume: number;
  search: string;
}

export interface NavigationState {
  exchange: Exchange;
  market: Market;
  symbol: string;
  filters: FilterState;
}

export interface NavigationStateContextValue {
  state: NavigationState;
  setExchange: (exchange: Exchange) => void;
  setMarket: (market: Market) => void;
  setSymbol: (symbol: string) => void;
  setFilters: (filters: Partial<FilterState>) => void;
  resetFilters: () => void;
}

// ─── Constants ─────────────────────────────────────────────────────────────────

const LS_KEY_EXCHANGE = "mexc-nav-exchange";
const LS_KEY_MARKET = "mexc-nav-market";
const LS_KEY_SYMBOL = "mexc-nav-symbol";
const LS_KEY_FILTERS = "mexc-nav-filters";

const DEFAULT_FILTERS: FilterState = {
  quote: "Все",
  minSpread: 0,
  minVolume: 0,
  search: "",
};

const DEFAULT_SYMBOL = "BTCUSDT";

const DEFAULT_STATE: NavigationState = {
  exchange: "mexc",
  market: "spot",
  symbol: DEFAULT_SYMBOL,
  filters: { ...DEFAULT_FILTERS },
};

/** All valid exchange values for validation (generated from canonical registry). */
const VALID_EXCHANGES: ReadonlySet<string> = new Set<string>(ALL_EXCHANGES);

/** All valid market values for validation. */
const VALID_MARKETS: ReadonlySet<string> = new Set<Market>([
  "spot",
  "futures",
  "cross",
]);

// ─── Helpers ───────────────────────────────────────────────────────────────────

function isValidExchange(value: unknown): value is Exchange {
  return typeof value === "string" && VALID_EXCHANGES.has(value);
}

function isValidMarket(value: unknown): value is Market {
  return typeof value === "string" && VALID_MARKETS.has(value);
}

/** Символ может быть любым непустым — не из фиксированного множества
 *  (на случай пустого снимка ввод символа вручную должен работать). */
function normalizeSymbol(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const s = value.trim().toUpperCase();
  return s.length > 0 && s.length <= 40 ? s : null;
}

function safeGetItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetItem(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // localStorage unavailable (e.g. private mode) — silently ignore
  }
}

function readFiltersFromStorage(): FilterState {
  const raw = safeGetItem(LS_KEY_FILTERS);
  if (!raw) return { ...DEFAULT_FILTERS };
  try {
    const parsed = JSON.parse(raw) as Partial<FilterState>;
    return {
      quote: typeof parsed.quote === "string" ? parsed.quote : DEFAULT_FILTERS.quote,
      minSpread:
        typeof parsed.minSpread === "number" && Number.isFinite(parsed.minSpread)
          ? parsed.minSpread
          : DEFAULT_FILTERS.minSpread,
      minVolume:
        typeof parsed.minVolume === "number" && Number.isFinite(parsed.minVolume)
          ? parsed.minVolume
          : DEFAULT_FILTERS.minVolume,
      search: typeof parsed.search === "string" ? parsed.search : DEFAULT_FILTERS.search,
    };
  } catch {
    // Invalid JSON — clear and use defaults
    return { ...DEFAULT_FILTERS };
  }
}

/**
 * Determine initial state using priority:
 * 1. URL query params (?exchange=...&market=...)
 * 2. localStorage
 * 3. Defaults
 */
function resolveInitialState(): NavigationState {
  // 1. URL query params
  const params = new URLSearchParams(window.location.search);
  const urlExchange = params.get("exchange");
  const urlMarket = params.get("market");
  const urlSymbol = params.get("symbol");

  // 2. localStorage
  const lsExchange = safeGetItem(LS_KEY_EXCHANGE);
  const lsMarket = safeGetItem(LS_KEY_MARKET);
  const lsSymbol = safeGetItem(LS_KEY_SYMBOL);

  // Resolve exchange: URL > localStorage > default
  let exchange: Exchange = DEFAULT_STATE.exchange;
  if (isValidExchange(urlExchange)) {
    exchange = urlExchange;
  } else if (isValidExchange(lsExchange)) {
    exchange = lsExchange;
  }

  // Resolve market: URL > localStorage > default
  let market: Market = DEFAULT_STATE.market;
  if (isValidMarket(urlMarket)) {
    market = urlMarket;
  } else if (isValidMarket(lsMarket)) {
    market = lsMarket;
  }

  // Resolve symbol: URL > localStorage > default
  let symbol: string = DEFAULT_STATE.symbol;
  const resolvedSymbol = normalizeSymbol(urlSymbol) ?? normalizeSymbol(lsSymbol);
  if (resolvedSymbol) symbol = resolvedSymbol;

  // Filters: only from localStorage (not in URL)
  const filters = readFiltersFromStorage();

  return { exchange, market, symbol, filters };
}

// ─── Context ───────────────────────────────────────────────────────────────────

export const NavigationStateContext = createContext<NavigationStateContextValue | null>(null);

// ─── Provider ──────────────────────────────────────────────────────────────────

export function NavigationStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<NavigationState>(resolveInitialState);

  // Sync exchange to localStorage on change
  useEffect(() => {
    safeSetItem(LS_KEY_EXCHANGE, state.exchange);
  }, [state.exchange]);

  // Sync market to localStorage on change
  useEffect(() => {
    safeSetItem(LS_KEY_MARKET, state.market);
  }, [state.market]);

  // Sync symbol to localStorage on change
  useEffect(() => {
    safeSetItem(LS_KEY_SYMBOL, state.symbol);
  }, [state.symbol]);

  // Sync filters to localStorage on change
  useEffect(() => {
    safeSetItem(LS_KEY_FILTERS, JSON.stringify(state.filters));
  }, [state.filters]);

  const setExchange = useCallback((exchange: Exchange) => {
    setState((prev) => (prev.exchange === exchange ? prev : { ...prev, exchange }));
  }, []);

  const setMarket = useCallback((market: Market) => {
    setState((prev) => (prev.market === market ? prev : { ...prev, market }));
  }, []);

  const setSymbol = useCallback((symbol: string) => {
    const normalized = normalizeSymbol(symbol) ?? DEFAULT_SYMBOL;
    setState((prev) => (prev.symbol === normalized ? prev : { ...prev, symbol: normalized }));
  }, []);

  const setFilters = useCallback((partial: Partial<FilterState>) => {
    setState((prev) => ({
      ...prev,
      filters: { ...prev.filters, ...partial },
    }));
  }, []);

  const resetFilters = useCallback(() => {
    setState((prev) => ({
      ...prev,
      filters: { ...DEFAULT_FILTERS },
    }));
  }, []);

  const value = useMemo<NavigationStateContextValue>(
    () => ({ state, setExchange, setMarket, setSymbol, setFilters, resetFilters }),
    [state, setExchange, setMarket, setSymbol, setFilters, resetFilters],
  );

  return (
    <NavigationStateContext.Provider value={value}>
      {children}
    </NavigationStateContext.Provider>
  );
}
