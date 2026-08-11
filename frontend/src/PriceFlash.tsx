import { useEffect, useRef, useState, memo, type ReactNode } from "react";

interface PriceFlashProps {
  value: number | null | undefined;
  children: ReactNode;
  className?: string;
  /** Duration of flash in ms */
  flashDuration?: number;
}

/**
 * Wraps a value and briefly flashes green/red when it changes.
 * Green = value increased, Red = value decreased.
 */
export const PriceFlash = memo(function PriceFlash({
  value,
  children,
  className = "",
  flashDuration = 800,
}: PriceFlashProps) {
  const prevRef = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = value;

    if (prev == null || value == null || prev === value) return;

    // Clear previous timer
    if (timerRef.current) clearTimeout(timerRef.current);

    setFlash(value > prev ? "up" : "down");
    timerRef.current = setTimeout(() => setFlash(null), flashDuration);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [value, flashDuration]);

  const flashClass =
    flash === "up"
      ? "bg-emerald-500/15"
      : flash === "down"
        ? "bg-rose-500/15"
        : "";

  return (
    <span className={`transition-colors rounded px-0.5 ${flashClass} ${className}`}>
      {children}
    </span>
  );
});
