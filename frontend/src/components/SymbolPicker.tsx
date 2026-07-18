import { useMemo } from "react";
import type { Exchange, Market } from "../types";
import { useSymbolOptions } from "../hooks/useSymbolOptions";

interface SymbolPickerProps {
  /** Текущее значение (controlled). */
  value: string;
  /** Колбэк при изменении — получает уже uppercase-строку. */
  onChange: (symbol: string) => void;
  /** Биржа+рынок для загрузки списка автодополнения из снимка. */
  exchange?: Exchange;
  market?: Market;
  /** Явный список опций (приоритет над exchange/market). */
  options?: string[];
  placeholder?: string;
  className?: string;
  /** aria-label для доступности. */
  label?: string;
  disabled?: boolean;
}

/**
 * Поле выбора символа с автодополнением.
 *
 * Источник опций: либо явный `options`, либо снимок биржи (exchange+market)
 * через хук useSymbolOptions. Ввод произвольного символа разрешён — datalist
 * только подсказывает, но не ограничивает.
 *
 * Использует нативный <datalist> — 0 новых зависимостей, поддерживает
 * клавиатурную навигацию, работает на мобильных.
 */
export function SymbolPicker({
  value,
  onChange,
  exchange,
  market,
  options,
  placeholder = "BTCUSDT",
  className = "",
  label = "Символ",
  disabled = false,
}: SymbolPickerProps) {
  const { symbols: snapshotSymbols } = useSymbolOptions(
    exchange ?? "mexc",
    market ?? "spot",
  );

  const finalOptions = useMemo(() => {
    const base = options ?? snapshotSymbols;
    // Гарантируем, что текущее значение присутствует в списке (для UX),
    // но без дубликатов. Ограничиваем до 4000 — datalist хорошо переваривает.
    const set = new Set<string>(base);
    if (value) set.add(value);
    return Array.from(set).slice(0, 4000);
  }, [options, snapshotSymbols, value]);

  const listId = useMemo(
    () => `symbol-options-${Math.random().toString(36).slice(2, 8)}`,
    [],
  );

  return (
    <>
      <input
        type="text"
        list={finalOptions.length > 0 ? listId : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value.toUpperCase())}
        placeholder={placeholder}
        aria-label={label}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        className={`rounded border border-line bg-surface px-2 py-1 font-mono text-xs uppercase text-ink outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      />
      {finalOptions.length > 0 && (
        <datalist id={listId}>
          {finalOptions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
    </>
  );
}
