"""
AsterDEX WebSocket Client — real-time bookTicker stream.

AsterDEX использует Binance-совместимый WebSocket формат:
  URL: wss://fstream.asterdex.com/ws
  Подписка: {"method": "SUBSCRIBE", "params": ["btcusdt@bookTicker"], "id": 1}
  Push: {"e": "bookTicker", "s": "BTCUSDT", "b": "65000.00", "B": "1.5", "a": "65001.00", "A": "2.3"}

Данные записываются в Spread_Buffer с префиксом ASTER: для различения от MEXC.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://fstream.asterdex.com/ws"
_MAX_RECONNECT_DELAY = 60.0
_INITIAL_RECONNECT_DELAY = 1.0
_PING_INTERVAL_SEC = 30.0

_lock = threading.Lock()
_client_instance: "AsterWebSocketClient | None" = None


class AsterWebSocketClient:
    """WebSocket-клиент для AsterDEX bookTicker stream."""

    def __init__(
        self,
        url: str = DEFAULT_WS_URL,
        symbols: list[str] | None = None,
        ping_interval_sec: float = _PING_INTERVAL_SEC,
        on_tick: Callable[[str, float, float, float, float], None] | None = None,
    ):
        self._url = url
        self._symbols: set[str] = set(s.upper() for s in (symbols or []))
        self._ping_interval = ping_interval_sec
        self._on_tick = on_tick
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected = False
        self._sub_id = 0
        self._ws: Any = None
        self._ws_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    def get_subscribed_symbols(self) -> list[str]:
        return sorted(self._symbols)

    def start(self) -> None:
        """Запуск фонового потока WebSocket."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="aster-ws",
        )
        self._thread.start()
        logger.info(
            "AsterDEX WS: started, symbols=%s",
            ", ".join(sorted(self._symbols)[:5]) + ("..." if len(self._symbols) > 5 else ""),
        )

    def stop(self) -> None:
        """Остановка WebSocket."""
        self._stop_event.set()
        with self._ws_lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._connected = False
        logger.info("AsterDEX WS: stopped")

    def subscribe(self, symbol: str) -> None:
        """Подписаться на символ (можно вызывать на ходу)."""
        sym = symbol.strip().upper()
        if sym in self._symbols:
            return
        self._symbols.add(sym)
        # Если WS уже подключён — отправить подписку
        with self._ws_lock:
            if self._ws is not None and self._connected:
                self._send_subscribe([sym])

    def unsubscribe(self, symbol: str) -> None:
        """Отписаться от символа."""
        sym = symbol.strip().upper()
        self._symbols.discard(sym)
        with self._ws_lock:
            if self._ws is not None and self._connected:
                self._send_unsubscribe([sym])

    def _send_subscribe(self, symbols: list[str]) -> None:
        """Отправить SUBSCRIBE для списка символов."""
        if not symbols:
            return
        self._sub_id += 1
        params = [f"{s.lower()}@bookTicker" for s in symbols]
        msg = json.dumps({"method": "SUBSCRIBE", "params": params, "id": self._sub_id})
        try:
            if self._ws:
                self._ws.send(msg)
        except Exception as e:
            logger.warning("AsterDEX WS subscribe error: %s", e)

    def _send_unsubscribe(self, symbols: list[str]) -> None:
        """Отправить UNSUBSCRIBE."""
        if not symbols:
            return
        self._sub_id += 1
        params = [f"{s.lower()}@bookTicker" for s in symbols]
        msg = json.dumps({"method": "UNSUBSCRIBE", "params": params, "id": self._sub_id})
        try:
            if self._ws:
                self._ws.send(msg)
        except Exception as e:
            logger.warning("AsterDEX WS unsubscribe error: %s", e)

    def _handle_message(self, raw: str) -> None:
        """Обработка входящего сообщения."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        # Пропускаем ответы на subscribe/pong
        if "result" in data or "id" in data:
            return

        event_type = data.get("e")
        if event_type != "bookTicker":
            return

        symbol = data.get("s", "")
        if not symbol:
            return

        try:
            bid = float(data.get("b", 0))
            bid_qty = float(data.get("B", 0))
            ask = float(data.get("a", 0))
            ask_qty = float(data.get("A", 0))
        except (TypeError, ValueError):
            return

        if bid <= 0 or ask <= 0 or ask < bid:
            return

        sym_upper = symbol.upper()

        # Записываем в spread_buffer с префиксом ASTER:
        try:
            from mexc_monitor.spread_buffer import push_tick
            push_tick(f"ASTER:{sym_upper}", bid, ask, bid_qty, ask_qty)
        except Exception:
            pass

        # Вычисляем кросс-спред если есть данные MEXC
        self._compute_cross_spread(sym_upper, bid, ask)

        # Callback
        if self._on_tick:
            try:
                self._on_tick(sym_upper, bid, ask, bid_qty, ask_qty)
            except Exception:
                pass

    def _compute_cross_spread(self, symbol: str, aster_bid: float, aster_ask: float) -> None:
        """Вычислить кросс-спред если есть данные MEXC для того же символа."""
        try:
            from mexc_monitor.spread_buffer import get_latest, push_tick

            # Попробовать MEXC spot (BTCUSDT) или futures (BTC_USDT)
            mexc_tick = get_latest(symbol)
            if mexc_tick is None:
                # Попробовать futures формат
                fut_sym = symbol.replace("USDT", "_USDT") if "USDT" in symbol and "_" not in symbol else None
                if fut_sym:
                    mexc_tick = get_latest(fut_sym)

            if mexc_tick is None:
                return

            # Вычисляем кросс-спред
            aster_mid = (aster_bid + aster_ask) / 2
            mexc_mid = mexc_tick.mid
            if mexc_mid <= 0 or aster_mid <= 0:
                return

            # Записываем basis как "спред" в CROSS: буфер
            # bid = mexc_mid, ask = aster_mid (для визуализации разницы)
            # spread_bps будет = basis_bps
            basis_bps_val = 10_000 * (aster_mid - mexc_mid) / mexc_mid
            # Используем push_tick с фиктивными bid/ask для хранения basis
            # bid = min(mexc_mid, aster_mid), ask = max(mexc_mid, aster_mid)
            push_tick(
                f"CROSS:{symbol}",
                min(mexc_mid, aster_mid),
                max(mexc_mid, aster_mid),
                0.0,  # qty не релевантно для кросс-спреда
                0.0,
            )
        except Exception:
            pass

    def _run_loop(self) -> None:
        """Основной цикл WebSocket с reconnect."""
        try:
            import websocket
        except ImportError:
            logger.error("websocket-client not installed; pip install websocket-client")
            return

        reconnect_delay = _INITIAL_RECONNECT_DELAY

        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(self._url, timeout=15.0)
                ws.settimeout(self._ping_interval + 5.0)
                with self._ws_lock:
                    self._ws = ws
                self._connected = True
                reconnect_delay = _INITIAL_RECONNECT_DELAY

                # Подписка на все символы
                if self._symbols:
                    self._send_subscribe(list(self._symbols))

                last_ping = time.monotonic()

                while not self._stop_event.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        # Ping
                        if time.monotonic() - last_ping >= self._ping_interval:
                            try:
                                ws.pong("")
                            except Exception:
                                pass
                            last_ping = time.monotonic()
                        continue
                    except websocket.WebSocketConnectionClosedException:
                        break
                    except OSError:
                        break

                    if not isinstance(raw, str):
                        continue
                    if raw in ("ping", "pong"):
                        continue

                    self._handle_message(raw)

            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(
                        "AsterDEX WS error: %s; reconnect in %.1fs",
                        f"{type(e).__name__}: {e}",
                        reconnect_delay,
                    )
            finally:
                self._connected = False
                with self._ws_lock:
                    self._ws = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, _MAX_RECONNECT_DELAY)


# ─── Module-level singleton management ──────────────────────────────────────────

def get_aster_ws_client() -> AsterWebSocketClient | None:
    """Получить текущий singleton клиент."""
    with _lock:
        return _client_instance


def ensure_aster_ws_started(
    url: str = DEFAULT_WS_URL,
    symbols: list[str] | None = None,
    ping_interval_sec: float = _PING_INTERVAL_SEC,
) -> AsterWebSocketClient:
    """Идемпотентный запуск AsterDEX WS клиента."""
    global _client_instance
    with _lock:
        if _client_instance is not None and _client_instance._thread is not None and _client_instance._thread.is_alive():
            # Добавить новые символы если нужно
            if symbols:
                for s in symbols:
                    _client_instance.subscribe(s)
            return _client_instance

        client = AsterWebSocketClient(
            url=url,
            symbols=symbols,
            ping_interval_sec=ping_interval_sec,
        )
        client.start()
        _client_instance = client
        return client


def stop_aster_ws() -> None:
    """Остановить AsterDEX WS клиент."""
    global _client_instance
    with _lock:
        if _client_instance is not None:
            _client_instance.stop()
            _client_instance = None
