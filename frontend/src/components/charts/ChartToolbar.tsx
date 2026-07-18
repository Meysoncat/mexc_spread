import type { ReactNode } from "react";

export interface ChartToolbarProps {
  intervals?: { value: string; label: string }[];
  visuals?: { value: string; label: string }[];
  activeInterval?: string;
  activeVisual?: string;
  onIntervalChange?: (value: string) => void;
  onVisualChange?: (value: string) => void;
  title?: ReactNode;
  status?: ReactNode;
  extra?: ReactNode;
}

export function ChartToolbar({
  intervals,
  visuals,
  activeInterval,
  activeVisual,
  onIntervalChange,
  onVisualChange,
  title,
  status,
  extra,
}: ChartToolbarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
      <div className="flex items-center gap-3">
        {title && (
          <div className="text-lg font-semibold text-ink">{title}</div>
        )}
        {status}
      </div>
      <div className="flex items-center gap-2">
        {intervals && intervals.length > 0 && (
          <div className="flex items-center gap-1">
            {intervals.map((iv) => (
              <button
                key={iv.value}
                onClick={() => onIntervalChange?.(iv.value)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                  activeInterval === iv.value
                    ? "bg-accent text-white"
                    : "border border-line text-ink-muted hover:bg-accent/10 hover:text-ink"
                }`}
              >
                {iv.label}
              </button>
            ))}
          </div>
        )}
        {visuals && visuals.length > 0 && (
          <div className="flex items-center gap-1">
            {visuals.map((v) => (
              <button
                key={v.value}
                onClick={() => onVisualChange?.(v.value)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                  activeVisual === v.value
                    ? "bg-accent text-white"
                    : "border border-line text-ink-muted hover:bg-accent/10 hover:text-ink"
                }`}
              >
                {v.label}
              </button>
            ))}
          </div>
        )}
        {extra}
      </div>
    </div>
  );
}
