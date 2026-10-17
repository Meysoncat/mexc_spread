"""
AsterDEX integration — публичный клиент.

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

__all__ = [
    "AsterPublicClient",
    "AsterApiError",
    "AsterBookTicker",
    "AsterTicker24h",
    "aster_snapshot_rows",
]
