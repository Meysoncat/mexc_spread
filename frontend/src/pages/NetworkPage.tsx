import { useEffect, useState } from "react";
import { Globe, Wifi, WifiOff, Loader2, RotateCcw } from "lucide-react";
import { apiUrl } from "../config";
import { proxyError } from "../lib/proxy";
import { EXCHANGE_LABELS, type Exchange } from "../types";

interface NetworkState {
  default_proxy: string | null;
  per_exchange: Record<string, string>;
  per_exchange_effective: Record<string, string | null>;
  config_proxy: string;
  active_proxy: string | null;
  known_exchanges: Exchange[];
  note: string;
}

interface TestResult {
  reachable: boolean;
  status?: string;
  status_code?: number | null;
  elapsed_ms?: number | null;
  error?: string | null;
  proxy?: string | null;
}

export function NetworkPage() {
  const [state, setState] = useState<NetworkState | null>(null);
  const [defaultDraft, setDefaultDraft] = useState("");
  const [rowDrafts, setRowDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestResult>>({});
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    fetch(apiUrl("/api/network/config"))
      .then((r) => r.json())
      .then((d: NetworkState) => {
        setState(d);
        setDefaultDraft(d.default_proxy ?? "");
        setRowDrafts(
          Object.fromEntries(
            d.known_exchanges.map((ex) => [ex, d.per_exchange[ex] ?? ""]),
          ),
        );
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  };

  useEffect(load, []);

  const patch = (body: Record<string, unknown>) => {
    setSaving(true);
    setErr(null);
    return fetch(apiUrl("/api/network/config"), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
        return d as NetworkState;
      })
      .then((d) => {
        setState(d);
        setDefaultDraft(d.default_proxy ?? "");
        setRowDrafts(
          Object.fromEntries(
            d.known_exchanges.map((ex) => [ex, d.per_exchange[ex] ?? ""]),
          ),
        );
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };

  const runTest = (ex: string) => {
    setTesting(ex);
    fetch(apiUrl(`/api/network/test?exchange=${ex}`))
      .then((r) => r.json())
      .then((d) => setTests((prev) => ({ ...prev, [ex]: d })))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setTesting(null));
  };

  const defaultErr = proxyError(defaultDraft);
  const usingProxy = Boolean(state?.active_proxy);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <Globe className="h-5 w-5 text-accent" />
        <div>
          <h1 className="text-base font-semibold text-ink">Сеть / Прокси</h1>
          <p className="text-xs text-ink-muted">
            Умная маршрутизация: общий прокси + переопределения по биржам.
            MetaScalp (localhost) — всегда напрямую.
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-4 py-4">
        {/* Default proxy */}
        <div className="mb-4 rounded-lg border border-line bg-surface-elevated p-4">
          <div className="mb-3 flex items-center gap-2 text-sm">
            {usingProxy ? (
              <Wifi className="h-4 w-4 text-emerald-500" />
            ) : (
              <WifiOff className="h-4 w-4 text-ink-muted" />
            )}
            <span className="font-medium text-ink">
              {usingProxy ? "Общий прокси активен" : "Прямое подключение"}
            </span>
          </div>
          <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-muted">
            Общий прокси (default)
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={defaultDraft}
              onChange={(e) => setDefaultDraft(e.target.value)}
              placeholder="http://host:port · socks5h://host:1080 · (пусто = без прокси)"
              className={`flex-1 rounded-md border bg-surface px-3 py-2 font-mono text-sm text-ink focus:outline-none ${
                defaultErr
                  ? "border-red-500 focus:border-red-500"
                  : "border-line focus:border-accent"
              }`}
            />
            <button
              onClick={() => patch({ default_proxy: defaultDraft.trim() })}
              disabled={saving || Boolean(defaultErr)}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-foreground hover:bg-accent/90 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Globe className="h-4 w-4" />
              )}
              Применить
            </button>
          </div>
          {defaultErr ? (
            <p className="mt-2 text-[11px] text-red-500">{defaultErr}</p>
          ) : (
            <p className="mt-2 text-[11px] text-ink-muted">
              Наследуется всеми биржами, кроме переопределённых ниже. Схемы:
              http, https, socks5, socks5h.
            </p>
          )}
        </div>

        {/* Per-exchange overrides */}
        <div className="rounded-lg border border-line bg-surface-elevated p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium text-ink">
              Маршрутизация по биржам
            </span>
            <span className="text-[11px] text-ink-muted">
              пусто = наследовать · direct = напрямую
            </span>
          </div>
          <div className="overflow-hidden rounded-md border border-line/60">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line/60 bg-surface text-left text-[11px] uppercase tracking-wide text-ink-muted">
                  <th className="px-3 py-2 font-medium">Биржа</th>
                  <th className="px-3 py-2 font-medium">
                    Прокси (override)
                  </th>
                  <th className="px-3 py-2 font-medium">Эффективный</th>
                  <th className="px-3 py-2 text-right font-medium">Тест</th>
                </tr>
              </thead>
              <tbody>
                {state?.known_exchanges.map((ex) => {
                  const draft = rowDrafts[ex] ?? "";
                  const rowErr = proxyError(draft);
                  const eff = state.per_exchange_effective[ex];
                  const t = tests[ex];
                  return (
                    <tr
                      key={ex}
                      className="border-b border-line/40 last:border-0"
                    >
                      <td className="px-3 py-2 font-medium text-ink">
                        {EXCHANGE_LABELS[ex] ?? ex}
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          value={draft}
                          onChange={(e) =>
                            setRowDrafts((prev) => ({
                              ...prev,
                              [ex]: e.target.value,
                            }))
                          }
                          onBlur={() => {
                            if (
                              !rowErr &&
                              draft.trim() !== (state.per_exchange[ex] ?? "")
                            )
                              patch({ per_exchange: { [ex]: draft.trim() } });
                          }}
                          placeholder="наследовать"
                          className={`w-full rounded border bg-surface px-2 py-1 font-mono text-xs text-ink focus:outline-none ${
                            rowErr
                              ? "border-red-500"
                              : "border-line/60 focus:border-accent"
                          }`}
                        />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-ink-muted">
                        {eff ?? "прямое"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {t && (
                            <span
                              className={
                                t.reachable
                                  ? "text-xs text-emerald-500"
                                  : "text-xs text-red-500"
                              }
                            >
                              {t.reachable
                                ? `✓ ${t.elapsed_ms}мс`
                                : `✗ ${t.status ?? "err"}`}
                            </span>
                          )}
                          <button
                            onClick={() => runTest(ex)}
                            disabled={testing === ex}
                            className="rounded border border-line px-2 py-1 text-xs text-ink hover:border-accent hover:text-accent disabled:opacity-50"
                          >
                            {testing === ex ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              "Проверить"
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-ink-muted">{state?.note}</p>
        </div>

        {(state?.config_proxy || err) && (
          <div className="mt-3 flex items-center justify-between text-xs">
            {state?.config_proxy ? (
              <span className="text-ink-muted">
                <RotateCcw className="mr-1 inline h-3 w-3" />
                config (external_apis.json): {state.config_proxy}
              </span>
            ) : (
              <span />
            )}
            {err && <span className="text-red-500">Ошибка: {err}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
