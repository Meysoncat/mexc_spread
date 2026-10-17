import type { Setup } from "./types";

export function fmtPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (a >= 1) return v.toPrecision(6).replace(/\.?0+$/, "");
  return v.toPrecision(5);
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

export function fmtNum(v: unknown, digits = 1, suffix = ""): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

export function fmtSigned(v: unknown, digits = 1, suffix = ""): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}${suffix}`;
}

export function ago(iso: string | null, now: number): string {
  if (!iso) return "—";
  const sec = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (sec < 60) return `${sec}с`;
  if (sec < 3600) return `${Math.floor(sec / 60)}м ${sec % 60}с`;
  return `${Math.floor(sec / 3600)}ч ${Math.floor((sec % 3600) / 60)}м`;
}

export function fmtDuration(sec: number | undefined): string {
  if (sec == null) return "—";
  if (sec < 60) return `${sec.toFixed(0)}с`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}м`;
  return `${(sec / 3600).toFixed(1)}ч`;
}

/** Короткая строка с главным для этого типа. */
export function summary(s: Setup): string {
  const m = s.metrics;
  switch (s.type) {
    case "cross_perp":
      return `gross ${fmtNum(m.gross_bps)} · база ${fmtSigned(m.baseline_bps)} · L1 ${fmtUsd(m.l1_notional_usd as number | null)}`;
    case "funding_arb":
      return `APR ${fmtNum(m.funding_apr_pct, 0, "%")} · окупаемость ${fmtNum(m.breakeven_h, 1, "ч")}`;
    case "basis":
      return `базис ${fmtNum(m.basis_bps)} · funding ${fmtSigned(m.funding_carry_bps)}${m.same_venue ? " · одна биржа" : ""}`;
    case "lead_lag":
      return `${m.leader} ${fmtSigned(m.leader_move_bps)} vs ${fmtSigned(m.lagger_move_bps)} bps`;
    case "breakout":
      return `объём ×${fmtNum(m.volume_ratio)} · уровень ${fmtPrice(m.level as number)} · диапазон ${fmtNum(m.range_pct, 1, "%")}`;
    case "momentum":
      return `${m.bias === "reversion" ? "откат" : "продолжение"} ${fmtSigned(m.change_pct, 1, "%")} · объём ×${fmtNum(m.volume_ratio)}`;
    case "funding_squeeze":
      return `funding ${fmtSigned(m.funding_8h_pct, 3, "%")}/8ч · OI ${fmtSigned(m.oi_change_pct, 1, "%")}`;
    case "btc_lag":
      return `BTC ${fmtSigned(m.btc_move_pct, 2, "%")} · отстаёт ${fmtNum(m.shortfall_pct, 2, "%")} · β ${fmtNum(m.beta, 2)}`;
    default:
      return "";
  }
}

export const CLOSE_REASON_LABELS: Record<string, string> = {
  condition_gone: "условие ушло",
  stop_hit: "стоп",
  target_hit: "цель",
  expired: "истёк срок",
};
