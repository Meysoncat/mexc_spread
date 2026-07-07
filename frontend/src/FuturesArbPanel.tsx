import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeftRight, BarChart3, Pause, Play, X } from "lucide-react";
import { apiUrl } from "./config";

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return "—";
  return n.toFixed(d);
}
function fmtTime(s: number): string {
  if (s < 60) return `${s.toFixed(0)}с`;
  if (s < 3600) return `${(s / 60).toFixed(1)}м`;
  return `${(s / 3600).toFixed(1)}ч`;
}

type Tab = "dashboard" | "positions" | "history" | "chart" | "settings";

interface BasisRow {
  symbol: string;
  exchange_combo: string;
  spot_mid: number;
  futures_mid: number;
  basis_bps: number;
  executable_basis_cc_bps: number;
  executable_basis_rcc_bps: number;
  funding_rate: number | null;
  estimated_apy: number;
  status: string;
}

interface Position {
  id: string;
  symbol: string;
  exchange_combo: string;
  strategy: string;
  state: string;
  entry_basis_bps: number;
  notional_usdt: number;
  basis_pnl: number;
  cumulative_funding: number;
  total_pnl: number;
  open_time_ms: number;
  close_time_ms: number;
  close_reason: string;
  exit_basis_bps: number;
  margin_ratio: number;
}

