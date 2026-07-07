"""
Gate.io integration — публичный клиент.

Gate.io — CEX биржа с поддержкой spot и futures.
Публичный API v4 без аутентификации для market data.
Spot Base URL: https://api.gateio.ws
Futures Base URL: https://api.gateio.ws
"""

from mexc_monitor.gateio.client import (
    GateioPublicClient,
    GateioApiError,
    GateioBookTicker,
    gateio_snapshot_rows,
)

__all__ = [
    "GateioPublicClient",
    "GateioApiError",
    "GateioBookTicker",
    "gateio_snapshot_rows",
]
