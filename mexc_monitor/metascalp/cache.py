"""Thread-safe in-memory cache with TTL for MetaScalp data.

Stores balance, orders, positions, orderbook, cluster, and signal levels
with automatic expiration. Used by both the poller and WebSocket bridge.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class MetaScalpCache:
    """TTL cache for MetaScalp data per connection.

    All operations are thread-safe. Data is stored as raw dicts/lists
    so it can be serialized directly to JSON by FastAPI.
    """

    def __init__(self, default_ttl_sec: float = 10.0) -> None:
        self._default_ttl = default_ttl_sec
        self._data: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    # ── Generic helpers ───────────────────────────────────────────────────────

    def _key(self, conn_id: str, data_type: str, ticker: str | None = None) -> str:
        """Build a unique cache key."""
        if ticker:
            return f"{conn_id}:{data_type}:{ticker}"
        return f"{conn_id}:{data_type}"

    def set(
        self,
        conn_id: str,
        data_type: str,
        value: Any,
        ticker: str | None = None,
        ttl_sec: float | None = None,
    ) -> None:
        """Store value with TTL."""
        key = self._key(conn_id, data_type, ticker)
        with self._lock:
            self._data[key] = value
            self._timestamps[key] = time.monotonic() + (ttl_sec or self._default_ttl)

    def get(
        self, conn_id: str, data_type: str, ticker: str | None = None
    ) -> Any | None:
        """Get value if not expired, else return None."""
        key = self._key(conn_id, data_type, ticker)
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None or time.monotonic() > ts:
                return None
            return self._data.get(key)

    def get_all(self, conn_id: str, data_type: str) -> dict[str, Any]:
        """Get all non-expired values for a connection and data type."""
        prefix = f"{conn_id}:{data_type}:"
        now = time.monotonic()
        with self._lock:
            result = {}
            for key, ts in list(self._timestamps.items()):
                if key.startswith(prefix) and now <= ts:
                    ticker = key[len(prefix) :]
                    result[ticker] = self._data[key]
            return result

    def invalidate(self, conn_id: str | None = None, data_type: str | None = None) -> None:
        """Invalidate cache entries.

        Args:
            conn_id: If given, only invalidate for this connection.
            data_type: If given, only invalidate this data type.
        """
        with self._lock:
            if conn_id is None and data_type is None:
                self._data.clear()
                self._timestamps.clear()
                return

            prefix = ""
            if conn_id:
                prefix += f"{conn_id}:"
            if data_type:
                prefix += f"{data_type}:"

            for key in list(self._timestamps.keys()):
                if key.startswith(prefix):
                    self._data.pop(key, None)
                    self._timestamps.pop(key, None)

    def is_fresh(self, conn_id: str, data_type: str, ticker: str | None = None) -> bool:
        """Check if data is still fresh (not expired)."""
        return self.get(conn_id, data_type, ticker) is not None

    def age_sec(self, conn_id: str, data_type: str, ticker: str | None = None) -> float | None:
        """Return age of cached data in seconds, or None if not cached."""
        key = self._key(conn_id, data_type, ticker)
        with self._lock:
            ts = self._timestamps.get(key)
            if ts is None:
                return None
            ttl = ts - time.monotonic()
            return max(0.0, self._default_ttl - ttl)

    # ── Convenience typed accessors ───────────────────────────────────────────

    def set_balance(self, conn_id: str, balances: list[dict]) -> None:
        self.set(conn_id, "balance", balances)

    def get_balance(self, conn_id: str) -> list[dict] | None:
        return self.get(conn_id, "balance")

    def set_orders(self, conn_id: str, orders: list[dict]) -> None:
        self.set(conn_id, "orders", orders)

    def get_orders(self, conn_id: str) -> list[dict] | None:
        return self.get(conn_id, "orders")

    def set_positions(self, conn_id: str, positions: list[dict]) -> None:
        self.set(conn_id, "positions", positions)

    def get_positions(self, conn_id: str) -> list[dict] | None:
        return self.get(conn_id, "positions")

    def set_orderbook(self, conn_id: str, ticker: str, orderbook: dict) -> None:
        self.set(conn_id, "orderbook", orderbook, ticker=ticker, ttl_sec=2.0)

    def get_orderbook(self, conn_id: str, ticker: str) -> dict | None:
        return self.get(conn_id, "orderbook", ticker=ticker)

    def set_cluster(self, conn_id: str, ticker: str, cluster: dict) -> None:
        self.set(conn_id, "cluster", cluster, ticker=ticker, ttl_sec=5.0)

    def get_cluster(self, conn_id: str, ticker: str) -> dict | None:
        return self.get(conn_id, "cluster", ticker=ticker)

    def set_signal_levels(self, conn_id: str, ticker: str, levels: list[dict]) -> None:
        self.set(conn_id, "signal_levels", levels, ticker=ticker, ttl_sec=5.0)

    def get_signal_levels(self, conn_id: str, ticker: str) -> list[dict] | None:
        return self.get(conn_id, "signal_levels", ticker=ticker)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return full cache snapshot for diagnostics."""
        now = time.monotonic()
        with self._lock:
            return {
                "keys": len(self._data),
                "fresh": sum(1 for ts in self._timestamps.values() if now <= ts),
                "stale": sum(1 for ts in self._timestamps.values() if now > ts),
            }
