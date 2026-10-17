import { useState } from "react";
import { Bell, BellRing } from "lucide-react";
import { useSetupStream } from "../setups/useSetupStream";
import { ALL_SETUP_TYPES, SETUP_TYPE_LABELS, type SetupType } from "../setups/types";

/** Алерты о новых сетапах: звук и браузерное уведомление при открытии идеи. */
export function SetupAlertToggle() {
  const { alerts, setAlerts } = useSetupStream();
  const [showPanel, setShowPanel] = useState(false);
  const Icon = alerts.enabled ? BellRing : Bell;

  const enable = (on: boolean) => {
    setAlerts({ enabled: on });
    if (on && typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => undefined);
    }
  };

  const toggleType = (t: SetupType) =>
    setAlerts({ types: alerts.types.includes(t) ? alerts.types.filter((x) => x !== t) : [...alerts.types, t] });

  return (
    <div className="relative">
      <button
        type="button"
        className={`flex h-7 w-7 items-center justify-center rounded-md transition ${
          alerts.enabled ? "text-amber-500 hover:bg-amber-500/10" : "text-ink-muted hover:bg-accent/10"
        }`}
        title={alerts.enabled ? "Алерты о сетапах включены" : "Алерты о сетапах выключены"}
        aria-label="Алерты о сетапах"
        onClick={() => setShowPanel(!showPanel)}
      >
        <Icon className="h-4 w-4" />
      </button>
      {showPanel && (
        <div className="absolute right-0 top-8 z-50 w-72 rounded-lg border border-line bg-surface-elevated p-3 text-xs shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-semibold text-ink">Алерты о новых сетапах</span>
            <button type="button" className="text-ink-muted hover:text-ink" onClick={() => setShowPanel(false)} aria-label="Закрыть">
              ✕
            </button>
          </div>
          <label className="mb-2 flex items-center justify-between text-ink-muted">
            <span>Включить</span>
            <input type="checkbox" checked={alerts.enabled} onChange={(e) => enable(e.target.checked)} className="h-4 w-4" />
          </label>
          <label className="mb-2 flex items-center justify-between text-ink-muted">
            <span>Звук</span>
            <input type="checkbox" checked={alerts.sound} onChange={(e) => setAlerts({ sound: e.target.checked })} className="h-4 w-4" />
          </label>
          <label className="mb-2 flex items-center justify-between text-ink-muted">
            <span>Минимальный score</span>
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              value={alerts.minScore}
              onChange={(e) => setAlerts({ minScore: Number(e.target.value) || 0 })}
              className="w-16 rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-ink"
            />
          </label>
          <p className="mb-1 text-ink-muted">Типы (пусто — все):</p>
          <div className="flex flex-wrap gap-1">
            {ALL_SETUP_TYPES.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => toggleType(t)}
                className={`rounded border px-1.5 py-0.5 ${alerts.types.includes(t) ? "border-accent bg-accent/10 text-accent" : "border-line text-ink-muted"}`}
              >
                {SETUP_TYPE_LABELS[t]}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
