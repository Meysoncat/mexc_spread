export type Exchange =
  | "mexc"
  | "asterdex"
  | "lighter"
  | "binance"
  | "bybit"
  | "okx"
  | "gateio"
  | "htx"
  | "bitget"
  | "dydx"
  | "hyperliquid";

export type Market = "spot" | "futures" | "cross";

/** Рынок для REST стакана (в режиме «Базис» выбирается нога). */
export type DomMarket = "spot" | "futures";

export type ChartInterval = "5m" | "15m" | "1h" | "4h" | "1d";

export type ChartVisualType = "candle" | "line";

export interface OrderbookLevel {
  price: number;
  qty: number;
  notional: number;
}

export interface VwapSummary {
  vwap_buy_price: number | null;
  vwap_sell_price: number | null;
  slippage_buy_bps: number | null;
  slippage_sell_bps: number | null;
  executable_buy_notional: number;
  executable_sell_notional: number;
  depth_levels: number;
}

export interface DepthResponse {
  ok: boolean;
  error?: string;
  market: DomMarket;
  symbol: string;
  limit?: number;
  bids: OrderbookLevel[];
  asks: OrderbookLevel[];
  best_bid?: number | null;
  best_ask?: number | null;
  mid?: number | null;
  last_update_id?: number | null;
  version?: number | null;
  timestamp_ms?: number | null;
  cache_hit?: boolean;
  vwap?: VwapSummary;
}

export interface KlineCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface KlinesResponse {
  ok: boolean;
  error?: string;
  market: "spot" | "futures";
  symbol: string;
  interval: string;
  count?: number;
  candles: KlineCandle[];
}

export interface MarketRow {
  symbol: string;
  bid: number;
  ask: number;
  bid_qty: number;
  ask_qty: number;
  mid: number;
  spread_abs: number;
  spread_bps: number | null;
  volume_24h_base: number;
  volume_24h_quote: number;
  funding_rate: number | null;
  /** ISO8601 UTC, одна метка на весь снимок */
  observed_at?: string | null;
  fee_round_trip_bps?: number;
  net_spread_bps?: number | null;
  l1_max_executable_base?: number;
  l1_max_notional_quote?: number;
  reference_quote_notional?: number | null;
  l1_covers_reference_notional?: boolean | null;
  // L2 depth (VWAP-based)
  vwap_buy_price?: number | null;
  vwap_sell_price?: number | null;
  slippage_buy_bps?: number | null;
  slippage_sell_bps?: number | null;
  executable_buy_notional?: number;
  executable_sell_notional?: number;
  depth_levels?: number;
}

/** Спот ↔ USDT-M perp: базис по mid (fut − spot). */
export interface CrossMarketRow {
  symbol_spot: string;
  symbol_futures: string;
  spot_bid: number;
  spot_ask: number;
  spot_mid: number;
  spot_spread_bps: number | null;
  fut_bid: number;
  fut_ask: number;
  fut_mid: number;
  fut_spread_bps: number | null;
  basis_mid_abs: number;
  basis_mid_bps: number | null;
  funding_rate: number | null;
  volume_24h_base_spot: number;
  volume_24h_quote_spot: number;
  volume_24h_base_fut: number;
  volume_24h_quote_fut: number;
  observed_at?: string | null;
}

export type SnapshotRow = MarketRow | CrossMarketRow;

export interface SnapshotExecutionModel {
  fee_model: string;
  spot_taker_fee_bps_one_way: number;
  futures_taker_fee_bps_one_way: number;
  reference_quote_notional: number;
  notes: string[];
}

export interface SnapshotResponse {
  ok: boolean;
  error?: string;
  market: Market;
  rows: SnapshotRow[];
  count: number;
  loaded_at?: string;
  /** Серверный TTL-кэш снимка (см. MEXC_SNAPSHOT_CACHE_TTL_SEC) */
  cache_hit?: boolean;
  execution_model?: SnapshotExecutionModel;
  exchange?: Exchange;
}

export type TradingMode = "paper" | "live";

export interface TradingState {
  running: boolean;
  mode: TradingMode;
  symbol: string;
  kill_switch: boolean;
  started_at?: string | null;
  stopped_at?: string | null;
  last_error?: string | null;
  consecutive_errors: number;
  loop_count: number;
  signals_seen: number;
  orders_submitted: number;
  open_orders: number;
  last_signal_net_spread_bps?: number | null;
  last_observed_at?: string | null;
  last_order_client_id?: string | null;
}

