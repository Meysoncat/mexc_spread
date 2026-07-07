"""
OKX integration — публичный клиент.

OKX — CEX биржа с поддержкой spot, futures и swap.
Публичный API v5 без аутентификации для market data.
Base URL: https://www.okx.com
"""

from mexc_monitor.okx.client import (
    OkxPublicClient,
    OkxApiError,
    OkxBookTicker,
    okx_snapshot_rows,
)

__all__ = [
    "OkxPublicClient",
    "OkxApiError",
    "OkxBookTicker",
    "okx_snapshot_rows",
]
