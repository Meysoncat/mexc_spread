import { useNavigationState } from "../hooks/useNavigationState";
import { MULTI_MARKET_EXCHANGES } from "../types";
import { ExchangeSwitcher } from "../ExchangeSwitcher";
import { SymbolPicker } from "./SymbolPicker";

/**
 * Глобальная панель выбора актива для шапки Layout: биржа + рынок + символ.
 * Видна на всех страницах, выбор синхронизируется через NavigationStateContext.
 *
 * Compact-режим (по умолчанию) — для desktop top bar.
 * Non-compact — для мобильной панели/модалки.
 */
interface GlobalAssetBarProps {
  /** Показывать селектор рынка (spot/futures). По умолчанию true. */
  showMarket?: boolean;
}

export function GlobalAssetBar({ showMarket = true }: GlobalAssetBarProps) {
  const { state, setExchange, setMarket, setSymbol } = useNavigationState();
  const { exchange, market, symbol } = state;
  const multiMarket = MULTI_MARKET_EXCHANGES.includes(exchange);

  return (
    <div className="flex items-center gap-2">
      {/* Биржа (компактный режим — один ряд без лейблов групп) */}
      <ExchangeSwitcher
        active={exchange}
        onChange={setExchange}
        compact
      />

      {/* Рынок: только для мульти-маркет бирж */}
      {showMarket && multiMarket && (
        <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
          {(["spot", "futures", "cross"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMarket(m)}
              className={`rounded px-1.5 py-0.5 text-[11px] font-medium transition ${
                market === m
                  ? "bg-accent text-accent-foreground"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {m === "futures" ? "Fut" : m === "cross" ? "Cross" : "Spot"}
            </button>
          ))}
        </div>
      )}

      {/* Символ с автодополнением из снимка выбранной биржи */}
      <SymbolPicker
        value={symbol}
        onChange={setSymbol}
        exchange={exchange}
        market={market}
        className="w-28"
      />
    </div>
  );
}
