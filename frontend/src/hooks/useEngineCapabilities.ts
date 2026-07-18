import { useEffect, useState } from "react";
import { apiFetch } from "../config";

/**
 * Engine keys returned by GET /api/system/capabilities under `live_ready` /
 * `reasons`. Keep this in sync with the backend endpoint.
 */
export type EngineKey = "capture" | "arbitrage" | "futures_arb" | "trading";

interface CapabilitiesResponse {
  ok: boolean;
  live_ready: Record<EngineKey, boolean>;
  reasons: Record<EngineKey, string[] | Record<string, string[]>>;
}

/**
 * Module-level cache so multiple engine pages don't each fire their own
 * /api/system/capabilities request. The data is slow-moving (it depends only
 * on env vars + injected executors, both of which change on backend restart),
 * so a single fetch per page load is enough.
 */
let cachedPromise: Promise<CapabilitiesResponse | null> | null = null;

async function loadCapabilities(): Promise<CapabilitiesResponse | null> {
  if (!cachedPromise) {
    cachedPromise = apiFetch("/api/system/capabilities")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return cachedPromise;
}

/** Forget the cached capabilities — used by tests or after a backend restart. */
export function resetCapabilitiesCache(): void {
  cachedPromise = null;
}

export interface EngineCapability {
  /** true iff this engine can place real orders right now. */
  liveReady: boolean;
  /** Human-readable reasons why not (empty when liveReady is true). */
  reasons: string[];
  /** true while the capabilities request is in flight (first render). */
  loading: boolean;
}

/**
 * Read whether a given engine is ready to trade in live mode.
 *
 * Usage on an engine page:
 *   const { liveReady, reasons } = useEngineCapabilities("capture");
 *   {!liveReady && <LiveModeWarning reasons={reasons} engineName="Spread Capture" currentMode={mode} />}
 *
 * The hook intentionally does NOT block rendering while loading — the engine
 * page still works, just without the warning until capabilities arrive. This
 * avoids a flash of empty content for the common case where everything IS ready.
 */
export function useEngineCapabilities(engine: EngineKey): EngineCapability {
  const [state, setState] = useState<EngineCapability>({
    liveReady: false,
    reasons: [],
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    loadCapabilities().then((caps) => {
      if (cancelled || !caps) {
        setState({ liveReady: true, reasons: [], loading: false });
        return;
      }
      const ready = Boolean(caps.live_ready?.[engine]);
      const rawReasons = caps.reasons?.[engine];
      // `reasons` is either a string[] (capture/arbitrage/futures_arb) or a
      // Record<exchange, string[]> (trading). Flatten the latter into strings.
      let reasons: string[] = [];
      if (Array.isArray(rawReasons)) {
        reasons = rawReasons;
      } else if (rawReasons && typeof rawReasons === "object") {
        reasons = Object.entries(rawReasons).map(
          ([ex, r]) => `${ex}: ${(r as string[]).join(", ")}`,
        );
      }
      if (cancelled) return;
      setState({ liveReady: ready, reasons, loading: false });
    });
    return () => {
      cancelled = true;
    };
  }, [engine]);

  return state;
}
