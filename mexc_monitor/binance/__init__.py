"""
Binance integration — публичный клиент.

Binance — крупнейшая CEX биржа по объёму торгов.
Поддерживает spot и futures рынки.
Spot Base URL: https://api.binance.com
Futures Base URL: https://fapi.binance.com
"""

from mexc_monitor.binance.client import (
    BinancePublicClient,
    BinanceApiError,
    BinanceBookTicker,
    binance_snapshot_rows,
)

__all__ = [
    "BinancePublicClient",
    "BinanceApiError",
    "BinanceBookTicker",
    "binance_snapshot_rows",
]
