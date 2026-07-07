"""
Модели данных для Cross-Exchange Arbitrage Engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ArbMode = Literal["paper", "live"]
ArbPositionState = Literal["pending_open", "open", "pending_close", "closed"]


@dataclass
class ArbitrageSettings:
    """Настройки арбитражного движка."""
    enabled: bool = False
    mode: ArbMode = "paper"
    use_real_orders: bool = False
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    entry_threshold_bps: float = 15.0
    exit_threshold_bps: float = 3.0
    max_position_notional_usdt: float = 500.0
    max_concurrent_trades: int = 3
    max_pending_sec: float = 10.0
    max_hold_sec: float = 600.0
    kill_switch: bool = True
    loop_interval_sec: float = 0.5
    # Комиссии
    mexc_taker_fee_bps: float = 1.0
    aster_taker_fee_bps: float = 2.0
    # Freshness: максимальный возраст тика (мс). Тики старше — игнорируются.
    max_tick_age_ms: float = 5000.0
    # Persistence
    state_file: str = "data/arbitrage_state.json"


@dataclass
class ArbPosition:
    """Открытая арбитражная позиция."""
    symbol: str
    state: ArbPositionState = "pending_open"
    buy_exchange: str = ""  # "mexc" | "asterdex"
    sell_exchange: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    qty: float = 0.0
    notional_usdt: float = 0.0
    open_time_ms: int = 0
    entry_basis_bps: float = 0.0
    # Leg fill tracking (for one-leg protection)
    buy_leg_filled: bool = False
    sell_leg_filled: bool = False
    pending_since_ms: int = 0
    # Order ticket IDs (for live mode)
    buy_ticket_id: str = ""
    sell_ticket_id: str = ""
    # Close
    close_time_ms: int = 0
    buy_close_price: float = 0.0
    sell_close_price: float = 0.0
    exit_basis_bps: float = 0.0
    # PNL
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    close_reason: str = ""


@dataclass
class ArbTradeRecord:
    """Запись о завершённой арбитражной сделке."""
    symbol: str
    mode: ArbMode
    buy_exchange: str
    sell_exchange: str
    buy_entry_price: float
    sell_entry_price: float
    buy_exit_price: float
    sell_exit_price: float
    qty: float
    notional_usdt: float
    entry_basis_bps: float
    exit_basis_bps: float
    open_time_iso: str
    close_time_iso: str
    hold_sec: float
    gross_pnl_usdt: float
    total_fees_usdt: float
    net_pnl_usdt: float
    net_pnl_bps: float
    close_reason: str  # "spread_converged" | "timeout" | "kill_switch" | "manual"


@dataclass
class ArbStats:
    """Статистика арбитража."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_gross_pnl_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    avg_hold_sec: float = 0.0
    avg_net_pnl_bps: float = 0.0
    max_pnl_usdt: float = 0.0
    min_pnl_usdt: float = 0.0
