import {
  EXCHANGE_GROUPS,
  type Exchange,
  type ExchangeGroup,
} from "./types";

// Канонический реестр бирж живёт в types.ts (единый источник).
// Реэкспортируем сюда для обратной совместимости с существующими импортами.
export {
  MULTI_MARKET_EXCHANGES,
  EXCHANGE_GROUPS,
  ALL_EXCHANGES,
  EXCHANGE_LABELS,
} from "./types";
export type { ExchangeGroup };

interface ExchangeSwitcherProps {
  active: Exchange;
  onChange: (exchange: Exchange) => void;
  disabled?: boolean;
  /** Компактный режим: без групповых лейблов, один ряд (для шапки Layout). */
  compact?: boolean;
  /** Последнее известное число пар в снимке по бирже (бейдж на плитке). */
  pairCounts?: Partial<Record<Exchange, number>>;
}

export function ExchangeSwitcher({
  active,
  onChange,
  disabled = false,
  compact = false,
  pairCounts,
}: ExchangeSwitcherProps) {
  if (compact) {
    // Компактный режим: плоский список бирж одним рядом, без заголовков групп.
    return (
      <div className="flex flex-wrap items-center gap-0.5 rounded-lg bg-surface p-0.5 ring-1 ring-line">
        {EXCHANGE_GROUPS.flatMap((g) => g.exchanges).map(
          ({ value, label }) => {
            const isActive = active === value;
            return (
              <button
                key={value}
                type="button"
                disabled={disabled}
                onClick={() => onChange(value)}
                title={label}
                className={`rounded px-1.5 py-0.5 text-[11px] font-medium transition ${
                  isActive
                    ? "bg-accent text-white"
                    : "text-ink-muted hover:text-ink"
                } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
              >
                {label}
              </button>
            );
          },
        )}
      </div>
    );
  }

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
