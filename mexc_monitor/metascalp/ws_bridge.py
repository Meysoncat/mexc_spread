"""WebSocket bridge from MetaScalp to the shared cache.

Connects to MetaScalp's local WebSocket, subscribes to connection
updates (orders, positions, balances) and signal levels, and pushes
data into the MetaScalpCache so that REST endpoints can serve fresh
responses without hammering MetaScalp HTTP API.

Usage:
    from mexc_monitor.metascalp.ws_bridge import MetaScalpWSBridge
    bridge = MetaScalpWSBridge(cache)
    bridge.start()
    bridge.subscribe_connection(conn_id)
    ...
    bridge.stop()
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .cache import MetaScalpCache
from .participant_detector import ParticipantDetector
from .ws_client import MetaScalpWebSocketClient

logger = logging.getLogger(__name__)


class MetaScalpWSBridge:
    """Bridge MetaScalp WebSocket → in-memory cache + participant detector.

    Translates incoming WS messages into cache updates.
    Thread-safe: can be started/stopped from any thread.
    """

    def __init__(
        self,
        cache: MetaScalpCache,
        participant_detector: ParticipantDetector | None = None,
    ) -> None:
        self._cache = cache
        self._participant_detector = participant_detector
        self._ws_client = MetaScalpWebSocketClient(on_message=self._on_ws_message)
        self._running = False
        self._lock = threading.Lock()
        # Тикеры, на которые подписались на trades — чтобы не дублировать подписки
        self._subscribed_trades: set[tuple[str, str]] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Connect to MetaScalp WebSocket. Returns True if connected."""
        with self._lock:
            if self._running:
                return True
            ok = self._ws_client.connect()
            if ok:
                self._running = True
                logger.info("MetaScalpWSBridge started")
            else:
                logger.warning("MetaScalpWSBridge: MetaScalp WS not available")
            return ok

    def stop(self) -> None:
        """Disconnect from MetaScalp WebSocket."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._ws_client.disconnect()
        logger.info("MetaScalpWSBridge stopped")

    def is_running(self) -> bool:
        return self._running

    # ── Subscriptions ─────────────────────────────────────────────────────────

    def subscribe_connection(self, conn_id: str) -> None:
        """Subscribe to connection updates (orders, positions, balance, finres)."""
        self._ws_client.subscribe_connection(conn_id)
        logger.debug("Subscribed to connection %s", conn_id)

    def subscribe_signal_levels(self) -> None:
        """Subscribe to signal level updates."""
        self._ws_client.subscribe_signal_levels()
        logger.debug("Subscribed to signal levels")

    def subscribe_notifications(self) -> None:
        """Subscribe to notifications."""
        self._ws_client.subscribe_notifications()
        logger.debug("Subscribed to notifications")

    def subscribe_trades(self, conn_id: str, ticker: str) -> None:
        """Subscribe to trade updates for a ticker and route them to ParticipantDetector.

        Idempotent: re-subscribing the same (conn_id, ticker) is a no-op.
        Safe to call before the WS is connected — the subscription will be
        replayed on next ``_on_open``.
        """
        key = (conn_id, ticker.strip().upper())
        with self._lock:
            if key in self._subscribed_trades:
                return
            self._subscribed_trades.add(key)
        self._ws_client.subscribe_trades(conn_id, ticker)
        logger.debug("Subscribed to trades for %s/%s", conn_id, ticker)

    def unsubscribe_trades(self, conn_id: str, ticker: str) -> None:
        """Forget a trades subscription locally (MetaScalp WS has no unsubscribe)."""
        key = (conn_id, ticker.strip().upper())
        with self._lock:
            self._subscribed_trades.discard(key)

    def subscribed_trades(self) -> list[tuple[str, str]]:
        """Snapshot of (conn_id, ticker) pairs currently subscribed for trades."""
        with self._lock:
            return list(self._subscribed_trades)

    # ── Message handler ───────────────────────────────────────────────────────

    def _on_ws_message(self, msg_type: str, data: dict[str, Any]) -> None:
        """Process incoming MetaScalp WS message and update cache."""
        try:
            handler = getattr(self, f"_handle_{msg_type.lower()}", None)
            if handler:
                handler(data)
            else:
                # Generic fallback: try to infer type from payload keys
                self._handle_generic(msg_type, data)
        except Exception:
            logger.debug("WS bridge handle error for type=%s", msg_type, exc_info=True)

    # ── Specific handlers ─────────────────────────────────────────────────────

    def _handle_order(self, data: dict[str, Any]) -> None:
        """Incoming order update: invalidate orders cache for connection."""
        conn_id = self._extract_conn_id(data)
        if conn_id:
            # We can't easily patch the cached list, so invalidate it
            # The poller will refill on next tick, or client can fetch directly
            self._cache.invalidate(conn_id, "orders")
            logger.debug("Cache invalidated: orders for %s", conn_id)

    def _handle_position(self, data: dict[str, Any]) -> None:
        """Incoming position update."""
        conn_id = self._extract_conn_id(data)
        if conn_id:
            self._cache.invalidate(conn_id, "positions")
            logger.debug("Cache invalidated: positions for %s", conn_id)

    def _handle_balance(self, data: dict[str, Any]) -> None:
        """Incoming balance update."""
        conn_id = self._extract_conn_id(data)
        if conn_id:
            self._cache.invalidate(conn_id, "balance")
            logger.debug("Cache invalidated: balance for %s", conn_id)

    def _handle_signallevel(self, data: dict[str, Any]) -> None:
        """Incoming signal level update."""
        conn_id = self._extract_conn_id(data)
        ticker = data.get("Ticker") or data.get("ticker")
        if conn_id and ticker:
            self._cache.invalidate(conn_id, "signal_levels")
            logger.debug("Cache invalidated: signal_levels for %s/%s", conn_id, ticker)

    def _handle_notification(self, data: dict[str, Any]) -> None:
        """Generic notification — log but don't cache."""
        logger.debug("MetaScalp notification: %s", data.get("Message", data))

    def _handle_trade(self, data: dict[str, Any]) -> None:
        """Incoming trade: forward to ParticipantDetector (if attached).

        Note: MetaScalp trade payloads may be a single dict or a list of dicts.
        """
        if self._participant_detector is None:
            return
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._participant_detector.on_trade_payload(item)
        elif isinstance(data, dict):
            self._participant_detector.on_trade_payload(data)

    def _handle_generic(self, msg_type: str, data: dict[str, Any]) -> None:
        """Fallback for unknown message types."""
        # Try to detect order/position/balance by payload shape
        if "OrderId" in data or "orderId" in data:
            self._handle_order(data)
        elif "PositionId" in data or "positionId" in data:
            self._handle_position(data)
        elif "Coin" in data or "coin" in data:
            self._handle_balance(data)
        else:
            logger.debug("Unhandled WS message type=%s", msg_type)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_conn_id(data: dict[str, Any]) -> str | None:
        """Extract connection ID from payload (tries multiple key variants)."""
        for key in ("ConnectionId", "connectionId", "ConnId", "conn_id", "Connection"):
            val = data.get(key)
            if val:
                return str(val)
        return None
