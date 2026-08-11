import { useCallback, useEffect, useState } from "react";
import { Users, CheckCircle, XCircle } from "lucide-react";
import { apiUrl } from "../config";

interface Account {
  id: string;
  label: string;
  exchange: string;
  market: string;
  api_key_env: string;
  api_secret_env: string;
  enabled: boolean;
  has_credentials: boolean;
}

export function AccountsPanel() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(apiUrl("/api/trading/accounts"));
      const d = await r.json();
      if (d.ok) setAccounts(d.accounts ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="rounded-xl border border-line bg-surface-elevated p-4">
      <div className="flex items-center gap-2 mb-3">
        <Users className="h-4 w-4 text-accent" />
        <h3 className="text-sm font-semibold text-ink">Торговые аккаунты</h3>
        {loading && <span className="text-xs text-ink-muted">…</span>}
      </div>
      {accounts.length === 0 ? (
        <p className="text-xs text-ink-muted">
          Нет настроенных аккаунтов. Добавьте в <code>config/trading_accounts.json</code>.
        </p>
      ) : (
        <div className="space-y-2">
          {accounts.map((acc) => (
            <div
              key={acc.id}
              className="flex items-center justify-between rounded-lg bg-surface px-3 py-2"
            >
              <div>
                <div className="text-sm font-medium text-ink">{acc.label}</div>
                <div className="text-xs text-ink-muted">
                  {acc.exchange.toUpperCase()} / {acc.market} · ID: {acc.id}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {acc.has_credentials ? (
                  <span className="flex items-center gap-1 text-xs text-emerald-500">
                    <CheckCircle className="h-3.5 w-3.5" /> Ключи есть
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-ink-muted">
                    <XCircle className="h-3.5 w-3.5" /> Нет ключей
                  </span>
                )}
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                    acc.enabled
                      ? "bg-emerald-500/10 text-emerald-600"
                      : "bg-surface text-ink-muted"
                  }`}
                >
                  {acc.enabled ? "ON" : "OFF"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-[10px] text-ink-muted">
        Конфиг: <code>config/trading_accounts.json</code>. API ключи — в env vars.
      </p>
    </div>
  );
}
