"""
Hyperliquid integration — публичный клиент.

Hyperliquid — DEX перпетуальных фьючерсов (L1).
Публичный API через POST /info без аутентификации.
Base URL: https://api.hyperliquid.xyz
"""

from mexc_monitor.hyperliquid.client import (
    HyperliquidPublicClient,
    HyperliquidApiError,
    HyperliquidBookTicker,
    hyperliquid_snapshot_rows,
)

__all__ = [
    "HyperliquidPublicClient",
    "HyperliquidApiError",
    "HyperliquidBookTicker",
    "hyperliquid_snapshot_rows",
]
