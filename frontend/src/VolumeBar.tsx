import { memo } from "react";

interface VolumeBarProps {
  value: number;
  maxValue: number;
  className?: string;
}

/**
 * Inline volume bar — shows volume as a colored bar with label.
 * Used in table cells to visualize volume distribution.
 */
export const VolumeBar = memo(function VolumeBar({
  value,
  maxValue,
  className = "",
}: VolumeBarProps) {
  const pct = maxValue > 0 ? Math.min(100, (value / maxValue) * 100) : 0;

  const fmt = (n: number): string => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
    return n.toFixed(0);
  };

  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      <div className="flex-1 h-1.5 rounded-full bg-surface overflow-hidden">
        <div
          className="h-full rounded-full bg-accent/50 transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-ink-muted whitespace-nowrap">
        {fmt(value)}
      </span>
    </div>
  );
});
