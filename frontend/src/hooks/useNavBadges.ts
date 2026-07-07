import { useState, useEffect, useCallback } from "react";

interface BadgeMap {
  [path: string]: { count: number; level: "info" | "warning" | "critical" };
}

const POLL_SEC = 15;

export function useNavBadges(): BadgeMap {
  const [badges, setBadges] = useState<BadgeMap>({});

  const fetchBadges = useCallback(async () => {
    const next: BadgeMap = {};
    try {
      const [cap, arb, fa, ll] = await Promise.allSettled([
        fetch("/api/capture/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/arbitrage/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/futures-arb/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/lead-lag/signals?active=true&limit=100").then((r) => r.ok ? r.json() : null),
      ]);

      if (cap.status === "fulfilled" && cap.value?.position) {
        const pos = cap.value.position;
        if (pos.state === "holding" || pos.state === "pending_buy" || pos.state === "pending_sell") {
          next["/spread-capture"] = { count: 1, level: "info" };
        }
        if (cap.value.settings?.kill_switch) {
          next["/spread-capture"] = { count: 1, level: "critical" };
        }
      }

      if (arb.status === "fulfilled" && arb.value?.open_count > 0) {
        next["/arbitrage"] = { count: arb.value.open_count, level: "info" };
      }

      if (fa.status === "fulfilled" && fa.value?.open_count > 0) {
        next["/futures-arb"] = { count: fa.value.open_count, level: "info" };
      }

      if (ll.status === "fulfilled" && Array.isArray(ll.value) && ll.value.length > 0) {
        next["/lead-lag"] = { count: ll.value.length, level: "warning" };
      }
    } catch {
      /* ignore */
    }
    setBadges(next);
  }, []);

  useEffect(() => {
    fetchBadges();
    const id = setInterval(fetchBadges, POLL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchBadges]);

  return badges;
}
