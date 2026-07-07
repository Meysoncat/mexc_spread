import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { NavigationStateProvider } from "./NavigationStateContext";
import { useNavigationState } from "../hooks/useNavigationState";

function wrapper({ children }: { children: ReactNode }) {
  return <NavigationStateProvider>{children}</NavigationStateProvider>;
}

describe("NavigationStateContext", () => {
  beforeEach(() => {
    localStorage.clear();
    // Reset URL search params
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("provides default state when no URL params or localStorage", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.exchange).toBe("mexc");
    expect(result.current.state.market).toBe("spot");
    expect(result.current.state.filters).toEqual({
      quote: "Все",
      minSpread: 0,
      minVolume: 0,
      search: "",
    });
  });

  it("reads exchange from localStorage when no URL params", () => {
    localStorage.setItem("mexc-nav-exchange", "binance");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.exchange).toBe("binance");
  });

  it("reads market from localStorage when no URL params", () => {
    localStorage.setItem("mexc-nav-market", "futures");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.market).toBe("futures");
  });

  it("URL params take priority over localStorage", () => {
    localStorage.setItem("mexc-nav-exchange", "binance");
    localStorage.setItem("mexc-nav-market", "futures");
    window.history.replaceState({}, "", "/?exchange=okx&market=cross");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.exchange).toBe("okx");
    expect(result.current.state.market).toBe("cross");
  });

  it("ignores invalid exchange in URL, falls back to localStorage", () => {
    localStorage.setItem("mexc-nav-exchange", "bybit");
    window.history.replaceState({}, "", "/?exchange=invalid_exchange");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.exchange).toBe("bybit");
  });

  it("ignores invalid market in URL, falls back to localStorage", () => {
    localStorage.setItem("mexc-nav-market", "cross");
    window.history.replaceState({}, "", "/?market=invalid_market");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.market).toBe("cross");
  });

  it("ignores invalid exchange in localStorage, uses default", () => {
    localStorage.setItem("mexc-nav-exchange", "not_a_real_exchange");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.exchange).toBe("mexc");
  });

  it("setExchange updates state and syncs to localStorage", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    act(() => {
      result.current.setExchange("gateio");
    });

    expect(result.current.state.exchange).toBe("gateio");
    expect(localStorage.getItem("mexc-nav-exchange")).toBe("gateio");
  });

  it("setMarket updates state and syncs to localStorage", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    act(() => {
      result.current.setMarket("futures");
    });

    expect(result.current.state.market).toBe("futures");
    expect(localStorage.getItem("mexc-nav-market")).toBe("futures");
  });

  it("setFilters merges partial filter updates", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    act(() => {
      result.current.setFilters({ minSpread: 10, search: "BTC" });
    });

    expect(result.current.state.filters).toEqual({
      quote: "Все",
      minSpread: 10,
      minVolume: 0,
      search: "BTC",
    });
  });

  it("setFilters persists to localStorage", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    act(() => {
      result.current.setFilters({ quote: "USDT", minVolume: 1000 });
    });

    const stored = JSON.parse(localStorage.getItem("mexc-nav-filters")!);
    expect(stored.quote).toBe("USDT");
    expect(stored.minVolume).toBe(1000);
  });

  it("resetFilters restores default filter values", () => {
    const { result } = renderHook(() => useNavigationState(), { wrapper });

    act(() => {
      result.current.setFilters({ quote: "BTC", minSpread: 50, search: "ETH" });
    });

    act(() => {
      result.current.resetFilters();
    });

    expect(result.current.state.filters).toEqual({
      quote: "Все",
      minSpread: 0,
      minVolume: 0,
      search: "",
    });
  });

  it("reads filters from localStorage on init", () => {
    localStorage.setItem(
      "mexc-nav-filters",
      JSON.stringify({ quote: "USDT", minSpread: 5, minVolume: 100, search: "SOL" }),
    );

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.filters).toEqual({
      quote: "USDT",
      minSpread: 5,
      minVolume: 100,
      search: "SOL",
    });
  });

  it("handles invalid JSON in localStorage filters gracefully", () => {
    localStorage.setItem("mexc-nav-filters", "not valid json{{{");

    const { result } = renderHook(() => useNavigationState(), { wrapper });

    expect(result.current.state.filters).toEqual({
      quote: "Все",
      minSpread: 0,
      minVolume: 0,
      search: "",
    });
  });

  it("throws when useNavigationState is used outside provider", () => {
    expect(() => {
      renderHook(() => useNavigationState());
    }).toThrow("useNavigationState must be used within a NavigationStateProvider");
  });
});
