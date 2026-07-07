"""
Lighter DEX integration — публичный клиент.

Lighter — DEX перпетуальных фьючерсов на zkSync.
Публичный API без аутентификации.
Base URL: https://mainnet.zklighter.elliot.ai
"""

from mexc_monitor.lighter.client import (
    LighterPublicClient,
    LighterApiError,
    LighterMarketInfo,
    LighterOrderbookSummary,
    LighterFundingRate,
    lighter_snapshot_rows,
)

__all__ = [
    "LighterPublicClient",
    "LighterApiError",
    "LighterMarketInfo",
    "LighterOrderbookSummary",
    "LighterFundingRate",
    "lighter_snapshot_rows",
]
