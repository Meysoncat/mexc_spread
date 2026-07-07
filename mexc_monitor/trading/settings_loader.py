"""Multi-exchange trading settings loader with MEXC fallback.

Loads trading settings for any supported exchange using the convention:
1. Check {EXCHANGE}_TRADING_{SETTING} env var (e.g. BINANCE_TRADING_SYMBOL)
2. If not set, fall back to MEXC_TRADING_{SETTING} env var
3. If neither set, use hardcoded default
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar

from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS
from mexc_monitor.trading.exchanges import Exchange, Market

TradingMode = Literal["paper", "live"]

T = TypeVar("T")


def _parse_bool(value: str) -> bool:
    """Parse a string value to boolean."""
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class TradingSettings:
    """Trading settings for a specific exchange+market engine instance."""

    enabled: bool = False
    mode: TradingMode = "paper"
    symbol: str = "BTCUSDT"
    order_type: str = "LIMIT"
    order_side: str = "BUY"
    min_net_spread_bps: float = -2.0
    order_quote_notional: float = 25.0
    limit_price_offset_bps: float = 0.0
    loop_interval_sec: float = 3.0
    max_orders_per_day: int = 20
    max_open_orders: int = 3
    max_consecutive_errors: int = 5
    kill_switch: bool = True
    api_key: str = ""
    api_secret: str = ""
    recv_window_ms: int = 5_000
    events_log_path: str = "data/trading_events.jsonl"


def _env_with_fallback(
    prefix: str,
    name: str,
    default: T,
    parser: Callable[[str], T] = str,  # type: ignore[assignment]
) -> T:
    """Read env var with exchange-specific prefix, falling back to MEXC prefix.

    Resolution order:
    1. {prefix}_{name} (e.g. BINANCE_TRADING_SYMBOL)
    2. MEXC_TRADING_{name} (fallback)
    3. default value
    """
    # Try exchange-specific first
    val = os.environ.get(f"{prefix}_{name}")
    if val is not None and val.strip():
        return parser(val.strip())
    # Fallback to MEXC_TRADING_ prefix
    val = os.environ.get(f"MEXC_TRADING_{name}")
    if val is not None and val.strip():
        return parser(val.strip())
    return default


def load_trading_settings_for_exchange(
    exchange: Exchange, market: Market
) -> TradingSettings:
    """Load trading settings with exchange-specific prefix, falling back to MEXC defaults.

    For each setting, checks:
    1. {EXCHANGE}_TRADING_{SETTING} env var (e.g. BINANCE_TRADING_SYMBOL)
    2. MEXC_TRADING_{SETTING} env var (fallback)
    3. Hardcoded default

    Uses EXCHANGE_CONFIGS[exchange].env_prefix to determine the prefix.
    """
    config = EXCHANGE_CONFIGS[exchange]
    prefix = f"{config.env_prefix}_TRADING"

    def _env(name: str, default: T, parser: Callable[[str], T] = str) -> T:  # type: ignore[assignment]
        return _env_with_fallback(prefix, name, default, parser)

    return TradingSettings(
        enabled=_env("ENABLED", False, _parse_bool),
        mode=_env("MODE", "paper"),
        symbol=_env("SYMBOL", "BTCUSDT", str.upper),
        order_type=_env("ORDER_TYPE", "LIMIT", str.upper),
        order_side=_env("ORDER_SIDE", "BUY", str.upper),
        min_net_spread_bps=_env("MIN_NET_SPREAD_BPS", -2.0, float),
        order_quote_notional=_env("ORDER_QUOTE_NOTIONAL", 25.0, float),
        limit_price_offset_bps=_env("LIMIT_PRICE_OFFSET_BPS", 0.0, float),
        loop_interval_sec=_env("LOOP_INTERVAL_SEC", 3.0, float),
        max_orders_per_day=_env("MAX_ORDERS_PER_DAY", 20, int),
        max_open_orders=_env("MAX_OPEN_ORDERS", 3, int),
        max_consecutive_errors=_env("MAX_CONSECUTIVE_ERRORS", 5, int),
        kill_switch=_env("KILL_SWITCH", True, _parse_bool),
        # Credentials loaded separately via client factory
        api_key="",
        api_secret="",
        recv_window_ms=5000,
        events_log_path=_env(
            "EVENTS_LOG_PATH",
            f"data/trading_events_{exchange.value}_{market.value}.jsonl",
        ),
    )
