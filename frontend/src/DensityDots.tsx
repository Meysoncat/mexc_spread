import { memo } from "react";

interface DensityDotsProps {
  wallCount: number;
  largestWallNotional: number | null;
  imbalance: string | null;
  className?: string;
}

/**
 * Compact density indicator — shows wall count as dots and imbalance as color.
 * Used in table cells to quickly visualize order book density.
 */
export const DensityDots = memo(function DensityDots({
  wallCount,
  largestWallNotional,
  imbalance,
  className = "",
}: DensityDotsProps) {
  if (wallCount === 0 && !imbalance) {
    return <span className={`text-ink-muted/30 ${className}`}>—</span>;
  }

  const dots = Math.min(5, wallCount);
  const imbalanceColor =
    imbalance === "bid_heavy"
      ? "text-emerald-500"
      : imbalance === "ask_heavy"
        ? "text-rose-500"
        : "text-ink-muted";

  const fmtWall = (n: number | null): string => {
    if (n == null) return "";
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
    return `$${n.toFixed(0)}`;
  };

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      {/* Wall count dots */}
      <div className="flex gap-0.5">
        {Array.from({ length: dots }, (_, i) => (
          <div
            key={i}
            className={`h-1.5 w-1.5 rounded-full ${
              i < 3 ? "bg-emerald-500" : i < 4 ? "bg-amber-500" : "bg-rose-500"
            }`}
          />
        ))}
        {wallCount > 5 && (
          <span className="text-[8px] text-ink-muted">+{wallCount - 5}</span>
        )}
      </div>
      {/* Imbalance indicator */}
      {imbalance && (
        <span className={`text-[9px] font-mono ${imbalanceColor}`}>
          {imbalance === "bid_heavy" ? "B" : imbalance === "ask_heavy" ? "A" : "="}
        </span>
      )}
      {/* Largest wall */}
      {largestWallNotional != null && largestWallNotional > 0 && (
        <span className="text-[9px] font-mono text-ink-muted">
          {fmtWall(largestWallNotional)}
        </span>
      )}
    </div>
  );
});
