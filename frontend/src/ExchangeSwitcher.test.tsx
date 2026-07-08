/**
 * Unit tests for ExchangeSwitcher component.
 *
 * Feature: multi-exchange-integration
 * Tasks: 8.1
 * Validates: Requirements 12.1, 12.2, 12.5
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExchangeSwitcher, EXCHANGE_GROUPS } from "./ExchangeSwitcher";

describe("ExchangeSwitcher", () => {
  it("renders all 11 exchange options", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} />);

    expect(screen.getByText("MEXC")).toBeInTheDocument();
    expect(screen.getByText("Binance")).toBeInTheDocument();
    expect(screen.getByText("Bybit")).toBeInTheDocument();
    expect(screen.getByText("OKX")).toBeInTheDocument();
    expect(screen.getByText("Gate.io")).toBeInTheDocument();
    expect(screen.getByText("HTX")).toBeInTheDocument();
    expect(screen.getByText("Bitget")).toBeInTheDocument();
    expect(screen.getByText("AsterDEX")).toBeInTheDocument();
    expect(screen.getByText("Lighter")).toBeInTheDocument();
    expect(screen.getByText("dYdX")).toBeInTheDocument();
    expect(screen.getByText("Hyperliquid")).toBeInTheDocument();
  });

  it("displays CEX and DEX group labels", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} />);

    expect(screen.getByText("CEX")).toBeInTheDocument();
    expect(screen.getByText("DEX")).toBeInTheDocument();
  });

  it("visually highlights the active exchange", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ExchangeSwitcher active="mexc" onChange={onChange} />
    );

    // MEXC button should have the active class (bg-accent)
    const mexcBtn = screen.getByText("MEXC");
    expect(mexcBtn.className).toContain("bg-accent");

    // AsterDEX should NOT have the active class
    const asterBtn = screen.getByText("AsterDEX");
    expect(asterBtn.className).not.toContain("bg-accent");

    // Switch active to asterdex
    rerender(<ExchangeSwitcher active="asterdex" onChange={onChange} />);
    expect(screen.getByText("AsterDEX").className).toContain("bg-accent");
    expect(screen.getByText("MEXC").className).not.toContain("bg-accent");
  });

  it("calls onChange with correct exchange value when clicking a tab", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} />);

    fireEvent.click(screen.getByText("AsterDEX"));
    expect(onChange).toHaveBeenCalledWith("asterdex");

    fireEvent.click(screen.getByText("Lighter"));
    expect(onChange).toHaveBeenCalledWith("lighter");

    fireEvent.click(screen.getByText("MEXC"));
    expect(onChange).toHaveBeenCalledWith("mexc");
  });

  it("calls onChange for new exchanges", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} />);

    fireEvent.click(screen.getByText("Binance"));
    expect(onChange).toHaveBeenCalledWith("binance");

    fireEvent.click(screen.getByText("dYdX"));
    expect(onChange).toHaveBeenCalledWith("dydx");

    fireEvent.click(screen.getByText("Hyperliquid"));
    expect(onChange).toHaveBeenCalledWith("hyperliquid");
  });

  it("disables all buttons when disabled prop is true", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} disabled />);

    const buttons = screen.getAllByRole("button");
    buttons.forEach((btn) => {
      expect(btn).toBeDisabled();
    });
  });

  it("does not call onChange when disabled and clicked", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} disabled />);

    fireEvent.click(screen.getByText("AsterDEX"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("applies opacity class when disabled", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} disabled />);

    const buttons = screen.getAllByRole("button");
    buttons.forEach((btn) => {
      expect(btn.className).toContain("opacity-50");
    });
  });

  it("renders all exchanges as buttons (11 total)", () => {
    const onChange = vi.fn();
    render(<ExchangeSwitcher active="mexc" onChange={onChange} />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(11);
  });

  it("EXCHANGE_GROUPS has correct CEX exchanges", () => {
    const cexGroup = EXCHANGE_GROUPS.find((g) => g.label === "CEX");
    expect(cexGroup).toBeDefined();
    const cexValues = cexGroup!.exchanges.map((e) => e.value);
    expect(cexValues).toEqual([
      "mexc",
      "binance",
      "bybit",
      "okx",
      "gateio",
      "htx",
      "bitget",
    ]);
  });

  it("EXCHANGE_GROUPS has correct DEX exchanges", () => {
    const dexGroup = EXCHANGE_GROUPS.find((g) => g.label === "DEX");
    expect(dexGroup).toBeDefined();
    const dexValues = dexGroup!.exchanges.map((e) => e.value);
    expect(dexValues).toEqual(["asterdex", "lighter", "dydx", "hyperliquid"]);
  });
});