export function FuturesArbPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [status, setStatus] = useState<any>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<Position[]>([]);
  const [sortKey, setSortKey] = useState<string>("basis_bps");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);
  const pollRef = useRef<number>(0);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/futures-arb/status"));
      if (r.ok) { const d = await r.json(); if (d.ok) setStatus(d); }
    } catch {}
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/futures-arb/positions"));
      if (r.ok) { const d = await r.json(); if (d.ok) setPositions(d.positions ?? []); }
    } catch {}
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/futures-arb/history?limit=50"));
      if (r.ok) { const d = await r.json(); if (d.ok) setHistory(d.positions ?? []); }
    } catch {}
  }, []);

  useEffect(() => {
    if (!open) return;
    fetchStatus(); fetchPositions();
    pollRef.current = window.setInterval(() => {
      fetchStatus(); fetchPositions();
      if (tab === "history") fetchHistory();
    }, 3000);
    return () => window.clearInterval(pollRef.current);
  }, [open, tab, fetchStatus, fetchPositions, fetchHistory]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const doStart = () => fetch(apiUrl("/api/futures-arb/start"), { method: "POST" }).then(fetchStatus);
  const doStop = () => fetch(apiUrl("/api/futures-arb/stop"), { method: "POST" }).then(fetchStatus);
  const doClosePosition = (id: string) => {
    fetch(apiUrl("/api/futures-arb/close-position"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position_id: id }),
    }).then(fetchPositions);
  };

  if (!open) return null;

  const basisRows: BasisRow[] = status?.current_basis ?? [];
  const sorted = [...basisRows].sort((a: any, b: any) => {
    const av = a[sortKey] ?? 0;
    const bv = b[sortKey] ?? 0;
    return (av - bv) * sortDir;
  });

  const entryThreshold = status?.settings?.entry_threshold_bps ?? 30;
  const running = status?.running ?? false;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-gray-900 rounded-xl shadow-2xl w-[95vw] max-w-5xl max-h-[90vh] overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <div className="flex items-center gap-2">
            <ArrowLeftRight className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Futures/Spot Arbitrage</h2>
            <span className={`ml-2 px-2 py-0.5 rounded text-xs font-medium ${running ? "bg-green-900 text-green-300" : "bg-gray-700 text-gray-400"}`}>
              {running ? "Running" : "Stopped"}
            </span>
            <span className="ml-1 px-2 py-0.5 rounded text-xs bg-gray-700 text-gray-300">
              {status?.mode ?? "paper"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {!running && <button onClick={doStart} className="p-1.5 rounded hover:bg-gray-700 text-green-400"><Play className="w-4 h-4" /></button>}
            {running && <button onClick={doStop} className="p-1.5 rounded hover:bg-gray-700 text-yellow-400"><Pause className="w-4 h-4" /></button>}
            <button onClick={onClose} className="p-1.5 rounded hover:bg-gray-700 text-gray-400"><X className="w-4 h-4" /></button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-700 px-5">
          {(["dashboard", "positions", "history", "chart", "settings"] as Tab[]).map(t => (
            <button key={t} onClick={() => { setTab(t); if (t === "history") fetchHistory(); }}
              className={`px-3 py-2 text-sm font-medium border-b-2 ${tab === t ? "border-blue-400 text-blue-400" : "border-transparent text-gray-400 hover:text-gray-200"}`}>
              {t === "dashboard" ? "Дашборд" : t === "positions" ? "Позиции" : t === "history" ? "История" : t === "chart" ? "График" : "Настройки"}
            </button>
          ))}
        </div>

        {/* Summary bar */}
        <div className="flex gap-4 px-5 py-2 text-xs text-gray-400 border-b border-gray-800">
          <span>Позиций: <b className="text-white">{status?.open_count ?? 0}</b></span>
          <span>Exposure: <b className="text-white">{fmt(status?.total_exposure_usdt, 0)} USDT</b></span>
          <span>PNL: <b className="text-white">{fmt(status?.stats?.total_net_pnl_usdt, 2)} USDT</b></span>
          <span>Funding: <b className="text-white">{fmt(status?.stats?.total_funding_earned, 4)} USDT</b></span>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-5">
          {tab === "dashboard" && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left">
                  <th className="pb-2">Символ</th>
                  <th className="pb-2">Combo</th>
                  <th className="pb-2 cursor-pointer" onClick={() => { setSortKey("basis_bps"); setSortDir(d => d === 1 ? -1 : 1); }}>Basis bps</th>
                  <th className="pb-2 cursor-pointer" onClick={() => { setSortKey("funding_rate"); setSortDir(d => d === 1 ? -1 : 1); }}>Funding</th>
                  <th className="pb-2 cursor-pointer" onClick={() => { setSortKey("estimated_apy"); setSortDir(d => d === 1 ? -1 : 1); }}>APY %</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row, i) => {
                  const ccAbove = row.executable_basis_cc_bps >= entryThreshold;
                  const rccAbove = row.executable_basis_rcc_bps >= entryThreshold;
                  const rowClass = ccAbove ? "bg-green-900/20" : rccAbove ? "bg-red-900/20" : "";
                  return (
                    <tr key={i} className={`border-t border-gray-800 ${rowClass}`}>
                      <td className="py-1.5 text-white font-mono">{row.symbol}</td>
                      <td className="py-1.5 text-gray-300 text-xs">{row.exchange_combo}</td>
                      <td className={`py-1.5 font-mono ${row.basis_bps > 0 ? "text-green-400" : "text-red-400"}`}>{fmt(row.basis_bps, 1)}</td>
                      <td className="py-1.5 font-mono text-gray-300">{row.funding_rate != null ? (row.funding_rate * 100).toFixed(4) + "%" : "—"}</td>
                      <td className="py-1.5 font-mono text-yellow-300">{fmt(row.estimated_apy, 1)}</td>
                      <td className="py-1.5"><span className={`px-1.5 py-0.5 rounded text-xs ${row.status === "active" ? "bg-green-900 text-green-300" : "bg-gray-700 text-gray-400"}`}>{row.status}</span></td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && <tr><td colSpan={6} className="py-4 text-center text-gray-500">Нет данных</td></tr>}
              </tbody>
            </table>
          )}

          {tab === "positions" && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left">
                  <th className="pb-2">Символ</th>
                  <th className="pb-2">Стратегия</th>
                  <th className="pb-2">Entry bps</th>
                  <th className="pb-2">Basis PNL</th>
                  <th className="pb-2">Funding</th>
                  <th className="pb-2">Total PNL</th>
                  <th className="pb-2">Время</th>
                  <th className="pb-2">Margin</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {positions.map(p => {
                  const holdSec = (Date.now() - p.open_time_ms) / 1000;
                  return (
                    <tr key={p.id} className="border-t border-gray-800">
                      <td className="py-1.5 text-white font-mono">{p.symbol}</td>
                      <td className="py-1.5 text-gray-300 text-xs">{p.strategy.replace(/_/g, " ")}</td>
                      <td className="py-1.5 font-mono">{fmt(p.entry_basis_bps, 1)}</td>
                      <td className={`py-1.5 font-mono ${p.basis_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmt(p.basis_pnl, 4)}</td>
                      <td className="py-1.5 font-mono text-blue-300">{fmt(p.cumulative_funding, 4)}</td>
                      <td className={`py-1.5 font-mono font-bold ${p.total_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmt(p.total_pnl, 4)}</td>
                      <td className="py-1.5 text-gray-400">{fmtTime(holdSec)}</td>
                      <td className="py-1.5 font-mono">{(p.margin_ratio * 100).toFixed(0)}%</td>
                      <td className="py-1.5"><button onClick={() => doClosePosition(p.id)} className="px-2 py-0.5 rounded bg-red-900/50 text-red-300 text-xs hover:bg-red-800">Close</button></td>
                    </tr>
                  );
                })}
                {positions.length === 0 && <tr><td colSpan={9} className="py-4 text-center text-gray-500">Нет открытых позиций</td></tr>}
              </tbody>
            </table>
          )}

          {tab === "history" && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-left">
                  <th className="pb-2">Символ</th>
                  <th className="pb-2">Стратегия</th>
                  <th className="pb-2">Entry</th>
                  <th className="pb-2">Exit</th>
                  <th className="pb-2">Basis PNL</th>
                  <th className="pb-2">Funding</th>
                  <th className="pb-2">Net PNL</th>
                  <th className="pb-2">Время</th>
                  <th className="pb-2">Причина</th>
                </tr>
              </thead>
              <tbody>
                {history.map(p => {
                  const holdSec = p.close_time_ms > 0 ? (p.close_time_ms - p.open_time_ms) / 1000 : 0;
                  return (
                    <tr key={p.id} className="border-t border-gray-800">
                      <td className="py-1.5 text-white font-mono">{p.symbol}</td>
                      <td className="py-1.5 text-gray-300 text-xs">{p.strategy.replace(/_/g, " ")}</td>
                      <td className="py-1.5 font-mono">{fmt(p.entry_basis_bps, 1)}</td>
                      <td className="py-1.5 font-mono">{fmt(p.exit_basis_bps, 1)}</td>
                      <td className={`py-1.5 font-mono ${p.basis_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmt(p.basis_pnl, 4)}</td>
                      <td className="py-1.5 font-mono text-blue-300">{fmt(p.cumulative_funding, 4)}</td>
                      <td className={`py-1.5 font-mono font-bold ${p.total_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmt(p.total_pnl, 4)}</td>
                      <td className="py-1.5 text-gray-400">{fmtTime(holdSec)}</td>
                      <td className="py-1.5 text-gray-300 text-xs">{p.close_reason}</td>
                    </tr>
                  );
                })}
                {history.length === 0 && <tr><td colSpan={9} className="py-4 text-center text-gray-500">Нет закрытых сделок</td></tr>}
              </tbody>
            </table>
          )}

          {tab === "chart" && (
            <div className="text-center text-gray-500 py-8">
              <BarChart3 className="w-12 h-12 mx-auto mb-2 text-gray-600" />
              <p>График базиса — см. BasisChart.tsx</p>
            </div>
          )}

          {tab === "settings" && status?.settings && (
            <div className="grid grid-cols-2 gap-4 text-sm">
              {Object.entries(status.settings).map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-gray-800 py-1">
                  <span className="text-gray-400">{k}</span>
                  <span className="text-white font-mono">{String(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
