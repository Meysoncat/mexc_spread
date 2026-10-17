import {
  createContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { v1Url } from "./api";
import type { Setup, SetupEvent } from "./types";

const ALERTS_STORAGE = "setup-alerts";
const FLUSH_MS = 300;
const RECENT_CLOSED = 100;

export interface SetupAlertSettings {
  enabled: boolean;
  minScore: number;
  types: string[]; // пусто — все типы
  sound: boolean;
}

const DEFAULT_ALERTS: SetupAlertSettings = {
  enabled: false,
  minScore: 50,
  types: [],
  sound: true,
};

export interface SetupStreamValue {
  active: Setup[];
  recentClosed: Setup[];
  connected: boolean;
  lastSeq: number | null;
  lastEventAt: number | null;
  alerts: SetupAlertSettings;
  setAlerts: (next: Partial<SetupAlertSettings>) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export const SetupStreamContext = createContext<SetupStreamValue | null>(null);

function readAlerts(): SetupAlertSettings {
  try {
    const raw = localStorage.getItem(ALERTS_STORAGE);
    return raw ? { ...DEFAULT_ALERTS, ...JSON.parse(raw) } : DEFAULT_ALERTS;
  } catch {
    return DEFAULT_ALERTS;
  }
}

function streamUrl(): string {
  return v1Url("/setups/stream");
}

let audioCtx: AudioContext | null = null;

function beep(): void {
  try {
    audioCtx ??= new AudioContext();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.connect(gain).connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.15);
  } catch {
    /* звук недоступен */
  }
}

function notify(s: Setup): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const edge = s.edge_bps != null ? ` · edge ${s.edge_bps.toFixed(1)} bps` : "";
  const dir = s.direction ? ` ${s.direction}` : "";
  try {
    new Notification(`${s.base}${dir}: ${s.type}`, {
      body: `score ${s.score.toFixed(0)}${edge}\n${s.notes[0] ?? ""}`,
      tag: s.instance,
    });
  } catch {
    /* уведомления недоступны */
  }
}

/**
 * Одно SSE-подключение к /api/v1/setups/stream на всё приложение.
 * EventSource сам переподключается и шлёт Last-Event-ID — сервер дочитывает
 * пропущенное. События копятся и применяются пачкой раз в FLUSH_MS.
 */
export function SetupStreamProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<Setup[]>([]);
  const [recentClosed, setRecentClosed] = useState<Setup[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastSeq, setLastSeq] = useState<number | null>(null);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [alerts, setAlertsState] = useState<SetupAlertSettings>(readAlerts);

  const alertsRef = useRef(alerts);
  useEffect(() => {
    alertsRef.current = alerts;
  }, [alerts]);

  const setAlerts = useMemo(
    () => (next: Partial<SetupAlertSettings>) => {
      setAlertsState((prev) => {
        const merged = { ...prev, ...next };
        try {
          localStorage.setItem(ALERTS_STORAGE, JSON.stringify(merged));
        } catch {
          /* не сохранится между сессиями */
        }
        return merged;
      });
    },
    [],
  );

  useEffect(() => {
    const map = new Map<string, Setup>();
    const closed: Setup[] = [];
    let dirty = false;
    let seq: number | null = null;

    const es = new EventSource(streamUrl());
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.addEventListener("snapshot", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { seq: number; setups: Setup[] };
      map.clear();
      for (const s of data.setups) map.set(s.instance, s);
      seq = data.seq;
      dirty = true;
    });

    const onEvent = (e: Event) => {
      const ev = JSON.parse((e as MessageEvent).data) as SetupEvent;
      seq = ev.seq;
      dirty = true;
      if (ev.event === "closed") {
        map.delete(ev.setup.instance);
        closed.unshift(ev.setup);
        if (closed.length > RECENT_CLOSED) closed.length = RECENT_CLOSED;
        return;
      }
      map.set(ev.setup.instance, ev.setup);
      if (ev.event === "opened") {
        const a = alertsRef.current;
        if (
          a.enabled &&
          ev.setup.score >= a.minScore &&
          (a.types.length === 0 || a.types.includes(ev.setup.type))
        ) {
          if (a.sound) beep();
          notify(ev.setup);
        }
      }
    };
    es.addEventListener("opened", onEvent);
    es.addEventListener("updated", onEvent);
    es.addEventListener("closed", onEvent);

    const timer = setInterval(() => {
      if (!dirty) return;
      dirty = false;
      setActive(Array.from(map.values()));
      setRecentClosed(closed.slice());
      setLastSeq(seq);
      setLastEventAt(Date.now());
    }, FLUSH_MS);

    return () => {
      clearInterval(timer);
      es.close();
    };
  }, []);

  const value = useMemo<SetupStreamValue>(
    () => ({ active, recentClosed, connected, lastSeq, lastEventAt, alerts, setAlerts }),
    [active, recentClosed, connected, lastSeq, lastEventAt, alerts, setAlerts],
  );

  return <SetupStreamContext.Provider value={value}>{children}</SetupStreamContext.Provider>;
}
