import type { Exchange } from "./types";

/** Exchanges that support both spot and futures markets (show market switcher). */
export const MULTI_MARKET_EXCHANGES: Exchange[] = ["mexc", "binance", "okx", "gateio", "htx"];

export interface ExchangeGroup {
  label: string;
  exchanges: { value: Exchange; label: string }[];
}

export const EXCHANGE_GROUPS: ExchangeGroup[] = [
  {
    label: "CEX",
    exchanges: [
      { value: "mexc", label: "MEXC" },
      { value: "binance", label: "Binance" },
      { value: "bybit", label: "Bybit" },
      { value: "okx", label: "OKX" },
      { value: "gateio", label: "Gate.io" },
      { value: "htx", label: "HTX" },
      { value: "bitget", label: "Bitget" },
    ],
  },
  {
    label: "DEX",
    exchanges: [
      { value: "asterdex", label: "AsterDEX" },
      { value: "lighter", label: "Lighter" },
      { value: "dydx", label: "dYdX" },
      { value: "hyperliquid", label: "Hyperliquid" },
    ],
  },
];

interface ExchangeSwitcherProps {
  active: Exchange;
  onChange: (exchange: Exchange) => void;
  disabled?: boolean;
  /** Последнее известное число пар в снимке по бирже (бейдж на плитке). */
  pairCounts?: Partial<Record<Exchange, number>>;
}

export function ExchangeSwitcher({
  active,
  onChange,
  disabled = false,
  pairCounts,
}: ExchangeSwitcherProps) {
  return (
    <div className="flex flex-col gap-1 rounded-xl bg-surface p-1 ring-1 ring-line">
      {EXCHANGE_GROUPS.map((group) => (
        <div key={group.label} className="flex items-center gap-1">
          <span className="shrink-0 px-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
            {group.label}
          </span>
          <div className="flex flex-wrap gap-0.5">
            {group.exchanges.map(({ value, label }) => {
              const isActive = active === value;
              const count = pairCounts?.[value];
              return (
                <button
                  key={value}
                  type="button"
                  disabled={disabled}
                  onClick={() => onChange(value)}
                  title={
                    count != null ? `${label}: ${count} пар в снимке` : label
                  }
                  className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium whitespace-nowrap transition ${
                    isActive
                      ? "bg-accent text-white shadow"
                      : "text-ink-muted hover:text-ink"
                  } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                >
                  {label}
                  {count != null && count > 0 && (
                    <span
                      className={`rounded-full px-1 text-[9px] font-semibold leading-4 ${
                        isActive
                          ? "bg-white/25 text-white"
                          : "bg-ink-muted/15 text-ink-muted"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