export interface TradingSettingsView {
  enabled: boolean;
  mode: TradingMode;
  symbol: string;
  min_net_spread_bps: number;
  order_quote_notional: number;
  limit_price_offset_bps: number;
  loop_interval_sec: number;
  max_orders_per_day: number;
  max_open_orders: number;
  max_consecutive_errors: number;
  kill_switch: boolean;
  api_key: string;
  api_secret: string;
  recv_window_ms: number;
  events_log_path: string;
}

export interface TradingStatusResponse {
  ok: boolean;
  state: TradingState;
  settings: TradingSettingsView;
}

export interface TradingEventsResponse {
  ok: boolean;
  count: number;
  rows: Record<string, unknown>[];
}


// ─── Spread Buffer / Streaming types ───────────────────────────────────────────

export interface SpreadTick {
  timestamp_ms: number;
  bid: number;
  ask: number;
  bid_qty: number;
  ask_qty: number;
  mid: number;
  spread_abs: number;
  spread_bps: number | null;
}

export interface SpreadHistoryResponse {
  ok: boolean;
  symbol: string;
  count: number;
  ticks: SpreadTick[];
}

export interface SpreadStats {
  period_sec: number;
  ticks_count: number;
  avg_spread_bps: number | null;
  min_spread_bps: number | null;
  max_spread_bps: number | null;
  std_spread_bps: number | null;
  current_spread_bps: number | null;
  current_bid: number;
  current_ask: number;
  current_mid: number;
  pct_above_threshold: number | null;
}

export interface SpreadStatsResponse {
  ok: boolean;
  symbol: string;
  stats: SpreadStats | null;
}

export interface SpreadSymbolsResponse {
  ok: boolean;
  symbols: string[];
  count: number;
}

// ─── Portfolio Risk ───────────────────────────────────────────────────────────

export interface PortfolioRiskAlert {
  level: "critical" | "warning";
  type: string;
  symbol?: string;
  value?: number;
  limit?: number;
}

export interface PortfolioRiskStatus {
  ok: boolean;
  total_exposure_usdt: number;
  engine_count: number;
  positions_by_symbol: Record<string, number>;
  daily_drawdown_usdt: number;
  kill_switch_active: boolean;
  alerts: PortfolioRiskAlert[];
  all_clear: boolean;
}

// ─── Spread Capture Settings ────────────────────────────────────────────────

export type CaptureMode = "monitor" | "paper" | "live";

export interface CaptureSettings {
  symbol: string;
  market: string;
  exchange: string;
  mode: CaptureMode;
  entry_threshold_bps: number;
  exit_threshold_bps: number;
  order_notional_usdt: number;
  max_hold_sec: number;
  max_pending_sec: number;
  taker_fee_bps: number;
  enabled: boolean;
  kill_switch: boolean;
  loop_interval_sec: number;
  max_trades_per_hour: number;
  max_tick_age_ms: number;
  fill_rate_per_sec: number;
  adverse_selection_ratio: number;
  realistic_fills: boolean;
  sound_alert: boolean;
  telegram_alert: boolean;
  state_file: string;
}

export interface CapturePosition {
  state: "idle" | "pending_buy" | "holding" | "pending_sell";
  entry_price: number;
  entry_qty: number;
  entry_time_ms: number;
  entry_spread_bps: number;
  exit_price: number;
  exit_time_ms: number;
  exit_spread_bps: number;
  pending_order_id: string;
  pending_since_ms: number;
  entry_adverse_cost: number;
}

export interface CaptureStats {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  total_pnl_usdt: number;
  total_fees_usdt: number;
  net_pnl_usdt: number;
  avg_hold_sec: number;
  avg_spread_captured_bps: number;
  max_pnl_usdt: number;
  min_pnl_usdt: number;
}

export interface CaptureStatusResponse {
  ok: boolean;
  settings: CaptureSettings;
  position: CapturePosition;
  stats: CaptureStats;
  running: boolean;
}

export interface CaptureTradeRecord {
  symbol: string;
  exchange: string;
  mode: CaptureMode;
  entry_price: number;
  exit_price: number;
  qty: number;
  entry_spread_bps: number;
  exit_spread_bps: number;
  entry_time_iso: string;
  exit_time_iso: string;
  hold_sec: number;
  gross_pnl_usdt: number;
  fees_usdt: number;
  adverse_cost_usdt: number;
  net_pnl_usdt: number;
  net_pnl_bps: number;
}

// ─── Reconciliation ──────────────────────────────────────────────────────────

export interface ReconciliationDiscrepancy {
  type: "missing_on_exchange" | "unexpected_on_exchange" | "qty_mismatch";
  symbol: string;
  message: string;
}

export interface ReconciliationResult {
  ok: boolean;
  all_clear: boolean;
  matched: number;
  discrepancies: number;
  discrepancy_details: ReconciliationDiscrepancy[];
}
