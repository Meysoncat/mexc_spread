import type { LucideIcon } from "lucide-react";
import { Inbox, Loader2, TriangleAlert } from "lucide-react";

type EmptyStateVariant = "empty" | "loading" | "error";

const VARIANT_ICON: Record<EmptyStateVariant, LucideIcon> = {
  empty: Inbox,
  loading: Loader2,
  error: TriangleAlert,
};

const VARIANT_ICON_CLASS: Record<EmptyStateVariant, string> = {
  empty: "text-ink-muted",
  loading: "text-accent animate-spin",
  error: "text-red-500",
};

export interface EmptyStateProps {
  /** Visual intent — picks a default icon and tint. Defaults to "empty". */
  variant?: EmptyStateVariant;
  /** Override the default icon for the variant. */
  icon?: LucideIcon;
  /** Short headline, e.g. "Нет данных". */
  title: string;
  /** Optional supporting sentence explaining why / what to do next. */
  description?: string;
  /** Optional call-to-action rendered under the text (usually a button). */
  action?: React.ReactNode;
  /** Tighten vertical padding for use in small panels. */
  compact?: boolean;
  className?: string;
}

/**
 * Unified empty / loading / error placeholder used across pages and panels.
 * Centers a tinted icon chip above a title, optional description, and action.
 */
export function EmptyState({
  variant = "empty",
  icon,
  title,
  description,
  action,
  compact = false,
  className = "",
}: EmptyStateProps) {
  const Icon = icon ?? VARIANT_ICON[variant];
  return (
    <div
      className={`flex flex-col items-center justify-center text-center ${
        compact ? "gap-2 px-4 py-8" : "gap-3 px-6 py-14"
      } ${className}`}
      role={variant === "error" ? "alert" : "status"}
    >
      <span
        className={`flex items-center justify-center rounded-full border border-line bg-surface-elevated ${
          compact ? "h-9 w-9" : "h-12 w-12"
        }`}
      >
        <Icon
          className={`${compact ? "h-4 w-4" : "h-5 w-5"} ${VARIANT_ICON_CLASS[variant]}`}
          aria-hidden="true"
        />
      </span>
      <div className="flex flex-col gap-1">
        <p
          className={`font-medium text-ink ${compact ? "text-sm" : "text-base"} text-balance`}
        >
          {title}
        </p>
        {description && (
          <p className="mx-auto max-w-sm text-xs leading-relaxed text-ink-muted text-pretty">
            {description}
          </p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/**
 * EmptyState wrapped in a table row/cell so it can drop straight into a
 * `<tbody>` that spans the full column count.
 */
export function TableEmptyState({
  colSpan,
  ...props
}: EmptyStateProps & { colSpan: number }) {
  return (
    <tr>
      <td colSpan={colSpan} className="p-0">
        <EmptyState {...props} />
      </td>
    </tr>
  );
}
