"""
AsterDEX integration — публичный и приватный клиент.

AsterDEX — децентрализованная биржа перпетуальных фьючерсов.
API совместим с Binance Futures (HMAC SHA256, /fapi/v1/...).
Base URL: https://fapi.asterdex.com
"""

from mexc_monitor.aster.client import (
    AsterPublicClient,
    AsterApiError,
    AsterBookTicker,
    AsterTicker24h,
    aster_snapshot_rows,
)
from mexc_monitor.aster.private_client import AsterPrivateClient

__all__ = [
    "AsterPublicClient",
    "AsterPrivateClient",
    "AsterApiError",
    "AsterBookTicker",
    "AsterTicker24h",
    "aster_snapshot_rows",
]
