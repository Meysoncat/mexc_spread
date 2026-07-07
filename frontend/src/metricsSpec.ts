import bundledData from "./data/metrics-reference.json";
import { apiUrl } from "./config";

export type MetricsReferenceLink = {
  label: string;
  href: string;
};

export type MetricsReferenceRow = {
  key: string;
  label: string;
  description: string;
  formula?: string;
};

export type MetricsReferenceSpec = {
  version: string;
  title: string;
  intro?: string;
  links?: MetricsReferenceLink[];
  rows: MetricsReferenceRow[];
};

export const BUNDLED_METRICS_SPEC = bundledData as MetricsReferenceSpec;

function isSpec(x: unknown): x is MetricsReferenceSpec {
  if (!x || typeof x !== "object") return false;
  const o = x as Record<string, unknown>;
  return (
    typeof o.version === "string" &&
    typeof o.title === "string" &&
    Array.isArray(o.rows)
  );
}

/**
 * Порядок: API (можно отдать свежую спецификацию без пересборки UI) →
 * metrics-reference.json из public → встроенная копия из src/data.
 */
export async function loadMetricsReferenceSpec(): Promise<MetricsReferenceSpec> {
  try {
    const r = await fetch(apiUrl("/api/metrics-reference"));
    if (r.ok) {
      const j: unknown = await r.json();
      if (isSpec(j)) return j;
    }
  } catch {
    /* offline / нет API */
  }

  try {
    const base = import.meta.env.BASE_URL || "/";
    const url = base.endsWith("/")
      ? `${base}metrics-reference.json`
      : `${base}/metrics-reference.json`;
    const r = await fetch(url, { cache: "no-cache" });
    if (r.ok) {
      const j: unknown = await r.json();
      if (isSpec(j)) return j;
    }
  } catch {
    /* ignore */
  }

  return BUNDLED_METRICS_SPEC;
}
