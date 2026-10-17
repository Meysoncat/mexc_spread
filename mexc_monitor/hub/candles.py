"""1m-свечи для направленных сетапов: хранилище + WS-подписки на kline.

История подгружается по REST при добавлении символа во вселенную, дальше
свечи обновляются пушем. Объём хранится как отдаёт биржа (контракты или
монеты) — детекторы работают с относительным объёмом, для USD есть ``qv``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from mexc_monitor.hub.http import fnum
from mexc_monitor.hub.models import Candle
from mexc_monitor.hub.symbols import norm_key

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_SEC = [1, 2, 5, 10, 30]


class CandleSeries:
    __slots__ = ("candles",)

    def __init__(self, maxlen: int) -> None:
        self.candles: deque[Candle] = deque(maxlen=maxlen)

    def apply(self, c: Candle) -> None:
        if self.candles:
            last = self.candles[-1]
            if c.t == last.t:
                self.candles[-1] = c
                return
            if c.t < last.t:
                return  # запоздавшее обновление уже закрытой свечи
            if not last.closed:
                last.closed = True
        self.candles.append(c)

    def merge_history(self, history: list[Candle]) -> None:
        """Вклеить REST-историю перед живыми свечами (без дублей по t)."""
        live = list(self.candles)
        first_live = live[0].t if live else None
        merged = [h for h in history if first_live is None or h.t < first_live]
        for h in merged:
            h.closed = True
        self.candles.clear()
        for c in merged + live:
            self.candles.append(c)


class CandleStore:
    def __init__(self, maxlen: int = 300) -> None:
        self._lock = threading.Lock()
        self._maxlen = maxlen
        self._series: dict[tuple[str, str], CandleSeries] = {}

    def apply(self, venue: str, key: str, c: Candle) -> None:
        with self._lock:
            s = self._series.get((venue, key))
            if s is None:
                s = self._series[(venue, key)] = CandleSeries(self._maxlen)
            s.apply(c)

    def merge_history(self, venue: str, key: str, history: list[Candle]) -> None:
        with self._lock:
            s = self._series.get((venue, key))
            if s is None:
                s = self._series[(venue, key)] = CandleSeries(self._maxlen)
            s.merge_history(history)

    def get(self, venue: str, key: str) -> list[Candle]:
        with self._lock:
            s = self._series.get((venue, key))
            return list(s.candles) if s is not None else []

    def keys(self, venue: str | None = None) -> list[tuple[str, str]]:
        with self._lock:
            return [k for k in self._series if venue is None or k[0] == venue]

    def drop(self, venue: str, keep: set[str]) -> None:
        with self._lock:
            for k in [k for k in self._series if k[0] == venue and k[1] not in keep]:
                del self._series[k]


# ─────────────────────────────────────────────────────────── parsers


def parse_binance_kline_msg(msg: Any) -> list[tuple[str, Candle]]:
    data = msg.get("data") if isinstance(msg, dict) else None
    k = data.get("k") if isinstance(data, dict) else None
    sym = (k.get("s") or data.get("s")) if isinstance(k, dict) else None
    if not sym:
        return []
    vals = [fnum(k.get(f)) for f in ("o", "h", "l", "c", "v")]
    if None in vals or k.get("t") is None:
        return []
    o, h, l, c, v = vals
    return [(norm_key(sym), Candle(t=int(k["t"]), o=o, h=h, l=l, c=c, v=v, qv=fnum(k.get("q")) or 0.0, closed=bool(k.get("x"))))]


def parse_bybit_kline_msg(msg: Any) -> list[tuple[str, Candle]]:
    if not isinstance(msg, dict):
        return []
    topic = msg.get("topic", "")
    if not isinstance(topic, str) or not topic.startswith("kline."):
        return []
    key = norm_key(topic.split(".")[-1])
    out = []
    for it in msg.get("data") or []:
        if not isinstance(it, dict):
            continue
        vals = [fnum(it.get(f)) for f in ("start", "open", "high", "low", "close", "volume")]
        if None in vals:
            continue
        t, o, h, l, c, v = vals
        out.append((key, Candle(t=int(t), o=o, h=h, l=l, c=c, v=v, qv=fnum(it.get("turnover")) or 0.0, closed=bool(it.get("confirm")))))
    return out


def parse_mexc_kline_msg(msg: Any) -> list[tuple[str, Candle]]:
    if not isinstance(msg, dict) or msg.get("channel") != "push.kline":
        return []
    d = msg.get("data")
    if not isinstance(d, dict):
        return []
    sym = d.get("symbol") or msg.get("symbol")
    vals = [fnum(d.get(f)) for f in ("t", "o", "h", "l", "c", "q")]
    if not sym or None in vals:
        return []
    t, o, h, l, c, v = vals
    # MEXC не помечает закрытие свечи — CandleSeries закрывает её при появлении следующей
    return [(norm_key(sym), Candle(t=int(t) * 1000, o=o, h=h, l=l, c=c, v=v, qv=fnum(d.get("a")) or 0.0, closed=False))]


# ─────────────────────────────────────────────────────────── WS workers


class _WsWorker:
    """Одно WS-соединение: подписки после коннекта, пинг, разбор, реконнект."""

    def __init__(
        self,
        name: str,
        url: str,
        subscribe: list[str],
        parse: Callable[[Any], list[tuple[str, Candle]]],
        sink: Callable[[str, Candle], None],
        *,
        ping_interval: float = 0.0,
        ping_payload: str | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.subscribe = subscribe
        self.parse = parse
        self.sink = sink
        self.ping_interval = ping_interval
        self.ping_payload = ping_payload
        self.stop = threading.Event()
        self.last_msg_mono = 0.0
        self.thread = threading.Thread(target=self._run, name=f"ws-kline-{name}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        import websocket

        attempt = 0
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.url, timeout=15)
                for m in self.subscribe:
                    ws.send(m)
                    time.sleep(0.02)
                attempt = 0
                ws.settimeout(5)
                last_ping = time.monotonic()
                last_raw = time.monotonic()
                while not self.stop.is_set():
                    if self.ping_payload and self.ping_interval and time.monotonic() - last_ping >= self.ping_interval:
                        ws.send(self.ping_payload)
                        last_ping = time.monotonic()
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_raw > 90:
                            break
                        continue
                    last_raw = time.monotonic()
                    if not raw or raw in ("pong", "ping"):
                        continue
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    updates = self.parse(msg)
                    if updates:
                        self.last_msg_mono = time.monotonic()
                        for key, c in updates:
                            self.sink(key, c)
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001 — реконнект
                if self.stop.is_set():
                    return
                delay = _RECONNECT_BACKOFF_SEC[min(attempt, len(_RECONNECT_BACKOFF_SEC) - 1)]
                attempt += 1
                logger.warning("ws-kline %s error (%s), reconnect in %ss", self.name, e, delay)
                if self.stop.wait(delay):
                    return


def _chunks(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def build_kline_workers(
    venue: str,
    natives: list[str],
    sink: Callable[[str, Candle], None],
) -> list[_WsWorker]:
    """WS-воркеры свечей 1m для биржи; natives — символы API биржи."""
    workers: list[_WsWorker] = []
    if venue == "binance":
        for i, chunk in enumerate(_chunks(natives, 150)):
            streams = "/".join(f"{s.lower()}@kline_1m" for s in chunk)
            workers.append(_WsWorker(f"binance-{i}", f"wss://fstream.binance.com/stream?streams={streams}", [], parse_binance_kline_msg, sink))
    elif venue == "bybit":
        for i, chunk in enumerate(_chunks(natives, 100)):
            subs = [json.dumps({"op": "subscribe", "args": [f"kline.1.{s}" for s in part]}) for part in _chunks(chunk, 10)]
            workers.append(
                _WsWorker(f"bybit-{i}", "wss://stream.bybit.com/v5/public/linear", subs, parse_bybit_kline_msg, sink,
                          ping_interval=20, ping_payload=json.dumps({"op": "ping"}))
            )
    elif venue == "mexc":
        for i, chunk in enumerate(_chunks(natives, 100)):
            subs = [json.dumps({"method": "sub.kline", "param": {"symbol": s, "interval": "Min1"}}) for s in chunk]
            workers.append(
                _WsWorker(f"mexc-{i}", "wss://contract.mexc.com/edge", subs, parse_mexc_kline_msg, sink,
                          ping_interval=15, ping_payload=json.dumps({"method": "ping"}))
            )
    return workers


KLINE_WS_VENUES = ("binance", "bybit", "mexc")
