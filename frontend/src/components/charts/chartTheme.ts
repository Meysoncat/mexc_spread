/**
 * Unified chart theme — single source of truth for chart appearance.
 *
 * Colors are read from the same CSS custom properties the rest of the UI uses
 * (see `index.css`: `--surface-elevated`, `--ink`, `--line`, `--accent`).
 * Because toggling `.dark` on <html> changes those variable values, charts
 * follow the app theme automatically — no `isDark` prop-drilling and no
 * duplicated color constants.
 */
import { useEffect, useState } from "react";
import { ColorType } from "lightweight-charts";
import type { DeepPartial, ChartOptions } from "lightweight-charts";

export type ChartMode = "dark" | "light";

/**
 * Read a CSS custom property from :root. Values are stored as "R G B" triplets
 * (e.g. `--surface-elevated: 30 41 59`); this returns a ready-to-use
 * `rgb(R G B)` string, or `null` if the variable is unset.
 *
 * Always reflects the CURRENT theme — re-read after a `.dark` toggle to pick up
 * the new values.
 */
export function tokenVar(name: string): string | null {
  if (typeof window === "undefined") return null;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return null;
  return `rgb(${raw})`;
}

function tokenOr(name: string, fallback: string): string {
  return tokenVar(name) ?? fallback;
}

/**
 * Build lightweight-charts `ChartOptions` from the active app theme tokens.
 * Background/text/grid/border follow `surface-elevated` / `ink` / `line`, so a
 * chart stays in sync with the light/dark toggle without per-instance config.
 */
export function resolveChartOptions(): DeepPartial<ChartOptions> {
  const background = tokenOr("--surface-elevated", "#1e293b");
  const text = tokenOr("--ink", "#e2e8f0");
  const grid = tokenOr("--line", "#334155");
  return {
    layout: {
      background: { type: ColorType.Solid, color: background },
      textColor: text,
    },
    grid: {
      vertLines: { color: grid },
      horzLines: { color: grid },
    },
    rightPriceScale: {
      borderColor: grid,
      autoScale: true,
      scaleMargins: { top: 0.12, bottom: 0.12 },
      entireTextOnly: false,
    },
    timeScale: {
      borderColor: grid,
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: { mode: 0 },
  };
}

/**
 * Centralized series color palette. Replaces the 3+ divergent palettes that
 * previously lived inline in each chart component. Fixed colors are static;
 * `accent` / `line` are getters so they always read the live `--accent` token.
 */
export const chartColors = {
  up: "#26a69a",
  down: "#ef5350",
  bid: "#26a69a",
  ask: "#ef5350",
  spread: "#f59e0b",
  basis: "#4fc3f7",
  volume: "#3b82f6",
  get accent(): string {
    return tokenOr("--accent", "#0ea5e9");
  },
  get line(): string {
    return tokenOr("--accent", "#0ea5e9");
  },
} as const;

/**
 * Track the active app theme by observing the `.dark` class on <html>.
 * Re-renders when the class changes, so chart components can follow the global
 * theme without an explicit React context.
 */
export function useAppTheme(): { mode: ChartMode } {
  const [mode, setMode] = useState<ChartMode>(() =>
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
      ? "dark"
      : "light",
  );
  useEffect(() => {
    const el = document.documentElement;
    const observer = new MutationObserver(() => {
      setMode(el.classList.contains("dark") ? "dark" : "light");
    });
    observer.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return { mode };
}
