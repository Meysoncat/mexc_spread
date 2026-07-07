"""
Bitget integration — публичный клиент.

Bitget — CEX биржа деривативов (USDT-FUTURES).
Публичный API v2 без аутентификации для market data.
Base URL: https://api.bitget.com
"""

from mexc_monitor.bitget.client import (
    BitgetPublicClient,
    BitgetApiError,
    BitgetBookTicker,
    bitget_snapshot_rows,
)

__all__ = [
    "BitgetPublicClient",
    "BitgetApiError",
    "BitgetBookTicker",
    "bitget_snapshot_rows",
]
