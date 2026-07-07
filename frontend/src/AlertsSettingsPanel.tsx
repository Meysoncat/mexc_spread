import { useCallback, useEffect, useState } from "react";
import { Bell, Send, Shield } from "lucide-react";
import { apiUrl } from "./config";

interface AlertConfig {
  enabled: boolean;
  bot_token: string;
  chat_id: string;
  spread_threshold_enabled: boolean;
  spread_threshold_bps: number;
  arbitrage_enabled: boolean;
  arbitrage_threshold_bps: number;
  trade_events_enabled: boolean;
  rate_limit_sec: number;
}

export function AlertsSettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [config, setConfig] = useState<AlertConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/alerts/settings"));
      if (r.ok) {
        const data = await r.json();
        if (data.ok) setConfig(data.config);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (open) fetchConfig();
  }, [open, fetchConfig]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const save = async (patch: Partial<AlertConfig>) => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch(apiUrl("/api/alerts/settings"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await r.json();
      if (data.ok) {
        setConfig(data.config);
        setMessage("✅ Сохранено");
      } else {
        setMessage(`❌ ${data.detail || "Ошибка"}`);
      }
    } catch (e) {
      setMessage("❌ Ошибка сети");
    }
    setSaving(false);
  };

  const testAlert = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const r = await fetch(apiUrl("/api/alerts/test"), { method: "POST" });
      const data = await r.json();
      setMessage(data.ok ? "✅ Тестовое сообщение отправлено" : `❌ ${data.message}`);
    } catch {
      setMessage("❌ Ошибка сети");
    }
    setTesting(false);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl border border-line bg-surface-elevated shadow-xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <Bell className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold text-ink">Telegram Алерты</h2>
          </div>
          <button onClick={onClose} className="rounded-lg border border-line p-2 text-ink hover:bg-surface">✕</button>
        </div>

        {config && (
          <div className="p-5 space-y-4 max-h-[70vh] overflow-y-auto">
            {message && <p className="text-sm rounded-lg bg-surface p-2">{message}</p>}

            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={config.enabled} onChange={(e) => save({ enabled: e.target.checked })}
                  className="h-4 w-4 rounded border-line text-blue-500" />
                <span className="text-sm font-medium text-ink">Алерты включены</span>
              </label>
            </div>

            <div className="grid grid-cols-1 gap-3">
              <label className="block">
                <span className="text-xs text-ink-muted">Bot Token</span>
                <input type="password" value={config.bot_token} onChange={(e) => setConfig({ ...config, bot_token: e.target.value })}
                  onBlur={() => { if (!config.bot_token.startsWith("*")) save({ bot_token: config.bot_token }); }}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-blue-500" />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">Chat ID</span>
                <input type="text" value={config.chat_id} onChange={(e) => setConfig({ ...config, chat_id: e.target.value })}
                  onBlur={() => save({ chat_id: config.chat_id })}
                  className="mt-0.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm font-mono text-ink outline-none focus:ring-2 focus:ring-blue-500" />
              </label>
            </div>

            <button onClick={testAlert} disabled={testing}
              className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
              <Send className="h-4 w-4" /> {testing ? "Отправка..." : "Тест"}
            </button>

            <hr className="border-line" />

            <h3 className="text-xs font-semibold uppercase text-ink-muted">Типы алертов</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={config.spread_threshold_enabled}
                    onChange={(e) => save({ spread_threshold_enabled: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-blue-500" />
                  <span className="text-sm text-ink">Спред ≥ порога</span>
                </label>
                <input type="number" step="1" value={config.spread_threshold_bps}
                  onChange={(e) => save({ spread_threshold_bps: parseFloat(e.target.value) || 0 })}
                  className="w-20 rounded-lg border border-line bg-surface px-2 py-1 text-sm font-mono text-ink" />
              </div>
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={config.arbitrage_enabled}
                    onChange={(e) => save({ arbitrage_enabled: e.target.checked })}
                    className="h-4 w-4 rounded border-line text-blue-500" />
                  <span className="text-sm text-ink">Арбитраж ≥ порога</span>
                </label>
                <input type="number" step="1" value={config.arbitrage_threshold_bps}
                  onChange={(e) => save({ arbitrage_threshold_bps: parseFloat(e.target.value) || 0 })}
                  className="w-20 rounded-lg border border-line bg-surface px-2 py-1 text-sm font-mono text-ink" />
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={config.trade_events_enabled}
                  onChange={(e) => save({ trade_events_enabled: e.target.checked })}
                  className="h-4 w-4 rounded border-line text-blue-500" />
                <span className="text-sm text-ink">Открытие/закрытие позиций</span>
              </label>
            </div>

            <label className="block">
              <span className="text-xs text-ink-muted">Rate limit (сек между сообщениями одного типа)</span>
              <input type="number" step="10" value={config.rate_limit_sec}
                onChange={(e) => save({ rate_limit_sec: parseInt(e.target.value) || 60 })}
                className="mt-0.5 w-24 rounded-lg border border-line bg-surface px-2 py-1 text-sm font-mono text-ink" />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
