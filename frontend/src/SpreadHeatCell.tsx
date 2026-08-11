import { memo } from "react";

interface SpreadHeatCellProps {
  value: number | null | undefined;
  threshold?: number;
  className?: string;
}

/**
 * Spread cell with heat-map coloring — green for low spread, red for high.
 * Shows a mini bar proportional to the spread value.
 */
export const SpreadHeatCell = memo(function SpreadHeatCell({
  value,
  threshold = 50,
  className = "",
}: SpreadHeatCellProps) {
  if (value == null) {
    return <td className={`px-4 py-2.5 text-ink-muted ${className}`}>—</td>;
  }

  // Color based on spread value
  const color =
    value >= threshold * 2
      ? "text-rose-500 font-bold"
      : value >= threshold
        ? "text-amber-500 font-semibold"
        : value >= threshold * 0.5
          ? "text-emerald-500"
          : "text-emerald-600 dark:text-emerald-400";

  // Bar width proportional to threshold
  const barPct = Math.min(100, (value / threshold) * 100);
  const barColor =
    value >= threshold * 2
      ? "bg-rose-500/40"
      : value >= threshold
        ? "bg-amber-500/30"
        : "bg-emerald-500/20";

  return (
    <td className={`px-4 py-2.5 ${className}`}>
      <div className="flex items-center gap-1.5">
        <span className={`font-mono tabular-nums text-xs ${color}`}>
          {value.toFixed(2)}
        </span>
        <div className="flex-1 h-1 rounded-full bg-surface overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${barColor}`}
            style={{ width: `${barPct}%` }}
          />
        </div>
      </div>
    </td>
  );
});
