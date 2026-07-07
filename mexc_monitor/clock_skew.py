"""Clock skew detection between local machine and exchange servers.

Exchange APIs include a ``Date`` header in HTTP responses. By comparing the
server timestamp with the local clock, we can estimate the offset and warn
when the skew exceeds a threshold.

Significant clock skew (> 1s) can cause:

- Phantom arbitrage signals (ticks appear "fresh" but are actually old)
- Incorrect funding rate timing
- Invalid order signatures (recvWindow violations)

Usage::

    skew = ClockSkewDetector()
    skew.check_from_response("mexc", response_headers)
    if skew.get_skew_ms("mexc") > 1000:
        logger.warning("MEXC clock skew: %dms", skew.get_skew_ms("mexc"))
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SKEW_MS = 1000.0


@dataclass
class SkewEntry:
    """Clock skew measurement for one exchange."""

    exchange: str
    skew_ms: float = 0.0
    last_check_ms: int = 0
    warning_count: int = 0


class ClockSkewDetector:
    """Tracks clock offset between local machine and exchange servers.

    Thread-safe. Call :meth:`check_from_response` when processing HTTP
    responses from exchanges to update the skew estimate.
    """

    def __init__(self, max_skew_ms: float = _DEFAULT_MAX_SKEW_MS) -> None:
        self._max_skew_ms = max_skew_ms
        self._skews: dict[str, SkewEntry] = {}
        self._lock = threading.Lock()

    def check_from_response(self, exchange: str, headers: Any) -> float | None:
        """Extract ``Date`` header from HTTP response and compute skew.

        Parameters
        ----------
        exchange
            Exchange identifier (e.g. ``"mexc"``, ``"binance"``).
        headers
            HTTP response headers (dict-like with ``get`` method, or
            httpx ``Headers`` object).

        Returns
        -------
        float or None
            Skew in milliseconds (positive = local clock ahead), or None
            if the ``Date`` header is missing or unparseable.
        """
        date_str = None
        if hasattr(headers, "get"):
            date_str = headers.get("Date") or headers.get("date")
        elif isinstance(headers, dict):
            date_str = headers.get("Date") or headers.get("date")

        if not date_str:
            return None

        try:
            server_dt = parsedate_to_datetime(date_str)
            server_ms = int(server_dt.timestamp() * 1000)
        except (ValueError, TypeError, OverflowError):
            return None

        local_ms = int(time.time() * 1000)
        skew_ms = float(local_ms - server_ms)

        with self._lock:
            entry = self._skews.get(exchange)
            if entry is None:
                entry = SkewEntry(exchange=exchange)
                self._skews[exchange] = entry
            entry.skew_ms = skew_ms
            entry.last_check_ms = local_ms
            if abs(skew_ms) > self._max_skew_ms:
                entry.warning_count += 1
                logger.warning(
                    "Clock skew detected: %s offset=%+.0fms (threshold=%.0fms)",
                    exchange,
                    skew_ms,
                    self._max_skew_ms,
                )

        return skew_ms

    def get_skew_ms(self, exchange: str) -> float:
        """Return last known skew for *exchange* (0.0 if never measured)."""
        with self._lock:
            entry = self._skews.get(exchange)
            return entry.skew_ms if entry else 0.0

    def is_skewed(self, exchange: str) -> bool:
        """True if skew exceeds threshold for *exchange*."""
        return abs(self.get_skew_ms(exchange)) > self._max_skew_ms

    def get_all_skews(self) -> dict[str, float]:
        """Return ``{exchange: skew_ms}`` for all measured exchanges."""
        with self._lock:
            return {ex: e.skew_ms for ex, e in self._skews.items()}

    def get_status(self) -> list[dict[str, Any]]:
        """Return detailed status for all exchanges."""
        with self._lock:
            return [
                {
                    "exchange": e.exchange,
                    "skew_ms": e.skew_ms,
                    "last_check_ms": e.last_check_ms,
                    "warning_count": e.warning_count,
                    "skewed": abs(e.skew_ms) > self._max_skew_ms,
                }
                for e in self._skews.values()
            ]

    def adjust_timestamp(self, exchange: str, timestamp_ms: int) -> int:
        """Correct a server timestamp to local clock time.

        If local clock is ahead by 500ms, a server timestamp of T
        becomes T + 500 in local terms.

        Returns the original timestamp if no skew data is available.
        """
        skew = self.get_skew_ms(exchange)
        if skew == 0.0:
            return timestamp_ms
        return timestamp_ms + int(skew)
