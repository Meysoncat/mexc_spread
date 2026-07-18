"""MetaScalp integration — HTTP client, models, and WebSocket client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetaScalpConnection:
    """Active exchange connection in MetaScalp."""

    id: str
    name: str
    exchange: str
    status: str  # e.g. "Connected", "Disconnected"


@dataclass
class MetaScalpBalance:
    """Balance entry."""

    coin: str
    total: float
    free: float
    locked: float


@dataclass
class MetaScalpOrder:
    """Order entry."""

    order_id: str
    ticker: str
    side: str  # "Buy" | "Sell"
    type: str  # "Limit" | "Market" | ...
    price: float
    filled_price: float
    size: float
    filled_size: float
    fee: float
    fee_currency: str
    status: str  # "New" | "Partial" | "Filled" | "Cancelled"
    time: str


@dataclass
class MetaScalpPosition:
    """Position entry."""

    position_id: str
    ticker: str
    side: str  # "Long" | "Short"
    size: float
    avg_price: float
    avg_price_fix: float
    avg_price_dyn: float
    status: str  # "Open" | "Closed"


@dataclass
class MetaScalpOrderbookLevel:
    """Single level in order book."""

    price: float
    size: float
    type: str  # "Ask" | "Bid"


@dataclass
class MetaScalpOrderbookSnapshot:
    """Full order book snapshot."""

    ticker: str
    asks: list[MetaScalpOrderbookLevel] = field(default_factory=list)
    bids: list[MetaScalpOrderbookLevel] = field(default_factory=list)
    best_ask: float = 0.0
    best_bid: float = 0.0


@dataclass
class MetaScalpSignalLevel:
    """Signal / alert level."""

    id: str
    connection_id: str
    ticker: str
    price: float
    is_triggered: bool = False
    trigger_time: str = ""
    trigger_rule: str = ""


@dataclass
class MetaScalpClusterSnapshot:
    """Cluster (volume profile) snapshot."""

    ticker: str
    rows: list[dict[str, Any]] = field(default_factory=list)
