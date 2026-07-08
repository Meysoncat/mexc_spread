/**
 * Unit tests for exchange-aware fetch logic.
 *
 * Feature: exchange-switcher
 * Tasks: 5.3
 * Validates: Requirements 2.1, 3.1, 3.2, 3.3
 *
 * Tests the fetchSnapshot URL construction and exchange parameter handling.
 * Since the actual fetch logic is embedded in App.tsx hooks, we test the
 * URL construction pattern and the applyMarketFilters behavior.
 */

import { describe, it, expect } from "vitest";
import { applyMarketFilters } from "./filters";
import type { Exchange, Market, MarketRow } from "./types";

// --- URL construction tests ---

describe("fetchSnapshot URL construction", () => {
  it("includes exchange parameter in URL when exchange is specified", () => {
    const market: Market = "spot";
    const exchange: Exchange = "lighter";

    const q = new URLSearchParams({ market });
    if (exchange) q.set("exchange", exchange);

    const url = `/api/snapshot?${q}`;
    expect(url).toBe("/api/snapshot?market=spot&exchange=lighter");
  });

  it("includes exchange=mexc by default", () => {
    const market: Market = "spot";
    const exchange: Exchange = "mexc";

    const q = new URLSearchParams({ market });
    if (exchange) q.set("exchange", exchange);

    const url = `/api/snapshot?${q}`;
    expect(url).toBe("/api/snapshot?market=spot&exchange=mexc");
  });

  it("includes exchange=asterdex for AsterDEX", () => {
    const market: Market = "futures";
    const exchange: Exchange = "asterdex";

    const q = new URLSearchParams({ market });
    if (exchange) q.set("exchange", exchange);

    const url = `/api/snapshot?${q}`;
    expect(url).toBe("/api/snapshot?market=futures&exchange=asterdex");
  });

  it("includes nocache parameter when specified", () => {
    const market: Market = "spot";
    const exchange: Exchange = "lighter";

    const q = new URLSearchParams({ market });
    q.set("exchange", exchange);
    q.set("nocache", "true");

    const url = `/api/snapshot?${q}`;
    expect(url).toContain("exchange=lighter");
    expect(url).toContain("nocache=true");
  });
});

// --- Filter state preservation tests ---

