"""Живой bookTicker по WebSocket для бирж с единым стримом на все символы.

Паттерн как в ws_futures.py (MEXC): фоновый поток держит соединение и
обновляет буфер лучших bid/ask; сборка снапшота берёт буфер, если он свежий,
и падает обратно на REST, если стрим не готов.

Сейчас поддерживается Binance USDT-M futures (стрим `!bookTicker` — один
канал на все символы). 24h-объёмы меняются медленно, поэтому берутся по REST
и кэшируются отдельно (60 сек).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

_STALE_AFTER_SEC = 15.0
_VOLUME_CACHE_TTL_SEC = 60.0
_RECONNECT_BACKOFF_SEC = [1, 2, 5, 10, 30]

_BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws/!bookTicker"


class _Feed:
    """Один WS-стрим: symbol -> (bid, bid_qty, ask, ask_qty), метка свежести."""

    def __init__(self, name: str, url: str, parse: Callable[[Any], tuple | None]):
        self.name = name
        self.url = url
        self.parse = parse
        self.lock = threading.Lock()
        self.book: dict[str, tuple[float, float, float, float]] = {}
        self.last_msg_mono = 0.0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def ensure_started(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(
            target=self._run, name=f"ws-bookticker-{self.name}", daemon=True
        )
        self.thread.start()

    def snapshot(self, *, max_age_sec: float = _STALE_AFTER_SEC) -> dict | None:
        with self.lock:
            if not self.book:
                return None
            if (time.monotonic() - self.last_msg_mono) > max_age_sec:
                return None
            return dict(self.book)

    def _run(self) -> None:
        import websocket

        attempt = 0
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.url, timeout=15)
                attempt = 0
                logger.info("ws-bookticker %s connected", self.name)
                # bookTicker шлёт тысячи сообщений/сек: тишина дольше 10с
                # означает «мёртвое» соединение — переподключаемся.
                ws.settimeout(10)
                while not self.stop.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        logger.warning(
                            "ws-bookticker %s stream silent, reconnecting",
                            self.name,
                        )
                        break
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    parsed = self.parse(msg)
                    if parsed is None:
                        continue
                    sym, bid, bq, ask, aq = parsed
                    with self.lock:
                        self.book[sym] = (bid, bq, ask, aq)
                        self.last_msg_mono = time.monotonic()
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001 — reconnect loop
                if self.stop.is_set():
                    return
                delay = _RECONNECT_BACKOFF_SEC[
                    min(attempt, len(_RECONNECT_BACKOFF_SEC) - 1)
                ]
                attempt += 1
                logger.warning(
                    "ws-bookticker %s error (%s: %s), reconnect in %ss",
                    self.name,
                    type(e).__name__,
                    e,
                    delay,
                )
                if self.stop.wait(delay):
                    return


def _parse_binance(msg: Any) -> tuple | None:
    if not isinstance(msg, dict):
        return None
    sym = msg.get("s")
    if not sym:
        return None
    try:
        bid = float(msg.get("b") or 0)
        ask = float(msg.get("a") or 0)
        bq = float(msg.get("B") or 0)
        aq = float(msg.get("A") or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0:
        return None
    return sym, bid, bq, ask, aq


_feeds: dict[str, _Feed] = {
    "binance": _Feed("binance", _BINANCE_FUTURES_WS_URL, _parse_binance),
}

_volume_lock = threading.Lock()
# exchange -> (истекает_mono, symbol -> (vol_base, vol_quote))
_volume_cache: dict[str, tuple[float, dict[str, tuple[float, float]]]] = {}


def _binance_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.binance.client import BinancePublicClient, _parse_float

    client = BinancePublicClient()
    out: dict[str, tuple[float, float]] = {}
    for t in client.ticker_24h(market="futures"):
        sym = t.get("symbol") if isinstance(t, dict) else None
        if sym:
            out[sym] = (
                _parse_float(t.get("volume")),
                _parse_float(t.get("quoteVolume")),
            )
    return out


_volume_loaders: dict[str, Callable[[], dict[str, tuple[float, float]]]] = {
    "binance": _binance_volumes,
}


def _volumes_for(exchange: str) -> dict[str, tuple[float, float]]:
    now = time.monotonic()
    with _volume_lock:
        cached = _volume_cache.get(exchange)
        if cached is not None and cached[0] > now:
            return cached[1]
    loader = _volume_loaders.get(exchange)
    if loader is None:
        return {}
    try:
        volumes = loader()
    except Exception as e:  # noqa: BLE001 — объёмы не критичны для снапшота
        logger.warning("ws-bookticker %s volumes failed: %s", exchange, e)
        stale = _volume_cache.get(exchange)
        return stale[1] if stale is not None else {}
    with _volume_lock:
        _volume_cache[exchange] = (now + _VOLUME_CACHE_TTL_SEC, volumes)
    return volumes


def ensure_started() -> None:
    """Запускает все WS-фиды заранее (прогрев при старте бэкенда)."""
    for feed in _feeds.values():
        feed.ensure_started()


def try_ws_snapshot_rows(exchange: str, market: str | None) -> list[BookTickerRow] | None:
    """Строки снапшота из WS-буфера, если стрим жив и свеж; иначе None (REST).

    Первый вызов запускает фоновый стрим — данные появятся через секунды,
    а до тех пор снапшот строится по REST как раньше.
    """
    if market not in (None, "futures", "perp"):
        return None
    if exchange == "dydx":
        from mexc_monitor.dydx.ws_feed import try_dydx_ws_snapshot_rows

        return try_dydx_ws_snapshot_rows()
    feed = _feeds.get(exchange)
    if feed is None:
        return None
    feed.ensure_started()
    book = feed.snapshot()
    if not book or len(book) < 10:
        return None

    volumes = _volumes_for(exchange)
    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[BookTickerRow] = []
    for sym, (bid, bq, ask, aq) in book.items():
        mid = (bid + ask) / 2
        spread_abs = ask - bid
        vol = volumes.get(sym, (0.0, 0.0))
        rows.append(
            BookTickerRow(
                symbol=sym,
                bid=bid,
                ask=ask,
                bid_qty=bq,
                ask_qty=aq,
                mid=mid,
                spread_abs=spread_abs,
                spread_bps=(10_000 * spread_abs / mid) if mid > 0 else None,
                volume_24h_base=vol[0],
                volume_24h_quote=vol[1],
                observed_at=now_iso,
            )
        )
    return rows


def stop_all() -> None:
    from mexc_monitor.dydx.ws_feed import stop_dydx_ws

    stop_dydx_ws()
    for feed in _feeds.values():
        feed.stop.set()
