"""
Модели данных для Futures/Spot Arbitrage Engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# --- Type aliases ---

FuturesArbMode = Literal["paper", "live"]
FuturesArbPositionState = Literal["pending_open", "open", "pending_close", "closed"]
FuturesArbStrategy = Literal["cash_and_carry", "reverse_cash_and_carry", "funding_arb"]
SpotSide = Literal["buy", "sell"]
FuturesSide = Literal["long", "short"]
BasisStatus = Literal["active", "stale"]
RiskAlertLevel = Literal["warning", "critical"]


# --- Configuration ---


@dataclass
class FuturesArbSettings:
    """Настройки движка Futures/Spot Arbitrage."""

    enabled: bool = False
    mode: FuturesArbMode = "paper"

    # Symbols & combos
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    exchange_combos: list[str] = field(
        default_factory=lambda: ["mexc_spot+mexc_futures"]
    )

    # Entry/exit thresholds
    entry_threshold_bps: float = 30.0
    exit_threshold_bps: float = 5.0
    max_basis_divergence_bps: float = 100.0
    target_profit_bps: float = 50.0

    # Funding
    funding_entry_threshold: float = 0.001  # 0.1%
    funding_consecutive_periods_exit: int = 3

    # Position sizing
    position_notional_usdt: float = 1000.0
    max_concurrent_positions: int = 5
    max_per_symbol_notional_usdt: float = 3000.0
    max_total_exposure_usdt: float = 10000.0
    futures_leverage: int = 3

    # Timing
    max_hold_duration_hours: float = 168.0  # 7 days
    max_leg_pending_sec: float = 30.0
    loop_interval_sec: float = 1.0
    expected_hold_hours: float = 24.0

    # Risk
    margin_warning_threshold: float = 0.5  # 50%
    margin_critical_threshold: float = 0.3  # 30%
    max_delta_imbalance_percent: float = 5.0
    critical_delta_imbalance_percent: float = 15.0
    kill_switch: bool = False

    # Fees (bps, one-way)
    spot_taker_fee_bps: float = 1.0
    futures_taker_fee_bps: float = 2.0

    # History
    basis_history_interval_sec: float = 60.0
    basis_history_retention_days: int = 90

    # Alerts
    funding_alert_threshold: float = 0.005  # 0.5%


# --- Position ---


@dataclass
class FuturesArbPosition:
    """Арбитражная позиция (спот + фьючерс)."""

    id: str
    symbol: str
    exchange_combo: str
    strategy: FuturesArbStrategy
    state: FuturesArbPositionState

    # Spot leg
    spot_side: SpotSide
    spot_entry_price: float
    spot_qty: float

    # Futures leg
    futures_side: FuturesSide
    futures_entry_price: float
    futures_qty: float
    futures_leverage: int

    # Tracking
    notional_usdt: float
    entry_basis_bps: float
    open_time_ms: int

    # PNL
    basis_pnl: float = 0.0
    cumulative_funding: float = 0.0
    entry_fees: float = 0.0
    exit_fees: float = 0.0
    total_pnl: float = 0.0

    # Close
    close_time_ms: int = 0
    close_reason: str = ""
    exit_basis_bps: float = 0.0

    # Risk
    margin_ratio: float = 1.0


# --- Trade Record ---


@dataclass
class FuturesArbTradeRecord:
    """Запись о завершённой арбитражной сделке (закрытая позиция)."""

    id: str
    symbol: str
    exchange_combo: str
    strategy: FuturesArbStrategy
    mode: FuturesArbMode

    # Spot leg
    spot_side: SpotSide
    spot_entry_price: float
    spot_exit_price: float

    # Futures leg
    futures_side: FuturesSide
    futures_entry_price: float
    futures_exit_price: float

    # Sizing
    qty: float
    notional_usdt: float
    futures_leverage: int

    # Basis
    entry_basis_bps: float
    exit_basis_bps: float

    # PNL breakdown
    basis_pnl: float
    funding_earned: float
    fees_spot_leg: float
    fees_futures_leg: float
    net_pnl: float
    net_pnl_bps: float
    annualized_return: float

    # Timing
    hold_duration_sec: float
    open_time_iso: str
    close_time_iso: str
    close_reason: str


# --- Statistics ---


@dataclass
class FuturesArbStats:
    """Агрегированная статистика Futures/Spot Arbitrage."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_net_pnl_usdt: float = 0.0
    total_funding_earned: float = 0.0
    total_fees_usdt: float = 0.0
    avg_hold_duration_sec: float = 0.0
    avg_net_pnl_bps: float = 0.0
    win_rate: float = 0.0
    max_pnl_usdt: float = 0.0
    min_pnl_usdt: float = 0.0


# --- Basis Snapshot ---


@dataclass
class BasisSnapshot:
    """Снимок базиса между спотом и фьючерсом в конкретный момент."""

    symbol: str
    exchange_combo: str  # "mexc_spot+mexc_futures" | "mexc_spot+asterdex_perp" | "asterdex_perp+mexc_futures"
    spot_mid: float
    futures_mid: float
    basis_abs: float  # futures_mid - spot_mid
    basis_bps: float  # 10000 * basis_abs / spot_mid
    executable_basis_cc_bps: float  # cash-and-carry: (futures_bid - spot_ask) / spot_mid * 10000 - fees
    executable_basis_rcc_bps: float  # reverse: (spot_bid - futures_ask) / spot_mid * 10000 - fees
    estimated_apy: float  # annualized yield
    funding_rate: float | None
    status: BasisStatus
    timestamp_ms: int


# --- Funding Info ---


@dataclass
class FundingInfo:
    """Информация о funding rate перпетуального контракта."""

    symbol: str
    exchange: str  # "mexc_futures" | "asterdex_perp"
    current_rate: float
    next_funding_time_ms: int
    avg_7d: float
    avg_30d: float
    annualized_yield: float
    direction_changed: bool
    std_30d: float = 0.0  # Standard deviation of funding rate over 30d
    z_score: float = 0.0  # Z-score of current rate vs 30d distribution


# --- Risk Alert ---


@dataclass
class RiskAlert:
    """Алерт контроля рисков."""

    level: RiskAlertLevel
    alert_type: str  # "margin_warning", "margin_critical", "delta_imbalance", etc.
    symbol: str
    message: str
    timestamp_ms: int
