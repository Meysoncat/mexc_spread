import { memo } from "react";

interface FundingBadgeProps {
  value: number | null | undefined;
  className?: string;
}

/**
 * Funding rate badge — shows funding as a colored pill.
 * Green for positive (longs pay shorts), red for negative.
 * Includes annualized percentage.
 */
export const FundingBadge = memo(function FundingBadge({
  value,
  className = "",
}: FundingBadgeProps) {
  if (value == null) {
    return <span className={`text-ink-muted/30 ${className}`}>—</span>;
  }

  const pct = value * 100;
  const annualized = value * 3 * 365 * 100;

  const color =
    Math.abs(value) >= 0.001
      ? value > 0
        ? "bg-emerald-500/15 text-emerald-500"
        : "bg-rose-500/15 text-rose-500"
      : "bg-surface text-ink-muted";

  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono ${color} ${className}`}
      title={`Annualized: ${annualized.toFixed(1)}%`}
    >
      {pct >= 0 ? "+" : ""}
      {pct.toFixed(3)}%
    </span>
  );
});
