"""Exchange enums and composite key for multi-exchange trading engine registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Exchange(str, Enum):
    """Supported cryptocurrency exchanges."""

    MEXC = "mexc"
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    GATEIO = "gateio"
    HTX = "htx"
    BITGET = "bitget"


class Market(str, Enum):
    """Market type for trading."""

    SPOT = "spot"
    FUTURES = "futures"


class OrderType(str, Enum):
    """Order type for trade execution."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderSide(str, Enum):
    """Order side (direction)."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class EngineKey:
    """Composite key for engine registry lookup."""

    exchange: Exchange
    market: Market
    account_id: str = "default"

    def __str__(self) -> str:
        if self.account_id != "default":
            return f"{self.exchange.value}/{self.market.value}/{self.account_id}"
        return f"{self.exchange.value}/{self.market.value}"
        return f"{self.exchange.value}:{self.market.value}"
