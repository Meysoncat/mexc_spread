import { useEffect, useState } from "react";
import { Globe, Wifi, WifiOff, Loader2 } from "lucide-react";

interface NetworkState {
  http_proxy_url: string;
  active_proxy: string | null;
  source: string;
  note: string;
}

interface TestResult {
  reachable: boolean;
  status_code?: number;
  elapsed_ms?: number;
  error?: string;
  proxy?: string | null;
}

export function NetworkPage() {
  const [state, setState] = useState<NetworkState | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    fetch("/api/network/config")
      .then((r) => r.json())
      .then((d) => {
        setState(d);
        setDraft(d.http_proxy_url ?? "");
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  };

  useEffect(load, []);

  const apply = () => {
    setSaving(true);
    setErr(null);
    fetch("/api/network/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ http_proxy_url: draft.trim() }),
    })
      .then((r) => r.json())
      .then((d) => {
        setState(d);
        setTest(null);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };

  const runTest = () => {
    setTesting(true);
    setTest(null);
    fetch("/api/network/test")
      .then((r) => r.json())
      .then((d) => setTest(d))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setTesting(false));
  };

  const sourceLabel: Record<string, string> = {
    runtime: "runtime (панель)",
    config: "config (external_apis.json)",
    env: "env (HTTP_PROXY/HTTPS_PROXY)",
    none: "нет — прямой",
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <Globe className="h-5 w-5 text-accent" />
        <div>
          <h1 className="text-base font-semibold text-ink">Сеть / Прокси</h1>
          <p className="text-xs text-ink-muted">
            Шлюз для трафика бирж (MEXC и др.). MetaScalp (localhost) — всегда
            напрямую.
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-4 py-4">
        {/* Status */}
        <div className="mb-4 rounded-lg border border-line bg-surface-elevated p-4">
          <div className="flex items-center gap-2 text-sm">
            {state?.active_proxy ? (
              <Wifi className="h-4 w-4 text-emerald-500" />
            ) : (
              <WifiOff className="h-4 w-4 text-ink-muted" />
            )}
            <span className="font-medium text-ink">
              {state?.active_proxy ? "Через прокси" : "Прямое подключение"}
            </span>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs text-ink-muted">
            <dt className="font-medium">Активный прокси</dt>
            <dd className="font-mono text-ink">
              {state?.active_proxy ?? "—"}
            </dd>
            <dt className="font-medium">Источник</dt>
            <dd className="text-ink">
              {state ? sourceLabel[state.source] ?? state.source : "—"}
            </dd>
          </dl>
          <p className="mt-2 text-[11px] text-ink-muted">{state?.note}</p>
        </div>

        {/* Edit */}
        <div className="mb-4 rounded-lg border border-line bg-surface-elevated p-4">
          <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-muted">
            Proxy URL
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="http://127.0.0.1:7890   или   socks5://127.0.0.1:1080   (пусто = сбросить)"
              className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
            <button
              onClick={apply}
              disabled={saving}
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
          <p className="mt-2 text-[11px] text-ink-muted">
            Применяется мгновенно к новым запросам бирж. Пустое поле — без
            прокси (httpx вернётся к env-переменным).
          </p>
        </div>

        {/* Test */}
        <div className="rounded-lg border border-line bg-surface-elevated p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-ink">
              Проверка связи с MEXC
            </span>
            <button
              onClick={runTest}
              disabled={testing}
              className="flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {testing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Wifi className="h-4 w-4" />
              )}
              Проверить
            </button>
          </div>
          {test && (
            <div className="mt-3 text-sm">
              {test.reachable ? (
                <span className="text-emerald-500">
                  ✓ MEXC доступен · {test.elapsed_ms} мс
                  {test.status_code ? ` · HTTP ${test.status_code}` : ""}
                </span>
              ) : (
                <span className="text-red-500">
                  ✗ Недоступно · {test.error ?? "нет ответа"} · {test.elapsed_ms}{" "}
                  мс
                </span>
              )}
            </div>
          )}
        </div>

        {err && (
          <p className="mt-3 text-xs text-red-500">Ошибка: {err}</p>
        )}
      </div>
    </div>
  );
}
