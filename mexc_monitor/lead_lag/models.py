"""Data models for the Lead-Lag Arbitrage module.

Contains all core dataclasses and enums used across the lead-lag subsystem:
price snapshots, spread calculations, lag estimates, signals, statistics,
and configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SignalDirection(str, Enum):
    """Direction of a lead-lag signal."""

    LONG = "long"    # Leader moved up, lagger should follow
    SHORT = "short"  # Leader moved down, lagger should follow


class SignalStatus(str, Enum):
    """Lifecycle status of a lead-lag signal."""

    ACTIVE = "active"       # Signal open, waiting for convergence
    RESOLVED = "resolved"   # Lagger caught up (z-score dropped below exit threshold)
    EXPIRED = "expired"     # Timeout without convergence


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceSnapshot:
    """Single price observation from one exchange.

    Represents a normalized bookTicker/ticker update with bid, ask, mid
    and a monotonic timestamp in milliseconds.
    """

    exchange: str
    symbol: str
    bid: float
    ask: float
    mid: float
    timestamp_ms: int  # Unix milliseconds (monotonic clock at receive time)


@dataclass(frozen=True)
class SpreadSnapshot:
    """Price difference between leader and lagger at a point in time.

    spread_bps = 10000 * (leader_mid - lagger_mid) / leader_mid
    """

    symbol: str
    leader_exchange: str
    lagger_exchange: str
    leader_mid: float
    lagger_mid: float
    spread_abs: float   # leader_mid - lagger_mid
    spread_bps: float   # 10000 * spread_abs / leader_mid
    timestamp_ms: int


@dataclass(frozen=True)
class LagEstimate:
    """Estimated lag between two exchanges for a symbol.

    Produced by the Lag Detector via cross-correlation of price time series.
    """

    symbol: str
    leader_exchange: str
    lagger_exchange: str
    lag_ms: float           # Estimated delay in milliseconds (>= 0)
    correlation: float      # Cross-correlation coefficient (-1.0 to 1.0)
    confidence: float       # Confidence in estimate (0.0 to 1.0)
    sample_count: int       # Number of observations used
    updated_at: str         # ISO8601 timestamp of last update
    significant: bool = False    # Whether correlation is statistically significant
    p_value: float = 1.0         # Two-tailed p-value of the correlation


@dataclass
class LeadLagSignal:
    """A detected lead-lag arbitrage opportunity.

    Lifecycle: ACTIVE -> RESOLVED (lagger caught up) or EXPIRED (timeout).
    theoretical_pnl_bps = entry_spread_bps - exit_spread_bps - 2 * fee_bps
    """

    id: str                         # UUID
    symbol: str
    leader_exchange: str
    lagger_exchange: str
    direction: SignalDirection
    z_score: float                  # Normalized spread at signal time
    entry_spread_bps: float         # Spread in bps when signal generated
    leader_mid_at_signal: float     # Leader price when signal fired
    lagger_mid_at_signal: float     # Lagger price when signal fired
    estimated_lag_ms: float         # Expected convergence time from lag detector
    status: SignalStatus = SignalStatus.ACTIVE
    created_at: str = ""            # ISO8601
    resolved_at: Optional[str] = None
    actual_lag_ms: Optional[float] = None
    exit_spread_bps: Optional[float] = None
    theoretical_pnl_bps: Optional[float] = None


@dataclass(frozen=True)
class LeadLagStats:
    """Aggregate statistics over a time window.

    win_rate = resolved signals with theoretical_pnl_bps > 0 / total resolved
    signals_per_hour = total_signals / window_hours
    """

    window_hours: int
    total_signals: int
    resolved_signals: int
    expired_signals: int
    win_rate: Optional[float]                  # None if no resolved signals
    avg_lag_ms: Optional[float]                # None if no signals
    median_lag_ms: Optional[float]             # None if no signals
    avg_theoretical_pnl_bps: Optional[float]   # None if no resolved signals
    total_theoretical_pnl_bps: float
    signals_per_hour: float
    top_symbols: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class LeadLagConfig:
    """Configuration for the lead-lag engine.

    Loaded from config/external_apis.json section "lead_lag".
    All thresholds and timing parameters are configurable.
    """

    enabled: bool = False
    leader_exchange: str = "binance"
    lagger_exchanges: list[str] = field(default_factory=lambda: ["mexc"])
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    market: str = "futures"                     # "spot" or "futures"
    z_score_entry_threshold: float = 2.0        # Signal when z > N
    z_score_exit_threshold: float = 0.5         # Resolve when z < N
    signal_timeout_sec: float = 10.0            # Expire signal after N sec
    rolling_window_sec: float = 300.0           # Window for std dev calculation
    min_spread_bps: float = 3.0                 # Minimum spread to generate signal
    lag_estimation_interval_sec: float = 30.0   # How often to re-estimate lag
    price_buffer_history_sec: float = 60.0      # How much price history to keep
    db_path: str = "data/lead_lag_signals.sqlite"
    assumed_taker_fee_bps: float = 2.0          # Per side, for theoretical PnL
    ws_urls: dict[str, str] = field(default_factory=lambda: {
        "binance_futures": "wss://fstream.binance.com/ws",
        "binance_spot": "wss://stream.binance.com:9443/ws",
        "mexc_futures": "wss://contract.mexc.com/edge",
        "mexc_spot": "wss://wbs.mexc.com/ws",
        "bybit": "wss://stream.bybit.com/v5/public/linear",
        "okx": "wss://ws.okx.com:8443/ws/v5/public",
    })