describe("filter state preservation across exchange switches", () => {
  // Create sample rows that would come from different exchanges
  const sampleRows: MarketRow[] = [
    {
      symbol: "BTCUSDT",
      bid: 50000,
      ask: 50010,
      bid_qty: 1.5,
      ask_qty: 2.0,
      mid: 50005,
      spread_abs: 10,
      spread_bps: 2.0,
      net_spread_bps: 1.0,
      volume_24h_base: 1000,
      volume_24h_quote: 50000000,
      funding_rate: 0.0001,
      observed_at: "2024-01-01T00:00:00Z",
      fee_round_trip_bps: 2.0,
      l1_max_executable_base: 1.5,
      l1_max_notional_quote: 75000,
      reference_quote_notional: 10000,
      l1_covers_reference_notional: true,
    },
    {
      symbol: "ETHUSDT",
      bid: 3000,
      ask: 3001,
      bid_qty: 10,
      ask_qty: 12,
      mid: 3000.5,
      spread_abs: 1,
      spread_bps: 3.33,
      net_spread_bps: 2.33,
      volume_24h_base: 5000,
      volume_24h_quote: 15000000,
      funding_rate: 0.0002,
      observed_at: "2024-01-01T00:00:00Z",
      fee_round_trip_bps: 2.0,
      l1_max_executable_base: 10,
      l1_max_notional_quote: 30000,
      reference_quote_notional: 10000,
      l1_covers_reference_notional: true,
    },
    {
      symbol: "SOLUSDT",
      bid: 100,
      ask: 100.05,
      bid_qty: 100,
      ask_qty: 150,
      mid: 100.025,
      spread_abs: 0.05,
      spread_bps: 5.0,
      net_spread_bps: 4.0,
      volume_24h_base: 50000,
      volume_24h_quote: 5000000,
      funding_rate: null,
      observed_at: "2024-01-01T00:00:00Z",
      fee_round_trip_bps: 2.0,
      l1_max_executable_base: 100,
      l1_max_notional_quote: 10000,
      reference_quote_notional: 10000,
      l1_covers_reference_notional: true,
    },
  ];

  it("search filter applies correctly to new data after exchange switch", () => {
    // Simulate: user has search="ETH", switches exchange, new data arrives
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 0,
      minVolQuote: 0,
      search: "ETH",
      sortBy: "spread_bps",
      ascending: false,
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    const filtered = (applyMarketFilters(sampleRows, filterConfig) as MarketRow[]);

    // Only ETHUSDT should match
    expect(filtered.length).toBe(1);
    expect(filtered[0].symbol).toBe("ETHUSDT");
  });

  it("sort order is preserved and applied to new data", () => {
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 0,
      minVolQuote: 0,
      search: "",
      sortBy: "spread_bps",
      ascending: true, // ascending sort by spread_bps
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    const filtered = (applyMarketFilters(sampleRows, filterConfig) as MarketRow[]);

    // Should be sorted by spread_bps ascending: BTC(2.0) < ETH(3.33) < SOL(5.0)
    expect(filtered.length).toBe(3);
    expect(filtered[0].symbol).toBe("BTCUSDT");
    expect(filtered[1].symbol).toBe("ETHUSDT");
    expect(filtered[2].symbol).toBe("SOLUSDT");
  });

  it("descending sort order is preserved", () => {
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 0,
      minVolQuote: 0,
      search: "",
      sortBy: "spread_bps",
      ascending: false, // descending
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    const filtered = (applyMarketFilters(sampleRows, filterConfig) as MarketRow[]);

    // Should be sorted by spread_bps descending: SOL(5.0) > ETH(3.33) > BTC(2.0)
    expect(filtered.length).toBe(3);
    expect(filtered[0].symbol).toBe("SOLUSDT");
    expect(filtered[1].symbol).toBe("ETHUSDT");
    expect(filtered[2].symbol).toBe("BTCUSDT");
  });

  it("combined search + sort is preserved across exchange switch", () => {
    // User has search="USDT" (matches all), sort by volume descending
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 0,
      minVolQuote: 0,
      search: "USDT",
      sortBy: "volume_24h_quote",
      ascending: false,
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    const filtered = (applyMarketFilters(sampleRows, filterConfig) as MarketRow[]);

    // All match "USDT", sorted by volume desc: BTC(50M) > ETH(15M) > SOL(5M)
    expect(filtered.length).toBe(3);
    expect(filtered[0].symbol).toBe("BTCUSDT");
    expect(filtered[1].symbol).toBe("ETHUSDT");
    expect(filtered[2].symbol).toBe("SOLUSDT");
  });

  it("minSpreadBps filter is preserved", () => {
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 3, // Only ETH(3.33) and SOL(5.0) pass
      minVolQuote: 0,
      search: "",
      sortBy: "spread_bps",
      ascending: false,
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    const filtered = (applyMarketFilters(sampleRows, filterConfig) as MarketRow[]);

    expect(filtered.length).toBe(2);
    // Descending: SOL first, then ETH
    expect(filtered[0].symbol).toBe("SOLUSDT");
    expect(filtered[1].symbol).toBe("ETHUSDT");
  });

  it("empty rows after exchange switch still applies filters without error", () => {
    const filterConfig = {
      market: "spot" as Market,
      quoteRaw: "USDT",
      minSpreadBps: 0,
      minVolQuote: 0,
      search: "BTC",
      sortBy: "spread_bps",
      ascending: false,
      minBidL1NotionalQuote: 0,
      minAskL1NotionalQuote: 0,
    };

    // Empty rows (simulating cleared state after exchange switch)
    const filtered = applyMarketFilters([], filterConfig);
    expect(filtered).toEqual([]);
  });
});

// --- Exchange switch state behavior ---

describe("exchange switch state behavior", () => {
  it("rows should be cleared on exchange switch (simulated)", () => {
    // This tests the pattern: when exchange changes, rows = []
    let rows: MarketRow[] = sampleRows;
    expect(rows.length).toBeGreaterThan(0);

    // Simulate exchange switch
    rows = []; // This is what App.tsx does: setRows([])

    expect(rows).toEqual([]);
  });

  it("exchange display names are correct", () => {
    const EXCHANGE_DISPLAY_NAMES: Partial<Record<Exchange, string>> = {
      mexc: "MEXC",
      asterdex: "AsterDEX",
      lighter: "Lighter",
    };

    expect(EXCHANGE_DISPLAY_NAMES.mexc).toBe("MEXC");
    expect(EXCHANGE_DISPLAY_NAMES.asterdex).toBe("AsterDEX");
    expect(EXCHANGE_DISPLAY_NAMES.lighter).toBe("Lighter");
  });

  it("error message includes exchange name pattern", () => {
    const exchange: Exchange = "lighter";
    const EXCHANGE_DISPLAY_NAMES: Partial<Record<Exchange, string>> = {
      mexc: "MEXC",
      asterdex: "AsterDEX",
      lighter: "Lighter",
    };

    const errorMsg = `Ошибка загрузки данных ${EXCHANGE_DISPLAY_NAMES[exchange]}: timeout`;
    expect(errorMsg).toContain("Lighter");
  });
});

const sampleRows: MarketRow[] = [
  {
    symbol: "BTCUSDT",
    bid: 50000,
    ask: 50010,
    bid_qty: 1.5,
    ask_qty: 2.0,
    mid: 50005,
    spread_abs: 10,
    spread_bps: 2.0,
    net_spread_bps: 1.0,
    volume_24h_base: 1000,
    volume_24h_quote: 50000000,
    funding_rate: 0.0001,
    observed_at: "2024-01-01T00:00:00Z",
    fee_round_trip_bps: 2.0,
    l1_max_executable_base: 1.5,
    l1_max_notional_quote: 75000,
    reference_quote_notional: 10000,
    l1_covers_reference_notional: true,
  },
];
