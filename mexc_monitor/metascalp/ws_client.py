"""MetaScalp WebSocket client for real-time data.

Connects to ws://127.0.0.1:{port}/ and subscribes to:
- connection updates (orders, positions, balances, finres)
- market data (trades, orderbook, mark price, funding)
- notifications
- signal levels

Message envelope: {"Type": "...", "Data": {...}}
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import websocket

from .client import METASCALP_PORTS, loopback_port_is_open

logger = logging.getLogger(__name__)


class MetaScalpWebSocketClient:
    """WebSocket client that auto-discovers MetaScalp port and handles subscriptions."""

    def __init__(self, on_message: Callable[[str, dict[str, Any]], None] | None = None):
        self._on_message = on_message
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._base_url: str | None = None
        self._subscriptions: set[str] = set()
        self._lock = threading.Lock()
        self._running = False

    def _discover_port(self) -> str | None:
        for port in METASCALP_PORTS:
            # Cheap TCP probe first: a full WS handshake per closed port cost
            # up to 2s each (22s for the whole scan) when MetaScalp is down.
            if not loopback_port_is_open(port):
                continue
            url = f"ws://127.0.0.1:{port}"
            try:
                ws = websocket.create_connection(url, timeout=2)
                ws.close()
                return url
            except Exception:
                continue
        return None

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("MetaScalp WebSocket connected: %s", self._base_url)
        with self._lock:
            for sub in self._subscriptions:
                try:
                    ws.send(sub)
                except Exception:
                    pass

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            data = json.loads(message)
            msg_type = data.get("Type", data.get("type", ""))
            payload = data.get("Data", data.get("data", {}))
            if self._on_message:
                self._on_message(msg_type, payload)
        except Exception:
            logger.debug("MetaScalp WS parse error: %s", message[:200])

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.warning("MetaScalp WebSocket error: %s", error)

    def _on_close(self, ws: websocket.WebSocketApp, *args: Any) -> None:
        logger.info("MetaScalp WebSocket closed")

    def connect(self) -> bool:
        """Discover MetaScalp and start WebSocket connection."""
        if self._running:
            return True
        url = self._discover_port()
        if not url:
            logger.warning("MetaScalp not found for WebSocket")
            return False
        self._base_url = url
        self._running = True
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(target=self._ws.run_forever, name="metascalp-ws", daemon=True)
        self._thread.start()
        return True

    def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def subscribe_connection(self, conn_id: str) -> None:
        """Subscribe to connection-level updates (orders, positions, balance, finres)."""
        msg = json.dumps({"Type": "subscribe", "Data": {"ConnectionId": conn_id}})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_trades(self, conn_id: str, ticker: str) -> None:
        """Subscribe to trade updates."""
        msg = json.dumps({"Type": "subscribe_trades", "Data": {"ConnectionId": conn_id, "Ticker": ticker}})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_orderbook(self, conn_id: str, ticker: str, zoom_index: int = 0, depth_levels: int = 0, depth_percent: float = 0.0) -> None:
        """Subscribe to orderbook updates."""
        data: dict[str, Any] = {"ConnectionId": conn_id, "Ticker": ticker}
        if zoom_index:
            data["ZoomIndex"] = zoom_index
        if depth_levels:
            data["DepthLevels"] = depth_levels
        if depth_percent:
            data["DepthPercent"] = depth_percent
        msg = json.dumps({"Type": "subscribe_orderbook", "Data": data})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_mark_price(self, conn_id: str, ticker: str) -> None:
        """Subscribe to mark price updates (futures only)."""
        msg = json.dumps({"Type": "subscribe_mark_price", "Data": {"ConnectionId": conn_id, "Ticker": ticker}})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_funding(self, conn_id: str, ticker: str) -> None:
        """Subscribe to funding rate updates (futures only)."""
        msg = json.dumps({"Type": "subscribe_funding", "Data": {"ConnectionId": conn_id, "Ticker": ticker}})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_notifications(self) -> None:
        """Subscribe to notifications."""
        msg = json.dumps({"Type": "subscribe_notifications"})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def subscribe_signal_levels(self) -> None:
        """Subscribe to signal level updates."""
        msg = json.dumps({"Type": "subscribe_signal_levels"})
        with self._lock:
            self._subscriptions.add(msg)
        if self._ws and self._ws.sock and self._ws.sock.connected:
            try:
                self._ws.send(msg)
            except Exception as e:
                logger.warning("WS send error: %s", e)

    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.sock is not None and self._ws.sock.connected
