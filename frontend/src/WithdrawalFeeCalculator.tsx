import { useCallback, useEffect, useState } from "react";
import { Calculator, ChevronDown, ChevronUp } from "lucide-react";
import { apiUrl, adminAuthHeaders } from "./config";

interface WithdrawalFeesConfig {
  ok: boolean;
  tokens: Record<string, {
    networks: Record<string, { fee: number; min_withdrawal: number; eta_min: number }>;
  }>;
  exchange_overrides?: Record<string, Record<string, Record<string, number>>>;
}

interface BestNetwork {
  network: string;
  total_fee_usdt: number;
  net_profit_bps: number;
  net_profit_usdt: number;
  eta_min: number;
}

export function WithdrawalFeeCalculator({
  spreadBps: externalSpreadBps,
  token: externalToken,
}: {
  spreadBps?: number;
  token?: string;
} = {}) {
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState<WithdrawalFeesConfig | null>(null);
  const [token, setToken] = useState(externalToken ?? "USDT");
  const [srcExchange, setSrcExchange] = useState("mexc");
  const [dstExchange, setDstExchange] = useState("binance");
  const [spreadBps, setSpreadBps] = useState(externalSpreadBps ?? 50);
  const [notional, setNotional] = useState(1000);
  const [result, setResult] = useState<BestNetwork[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (externalSpreadBps != null) setSpreadBps(externalSpreadBps);
  }, [externalSpreadBps]);

  useEffect(() => {
    if (externalToken) setToken(externalToken);
  }, [externalToken]);

  useEffect(() => {
    if (!open) return;
    fetch(apiUrl("/api/withdrawal-fees"))
      .then((r) => r.json())
      .then((d: WithdrawalFeesConfig) => setConfig(d))
      .catch(() => {});
  }, [open]);

  const calculate = useCallback(async () => {
    setLoading(true);
    try {
      const q = new URLSearchParams({
        token,
        src_exchange: srcExchange,
        dst_exchange: dstExchange,
        spread_bps: String(spreadBps),
        notional_usdt: String(notional),
      });
      const r = await fetch(apiUrl(`/api/withdrawal-fees/calculate?${q}`), {
        headers: adminAuthHeaders(),
      });
      const d = await r.json();
      if (d.ok && d.networks) {
        setResult(d.networks);
      } else if (d.ok && d.network) {
        setResult([{
          network: d.network,
          total_fee_usdt: d.total_fee_usdt,
          net_profit_bps: d.net_profit_bps,
          net_profit_usdt: d.net_profit_usdt,
          eta_min: d.eta_min,
        }]);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [token, srcExchange, dstExchange, spreadBps, notional]);

  const tokens = config ? Object.keys(config.tokens) : ["USDT", "BTC", "ETH", "SOL", "XRP"];
  const exchanges = ["mexc", "binance", "bybit", "okx", "gateio", "htx", "bitget"];

  return (
    <div className="rounded-xl border border-line bg-surface-elevated">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-ink transition hover:bg-accent/5"
      >
        <Calculator className="h-4 w-4 text-accent" />
        <span className="flex-1">Withdrawal Fee Calculator</span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="border-t border-line px-4 py-3 space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Токен
              <select
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
              >
                {tokens.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Откуда
              <select
                value={srcExchange}
                onChange={(e) => setSrcExchange(e.target.value)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
              >
                {exchanges.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Куда
              <select
                value={dstExchange}
                onChange={(e) => setDstExchange(e.target.value)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink"
              >
                {exchanges.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Спред (bps)
              <input
                type="number"
                value={spreadBps}
                onChange={(e) => setSpreadBps(Number(e.target.value) || 0)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-muted">
              Размер (USDT)
              <input
                type="number"
                value={notional}
                onChange={(e) => setNotional(Number(e.target.value) || 0)}
                className="rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm text-ink"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={calculate}
            disabled={loading}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:bg-accent/90 disabled:opacity-50"
          >
            {loading ? "Расчёт…" : "Рассчитать"}
          </button>
          {result && result.length > 0 && (
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-line text-ink-muted">
                    <th className="px-2 py-1.5 text-left">Сеть</th>
                    <th className="px-2 py-1.5 text-right">Комиссия (USDT)</th>
                    <th className="px-2 py-1.5 text-right">Чистый спред (bps)</th>
                    <th className="px-2 py-1.5 text-right">Прибыль (USDT)</th>
                    <th className="px-2 py-1.5 text-right">Время вывода</th>
                  </tr>
                </thead>
                <tbody>
                  {result.map((r) => (
                    <tr key={r.network} className="border-b border-line/40 hover:bg-accent/5">
                      <td className="px-2 py-1.5 font-medium">{r.network}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{r.total_fee_usdt.toFixed(4)}</td>
                      <td className={`px-2 py-1.5 text-right font-mono font-semibold ${
                        r.net_profit_bps >= 0 ? "text-emerald-500" : "text-rose-500"
                      }`}>
                        {r.net_profit_bps.toFixed(2)}
                      </td>
                      <td className={`px-2 py-1.5 text-right font-mono font-semibold ${
                        r.net_profit_usdt >= 0 ? "text-emerald-500" : "text-rose-500"
                      }`}>
                        {r.net_profit_usdt.toFixed(4)}
                      </td>
                      <td className="px-2 py-1.5 text-right text-ink-muted">{r.eta_min} min</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
