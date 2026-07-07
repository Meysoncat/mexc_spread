"""
dYdX integration — публичный клиент.

dYdX — DEX перпетуальных фьючерсов (v4, Cosmos-based).
Публичный API через indexer без аутентификации.
Base URL: https://indexer.dydx.trade
"""

from mexc_monitor.dydx.client import (
    DydxPublicClient,
    DydxApiError,
    DydxBookTicker,
    dydx_snapshot_rows,
)

__all__ = [
    "DydxPublicClient",
    "DydxApiError",
    "DydxBookTicker",
    "dydx_snapshot_rows",
]
