"""Auto-trading engine for MetaScalp signal levels.

Monitors triggered signal levels and automatically places orders
based on configurable rules (e.g. BuyMarket when price hits support).

Usage:
    trader = MetaScalpAutoTrader(client)
    trader.start()
    trader.set_config({"enabled": True, "default_side": "Buy", "default_size": 0.01})
    ...
    trader.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .client import MetaScalpClient

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = {
    "enabled": False,
    "default_side": "Buy",
    "default_type": "Market",
    "default_size": 0.01,
    "auto_cancel_triggered": True,  # Auto-remove triggered levels after execution
}


class MetaScalpAutoTrader:
    """Auto-trades on triggered MetaScalp signal levels."""

    def __init__(self, client: MetaScalpClient | None = None) -> None:
        self._client = client or MetaScalpClient()
        self._config: dict[str, Any] = dict(_DEFAULT_CONFIG)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_triggered: set[str] = set()  # Track already-processed triggers

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the auto-trader background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="metascalp-auto-trader",
        )
        self._thread.start()
        logger.info("MetaScalpAutoTrader started")

    def stop(self) -> None:
        """Stop the auto-trader."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._thread = None
        logger.info("MetaScalpAutoTrader stopped")

    def is_running(self) -> bool:
        return self._running

    # ── Config ────────────────────────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def set_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            self._config.update(config)
        logger.info("MetaScalpAutoTrader config updated: %s", config)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Poll for triggered signal levels and execute trades."""
        while not self._stop_event.is_set():
            try:
                cfg = self.get_config()
                if cfg.get("enabled"):
                    self._check_and_trade()
            except Exception:
                logger.exception("Auto-trader tick failed")

            # Check every 2 seconds
            if self._stop_event.wait(timeout=2.0):
                break

    def _check_and_trade(self) -> None:
        """Check all connections for triggered signal levels and trade."""
        conns = self._client.connections()
        for conn in conns:
            if self._stop_event.is_set():
                break
            self._process_connection(conn.id)

    def _process_connection(self, conn_id: str) -> None:
        """Process triggered levels for a single connection."""
        cfg = self.get_config()

        # Get all signal levels for all tickers is expensive,
        # so we rely on the triggered-only endpoint or check per-ticker
        # For now, we check a common set of tickers
        # In production, MetaScalp WS would push triggered events

        # Alternative: get triggered levels via a special API if available
        # For now, we poll signal levels for known symbols
        # This is a simplified version - in production use WS notifications

        # Check if any levels were triggered by comparing with cached state
        # This is a placeholder - real implementation would use:
        # 1. WS notifications for trigger events
        # 2. Or poll /signal-levels and detect is_triggered=True

        pass  # Placeholder - requires WS trigger events or polling per-ticker

    def on_signal_triggered(
        self,
        conn_id: str,
        ticker: str,
        price: float,
        rule: str = "",
    ) -> dict[str, Any]:
        """Called when a signal level is triggered (externally or via WS).

        Places an order based on the configured default parameters.
        """
        cfg = self.get_config()
        if not cfg.get("enabled"):
            return {"ok": False, "error": "Auto-trader disabled"}

        # Determine side from rule or default
        side = cfg.get("default_side", "Buy")
        if rule:
            rule_lower = rule.lower()
            if "buy" in rule_lower:
                side = "Buy"
            elif "sell" in rule_lower:
                side = "Sell"

        order_type = cfg.get("default_type", "Market")
        size = float(cfg.get("default_size", 0.01))

        logger.info(
            "Auto-trading: %s %s %s @ %s (rule=%s)",
            side, size, ticker, price, rule,
        )

        result = self._client.place_order(
            conn_id=conn_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            size=size,
            price=None if order_type == "Market" else price,
        )

        # Auto-remove triggered level if configured
        if cfg.get("auto_cancel_triggered") and result.get("ok"):
            self._client.remove_triggered_signal_levels()

        return result

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "config": self.get_config(),
        }
