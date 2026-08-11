"""Telegram Bot — proactive alerts for signals and anomalies."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)


class AlertManager:
    """Monitors market data and sends proactive alerts via Telegram."""

    def __init__(
        self,
        bot_send_fn: Callable[[str, dict | None], bool],
        api_base_url: str = "http://127.0.0.1:8006",
        poll_interval_sec: float = 60.0,
    ):
        self._send = bot_send_fn
        self._api = api_base_url.rstrip("/")
        self._interval = poll_interval_sec
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_alerts: dict[str, float] = {}  # key -> timestamp
        self._cooldown_sec = 300  # 5 min cooldown per alert type

    def start(self) -> None:
        """Start monitoring in background."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="AlertManager",
            daemon=True,
        )
        self._thread.start()
        logger.info("AlertManager started")

    def stop(self) -> None:
        """Stop monitoring."""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None
        logger.info("AlertManager stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while not self._stop_event.wait(self._interval):
            try:
                self._check_signals()
            except Exception as e:
                logger.warning("AlertManager check error: %s", e)

    def _should_alert(self, key: str) -> bool:
        """Check if enough time has passed since last alert of this type."""
        now = time.time()
        last = self._last_alerts.get(key, 0)
        if now - last < self._cooldown_sec:
            return False
        self._last_alerts[key] = now
        return True

    def _check_signals(self) -> None:
        """Check for alertable signals."""
        # Check high spread signals
        self._check_high_spreads()
        # Check extreme funding rates
        self._check_funding_extremes()
        # Check large density walls
        self._check_density_walls()

    def _check_high_spreads(self) -> None:
        """Alert on high spread opportunities."""
        try:
            r = httpx.get(
                f"{self._api}/api/snapshot",
                params={"market": "futures", "exchange": "binance"},
                timeout=10,
            )
            data = r.json()
            if not data.get("ok"):
                return

            for row in data.get("rows", []):
                net_bps = row.get("net_spread_bps")
                if net_bps and net_bps >= 30:
                    symbol = row.get("symbol", "")
                    key = f"spread:{symbol}"
                    if self._should_alert(key):
                        self._send(
                            f"🚨 <b>Высокий спред!</b>\n\n"
                            f"Символ: <b>{symbol}</b>\n"
                            f"Чистый спред: <b>{net_bps:.1f} bps</b>\n"
                            f"Объём 24ч: ${row.get('volume_24h_quote', 0):,.0f}\n\n"
                            f"💡 Возможен арбитраж!",
                            None,
                        )
        except Exception as e:
            logger.debug("High spread check failed: %s", e)

    def _check_funding_extremes(self) -> None:
        """Alert on extreme funding rates."""
        try:
            r = httpx.get(
                f"{self._api}/api/snapshot",
                params={"market": "futures", "exchange": "binance"},
                timeout=10,
            )
            data = r.json()
            if not data.get("ok"):
                return

            for row in data.get("rows", []):
                fr = row.get("funding_rate")
                if fr and abs(fr) >= 0.001:  # 0.1%
                    symbol = row.get("symbol", "")
                    key = f"funding:{symbol}"
                    if self._should_alert(key):
                        direction = "📈 положительный" if fr > 0 else "📉 отрицательный"
                        annualized = fr * 3 * 365 * 100
                        self._send(
                            f"💸 <b>Экстремальный funding!</b>\n\n"
                            f"Символ: <b>{symbol}</b>\n"
                            f"Funding: <b>{fr:.4%}</b> ({direction})\n"
                            f"Annualized: <b>{annualized:.1f}%</b>\n\n"
                            f"💡 Возможен funding arbitrage!",
                            None,
                        )
        except Exception as e:
            logger.debug("Funding check failed: %s", e)

    def _check_density_walls(self) -> None:
        """Alert on large density walls."""
        try:
            r = httpx.get(
                f"{self._api}/api/density/walls",
                params={"symbol": "BTCUSDT", "market": "spot", "multiplier": 10, "min_notional": 200000},
                timeout=15,
            )
            data = r.json()
            if not data.get("ok"):
                return

            for wall in data.get("walls", [])[:3]:
                notional = wall.get("notional_usdt", 0)
                if notional >= 200000:
                    side = wall.get("side", "")
                    price = wall.get("price", 0)
                    key = f"density:BTCUSDT:{side}:{price}"
                    if self._should_alert(key):
                        emoji = "🟢 BID" if side == "bid" else "🔴 ASK"
                        self._send(
                            f"🧱 <b>Крупная стена!</b>\n\n"
                            f"Символ: <b>BTCUSDT</b>\n"
                            f"Сторона: <b>{emoji}</b>\n"
                            f"Размер: <b>${notional:,.0f}</b>\n"
                            f"Цена: <b>{price:,.2f}</b>",
                            None,
                        )
        except Exception as e:
            logger.debug("Density check failed: %s", e)
