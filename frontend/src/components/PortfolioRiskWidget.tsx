import { useState, useEffect, useCallback } from "react";
import { ShieldAlert, ShieldCheck, AlertTriangle } from "lucide-react";
import type { PortfolioRiskStatus } from "../types";
import { SkeletonPill } from "./ui/Skeleton";

const POLL_INTERVAL_SEC = 10;

export function PortfolioRiskWidget() {
  const [status, setStatus] = useState<PortfolioRiskStatus | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/portfolio-risk/status");
      if (!r.ok) return;
      const data: PortfolioRiskStatus = await r.json();
      setStatus(data);
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, POLL_INTERVAL_SEC * 1000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  if (!status) return <SkeletonPill className="h-[26px] w-24" />;

  const hasCritical = status.alerts.some((a) => a.level === "critical");
  const hasWarning = status.alerts.some((a) => a.level === "warning");

  const icon = status.kill_switch_active ? (
    <ShieldAlert className="h-4 w-4 text-red-500" />
  ) : hasCritical ? (
    <ShieldAlert className="h-4 w-4 text-red-400" />
  ) : hasWarning ? (
    <AlertTriangle className="h-4 w-4 text-yellow-400" />
  ) : (
    <ShieldCheck className="h-4 w-4 text-green-500" />
  );

  const bgColor = status.kill_switch_active
    ? "bg-red-900/20 border-red-700"
    : hasCritical
    ? "bg-red-900/10 border-red-700/50"
    : hasWarning
    ? "bg-yellow-900/10 border-yellow-700/50"
    : "bg-green-900/10 border-green-700/30";

  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs ${bgColor}`}
      title={
        status.alerts.length > 0
          ? status.alerts.map((a) => a.type).join(", ")
          : "All clear"
      }
    >
      {icon}
      <span className="font-mono text-ink-muted">
        {status.kill_switch_active
          ? "KILL"
          : `${status.total_exposure_usdt.toFixed(0)}$`}
      </span>
      {status.daily_drawdown_usdt > 0 && (
        <span className="text-red-400">
          -{status.daily_drawdown_usdt.toFixed(1)}$
        </span>
      )}
      {status.engine_count > 0 && (
        <span className="text-ink-muted/60">
          {status.engine_count}eng
        </span>
      )}
    </div>
  );
}