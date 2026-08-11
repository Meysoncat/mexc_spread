/**
 * Shared price-format helper for lightweight-charts price scales.
 *
 * Extracted from PriceChart/ChartWidget where it was duplicated verbatim.
 * Picks a sensible precision/minMove based on a sample price.
 */
export function priceFormatFromSample(sample: number) {
  const p = Math.abs(sample);
  if (!Number.isFinite(p) || p === 0) {
    return { type: "price" as const, precision: 4, minMove: 0.0001 };
  }
  if (p >= 100) return { type: "price" as const, precision: 2, minMove: 0.01 };
  if (p >= 1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (p >= 0.01) return { type: "price" as const, precision: 6, minMove: 1e-6 };
  return { type: "price" as const, precision: 8, minMove: 1e-8 };
}
