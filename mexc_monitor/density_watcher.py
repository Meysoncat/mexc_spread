"""Density Watcher — фоновый мониторинг стакана и алерты о стенах.

Поллинг depth каждые N секунд, детекция появления/исчезновения стен,
отправка уведомлений через alerts/service.py.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from mexc_monitor.density import detect_walls, compute_density_stats, wall_to_dict
from mexc_monitor.density_buffer import (
    DensitySnapshot,
    WallChange,
    detect_changes,
    push_snapshot,
    push_wall_change,
)

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_SEC = 10.0
_DEFAULT_MIN_NOTIONAL_USDT = 50_000
_DEFAULT_MULTIPLIER = 5.0


class DensityWatcher:
    """Фоновый мониторинг плотности стакана."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        min_notional_usdt: float = _DEFAULT_MIN_NOTIONAL_USDT,
        multiplier: float = _DEFAULT_MULTIPLIER,
        on_wall_change: Callable[[WallChange], None] | None = None,
    ):
        self._symbols = symbols or ["BTCUSDT", "ETHUSDT"]
        self._poll_interval = poll_interval_sec
        self._min_notional = min_notional_usdt
        self._multiplier = multiplier
        self._on_wall_change = on_wall_change
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Запустить фоновый мониторинг."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="DensityWatcher",
            daemon=True,
        )
        self._thread.start()
        logger.info("DensityWatcher started: symbols=%s, interval=%.0fs", self._symbols, self._poll_interval)

    def stop(self) -> None:
        """Остановить мониторинг."""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 5)
            self._thread = None
        logger.info("DensityWatcher stopped")

    def update_symbols(self, symbols: list[str]) -> None:
        """Обновить список отслеживаемых символов."""
        with self._lock:
            self._symbols = list(symbols)

    def _poll_loop(self) -> None:
        """Основной цикл поллинга."""
        while not self._stop_event.wait(self._poll_interval):
            try:
                self._poll_once()
            except Exception as e:
                logger.warning("DensityWatcher poll error: %s", e)

    def _poll_once(self) -> None:
        """Один цикл опроса стаканов."""
        with self._lock:
            symbols = list(self._symbols)

        for symbol in symbols:
            if self._stop_event.is_set():
                return
            try:
                self._check_symbol(symbol)
            except Exception as e:
                logger.debug("DensityWatcher %s error: %s", symbol, e)

    def _check_symbol(self, symbol: str) -> None:
        """Проверить один символ."""
        import httpx

        # Получить depth (через Binance fallback)
        try:
            r = httpx.get(
                "https://api.binance.com/api/v3/depth",
                params={"symbol": symbol, "limit": 50},
                timeout=8,
            )
            r.raise_for_status()
            data = r.json()
            bids_raw = data.get("bids", [])
            asks_raw = data.get("asks", [])
            bids = [[float(b[0]), float(b[1])] for b in bids_raw if len(b) >= 2]
            asks = [[float(a[0]), float(a[1])] for a in asks_raw if len(a) >= 2]
        except Exception:
            return

        if not bids or not asks:
            return

        # Вычислить статистику
        stats = compute_density_stats(bids, asks)
        walls = detect_walls(bids, asks, multiplier=self._multiplier, min_notional_usdt=self._min_notional)
        wall_dicts = [wall_to_dict(w) for w in walls]

        # Сохранить snapshot в буфер
        now_ms = int(time.time() * 1000)
        largest_bid = max((w.notional_usdt for w in walls if w.side == "bid"), default=0)
        largest_ask = max((w.notional_usdt for w in walls if w.side == "ask"), default=0)

        push_snapshot(DensitySnapshot(
            timestamp_ms=now_ms,
            symbol=symbol,
            total_bid_notional=stats.total_bid_notional,
            total_ask_notional=stats.total_ask_notional,
            bid_ask_ratio=stats.bid_ask_ratio,
            wall_count=len(walls),
            largest_bid_notional=largest_bid,
            largest_ask_notional=largest_ask,
        ))

        # Детекция изменений
        changes = detect_changes(symbol, wall_dicts, min_notional_usdt=self._min_notional)
        for change in changes:
            push_wall_change(change)
            if self._on_wall_change:
                try:
                    self._on_wall_change(change)
                except Exception as e:
                    logger.warning("DensityWatcher callback error: %s", e)
