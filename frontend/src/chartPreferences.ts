import type { ChartVisualType } from "./types";

export function readStoredVisual(): ChartVisualType {
  try {
    const v = localStorage.getItem("mexc-ui-chart-visual");
    if (v === "line" || v === "candle") return v;
  } catch {
    /* ignore */
  }
  return "candle";
}
