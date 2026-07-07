"""
HTX (Huobi) integration — публичный клиент.

HTX — CEX биржа с поддержкой spot и futures (linear swap).
Публичный API без аутентификации для market data.
Spot Base URL: https://api.huobi.pro
Futures Base URL: https://api.hbdm.com
"""

from mexc_monitor.htx.client import (
    HtxPublicClient,
    HtxApiError,
    HtxBookTicker,
    htx_snapshot_rows,
)

__all__ = [
    "HtxPublicClient",
    "HtxApiError",
    "HtxBookTicker",
    "htx_snapshot_rows",
]
