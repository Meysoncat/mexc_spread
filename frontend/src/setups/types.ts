/** Контракт setup/v1 — см. docs/INTEGRATION.md и GET /api/v1/schema/setup. */

export type SetupType =
  | "cross_perp"
  | "funding_arb"
  | "basis"
  | "lead_lag"
  | "breakout"
  | "momentum"
  | "funding_squeeze"
  | "btc_lag";

export type SetupCategory = "arbitrage" | "directional";

export interface SetupLeg {
  venue: string;
  market: "perp" | "spot";
  symbol: string;
  side: "buy" | "sell";
  price: number;
  price_per_coin: number;
  l1_qty: number | null;
  l1_notional_usd: number | null;
  taker_fee_bps: number;
}

export interface SetupOutcome {
  result?: "target" | "stop" | "expired";
  targets_hit?: number;
  return_pct?: number | null;
  r_multiple?: number | null;
  mfe_pct?: number;
  mae_pct?: number;
  duration_sec?: number;
  max_edge_bps?: number | null;
  edge_at_close_bps?: number | null;
}

export interface Setup {
  schema: "setup/v1";
  id: string;
  instance: string;
  type: SetupType;
  category: SetupCategory;
  status: "active" | "closed";
  base: string;
  direction: "long" | "short" | null;
  legs: SetupLeg[];
  entry: number | null;
  stop: number | null;
  targets: number[];
  risk_reward: number | null;
  edge_bps: number | null;
  score: number;
  horizon_sec: number;
  valid_until: string | null;
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  close_reason: string | null;
  data_age_ms: number;
  metrics: Record<string, number | string | boolean | null>;
  notes: string[];
  outcome?: SetupOutcome;
}

export interface SetupEvent {
  seq: number;
  event: "opened" | "updated" | "closed";
  ts: string;
  setup: Setup;
}

export const SETUP_TYPE_LABELS: Record<SetupType, string> = {
  cross_perp: "Кросс-perp",
  funding_arb: "Funding",
  basis: "Базис",
  lead_lag: "Lead-lag",
  breakout: "Пробой",
  momentum: "Импульс",
  funding_squeeze: "Funding/OI",
  btc_lag: "Отставание от BTC",
};

export const SETUP_TYPE_CATEGORY: Record<SetupType, SetupCategory> = {
  cross_perp: "arbitrage",
  funding_arb: "arbitrage",
  basis: "arbitrage",
  lead_lag: "directional",
  breakout: "directional",
  momentum: "directional",
  funding_squeeze: "directional",
  btc_lag: "directional",
};

export const ALL_SETUP_TYPES = Object.keys(SETUP_TYPE_LABELS) as SetupType[];
