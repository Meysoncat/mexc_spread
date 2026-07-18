"""Background poller for MetaScalp data.

Runs in a daemon thread, periodically fetches account data (balance,
orders, positions, signal levels) from MetaScalp and updates the cache.
Orderbook and cluster are fetched on-demand only (too heavy for polling).

Usage:
    from mexc_monitor.metascalp.poller import MetaScalpPoller
    poller = MetaScalpPoller(cache, client)
    poller.start()
    ...
    poller.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .cache import MetaScalpCache
from .client import MetaScalpClient

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 5.0
_MIN_INTERVAL_SEC = 1.0


class MetaScalpPoller:
    """Polls MetaScalp REST API and updates the shared cache."""

    def __init__(
        self,
        cache: MetaScalpCache,
        client: MetaScalpClient | None = None,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._cache = cache
        self._client = client or MetaScalpClient()
        self._interval = max(_MIN_INTERVAL_SEC, float(interval_sec))
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread (idempotent)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="metascalp-poller",
        )
        self._thread.start()
        logger.info("MetaScalpPoller started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        """Stop the poller (idempotent)."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._thread = None
        logger.info("MetaScalpPoller stopped")

    def is_running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Poll connections periodically."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("MetaScalpPoller tick failed")

            # Sleep with early wake on stop
            if self._stop_event.wait(timeout=self._interval):
                break

    def _tick(self) -> None:
        """Single polling tick: discover connections, fetch data."""
        # Discover connections via cached client
        conns = self._client.connections()
        if not conns:
            logger.debug("No MetaScalp connections to poll")
            return

        for conn in conns:
            if self._stop_event.is_set():
                break
            self._poll_connection(conn.id)

    def _poll_connection(self, conn_id: str) -> None:
        """Fetch and cache all lightweight data for a connection."""
        try:
            balances = self._client.balance(conn_id)
            if balances:
                self._cache.set_balance(
                    conn_id, [b.__dict__ for b in balances]
                )
        except Exception:
            logger.debug("Poller balance error for %s", conn_id)

        try:
            orders = self._client.orders(conn_id)
            if orders is not None:
                self._cache.set_orders(
                    conn_id, [o.__dict__ for o in orders]
                )
        except Exception:
            logger.debug("Poller orders error for %s", conn_id)

        try:
            positions = self._client.positions(conn_id)
            if positions is not None:
                self._cache.set_positions(
                    conn_id, [p.__dict__ for p in positions]
                )
        except Exception:
            logger.debug("Poller positions error for %s", conn_id)

        # Signal levels: poll for common tickers only (no ticker = all)
        # We don't poll per-ticker here; WS bridge handles real-time updates.

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "interval_sec": self._interval,
            "cache_snapshot": self._cache.snapshot(),
        }
