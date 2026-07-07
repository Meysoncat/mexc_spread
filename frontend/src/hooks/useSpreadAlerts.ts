import { useCallback, useEffect, useRef, useState } from "react";

interface SpreadAlertConfig {
  enabled: boolean;
  threshold_bps: number;
  symbols: string[];
}

const STORAGE_KEY = "spread_alert_config";
const POLL_INTERVAL_SEC = 10;
const COOLDOWN_SEC = 30;

const DEFAULT_CONFIG: SpreadAlertConfig = {
  enabled: false,
  threshold_bps: 20,
  symbols: [],
};

function loadConfig(): SpreadAlertConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_CONFIG;
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_CONFIG;
  }
}

function saveConfig(cfg: SpreadAlertConfig) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
  } catch {
    /* ignore */
  }
}

type BeepType = "high" | "low";

function playBeep(type: BeepType) {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = type === "high" ? 880 : 440;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.3);
    osc.onended = () => ctx.close();
  } catch {
    /* AudioContext not available */
  }
}

export function useSpreadAlerts() {
  const [config, setConfig] = useState<SpreadAlertConfig>(loadConfig);
  const cooldownRef = useRef<Set<string>>(new Set());

  const updateConfig = useCallback((patch: Partial<SpreadAlertConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch };
      saveConfig(next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!config.enabled || config.symbols.length === 0) return;

    const checkAlerts = async () => {
      for (const sym of config.symbols) {
        if (cooldownRef.current.has(sym)) continue;
        try {
          const r = await fetch(
            `/api/spread/latest?symbol=${encodeURIComponent(sym)}`,
          );
          if (!r.ok) continue;
          const data = await r.json();
          const bps = data.tick?.spread_bps;
          if (bps != null && bps >= config.threshold_bps) {
            playBeep(bps >= 50 ? "high" : "low");
            cooldownRef.current.add(sym);
            setTimeout(() => cooldownRef.current.delete(sym), COOLDOWN_SEC * 1000);
            break; // One beep per cycle
          }
        } catch {
          /* ignore */
        }
      }
    };

    checkAlerts();
    const id = setInterval(checkAlerts, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [config]);

  return { config, updateConfig };
}
