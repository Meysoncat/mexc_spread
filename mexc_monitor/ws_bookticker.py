"""Живой bookTicker по WebSocket для CEX-бирж.

Паттерн как в ws_futures.py (MEXC): фоновый поток держит соединение и
обновляет буфер лучших bid/ask; сборка снапшота берёт буфер, если он свежий,
и падает обратно на REST, если стрим не готов.

Поддерживаются futures/perp:
  - Binance USDT-M (`!bookTicker` — один канал на все символы)
  - OKX (channel `tickers`, подписка по instId)
  - Bybit linear (topic `tickers.<symbol>`, snapshot+delta)
  - Gate.io USDT futures (`futures.book_ticker`)
  - Bitget USDT-FUTURES (channel `ticker`)
  - HTX linear-swap (`market.<code>.bbo`, gzip-фреймы)

24h-объёмы меняются медленно, поэтому берутся по REST и кэшируются (60 сек).
"""

from __future__ import annotations

import gzip
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
_OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_GATEIO_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
_BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
_HTX_WS_URL = "wss://api.hbdm.com/linear-swap-ws"

# (sym, bid|None, bid_qty|None, ask|None, ask_qty|None); None = взять прежнее
_Update = tuple[str, float | None, float | None, float | None, float | None]


class _Feed:
    """Один WS-стрим: symbol -> (bid, bid_qty, ask, ask_qty), метка свежести.

    parse(msg) возвращает список обновлений; None в полях обновления означает
    «оставить прошлое значение» (нужно для дельта-каналов вроде Bybit tickers).
    subscribe() возвращает список сообщений подписки, отправляемых после
    коннекта; ping_payload() шлётся каждые ping_interval секунд; control(msg)
    может вернуть ответ на серверный ping (HTX).
    """

    def __init__(
        self,
        name: str,
        url: str,
        parse: Callable[[Any], list[_Update] | None],
        *,
        subscribe: Callable[[], list[str]] | None = None,
        ping_interval: float = 0.0,
        ping_payload: Callable[[], str] | None = None,
        control: Callable[[Any], str | None] | None = None,
        gzip_frames: bool = False,
        silent_timeout: float = 60.0,
    ):
        self.name = name
        self.url = url
        self.parse = parse
        self.subscribe = subscribe
        self.ping_interval = ping_interval
        self.ping_payload = ping_payload
        self.control = control
        self.gzip_frames = gzip_frames
        self.silent_timeout = silent_timeout
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

    def health(self) -> dict[str, Any]:
        with self.lock:
            age = (
                round(time.monotonic() - self.last_msg_mono, 1)
                if self.last_msg_mono
                else None
            )
            return {
                "running": self.thread is not None and self.thread.is_alive(),
                "symbols": len(self.book),
                "last_message_age_sec": age,
                "live": bool(
                    self.book
                    and age is not None
                    and age <= _STALE_AFTER_SEC
                ),
            }

    def _apply(self, updates: list[_Update]) -> None:
        with self.lock:
            for sym, bid, bq, ask, aq in updates:
                prev = self.book.get(sym)
                if prev is None and (bid is None or ask is None):
                    continue
                p_bid, p_bq, p_ask, p_aq = prev or (0.0, 0.0, 0.0, 0.0)
                row = (
                    p_bid if bid is None else bid,
                    p_bq if bq is None else bq,
                    p_ask if ask is None else ask,
                    p_aq if aq is None else aq,
                )
                if row[0] <= 0 or row[2] <= 0:
                    continue
                self.book[sym] = row
            self.last_msg_mono = time.monotonic()

    def _run(self) -> None:
        import websocket

        attempt = 0
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.url, timeout=15)
                logger.info("ws-bookticker %s connected", self.name)
                if self.subscribe is not None:
                    for msg in self.subscribe():
                        ws.send(msg)
                attempt = 0
                ws.settimeout(5)
                last_raw = time.monotonic()
                last_ping = time.monotonic()
                while not self.stop.is_set():
                    now = time.monotonic()
                    if (
                        self.ping_payload is not None
                        and self.ping_interval > 0
                        and now - last_ping >= self.ping_interval
                    ):
                        ws.send(self.ping_payload())
                        last_ping = now
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_raw > self.silent_timeout:
                            logger.warning(
                                "ws-bookticker %s stream silent, reconnecting",
                                self.name,
                            )
                            break
                        continue
                    if not raw:
                        continue
                    last_raw = time.monotonic()
                    if self.gzip_frames and isinstance(raw, (bytes, bytearray)):
                        try:
                            raw = gzip.decompress(raw)
                        except OSError:
                            continue
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
                    if self.control is not None:
                        reply = self.control(msg)
                        if reply is not None:
                            ws.send(reply)
                            continue
                    updates = self.parse(msg)
                    if updates:
                        self._apply(updates)
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


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Binance


def _parse_binance(msg: Any) -> list[_Update] | None:
    if not isinstance(msg, dict):
        return None
    sym = msg.get("s")
    if not sym:
        return None
    bid, ask = _f(msg.get("b")), _f(msg.get("a"))
    if not bid or not ask:
        return None
    return [(sym, bid, _f(msg.get("B")) or 0.0, ask, _f(msg.get("A")) or 0.0)]


# ---------------------------------------------------------------- OKX


def _okx_subscribe() -> list[str]:
    from mexc_monitor.okx.client import OkxPublicClient

    inst_ids = [t.symbol for t in OkxPublicClient().book_tickers("SWAP")]
    msgs: list[str] = []
    for i in range(0, len(inst_ids), 50):
        args = [
            {"channel": "tickers", "instId": inst_id}
            for inst_id in inst_ids[i : i + 50]
        ]
        msgs.append(json.dumps({"op": "subscribe", "args": args}))
    return msgs


def _parse_okx(msg: Any) -> list[_Update] | None:
    from mexc_monitor.okx.client import _normalize_symbol

    if not isinstance(msg, dict):
        return None
    data = msg.get("data")
    if not isinstance(data, list):
        return None
    out: list[_Update] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        inst_id = item.get("instId")
        bid, ask = _f(item.get("bidPx")), _f(item.get("askPx"))
        if not inst_id or not bid or not ask:
            continue
        out.append(
            (
                _normalize_symbol(inst_id),
                bid,
                _f(item.get("bidSz")) or 0.0,
                ask,
                _f(item.get("askSz")) or 0.0,
            )
        )
    return out or None


# ---------------------------------------------------------------- Bybit


def _bybit_subscribe() -> list[str]:
    from mexc_monitor.bybit.client import BybitPublicClient

    symbols = [t.symbol for t in BybitPublicClient().book_tickers("linear")]
    msgs: list[str] = []
    for i in range(0, len(symbols), 10):
        args = [f"tickers.{s}" for s in symbols[i : i + 10]]
        msgs.append(json.dumps({"op": "subscribe", "args": args}))
    return msgs


def _parse_bybit(msg: Any) -> list[_Update] | None:
    if not isinstance(msg, dict):
        return None
    topic = msg.get("topic", "")
    if not topic.startswith("tickers."):
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    sym = data.get("symbol") or topic.split(".", 1)[1]
    if not sym:
        return None
    # delta-сообщения содержат только изменившиеся поля — None мёржится
    return [
        (
            sym,
            _f(data.get("bid1Price")),
            _f(data.get("bid1Size")),
            _f(data.get("ask1Price")),
            _f(data.get("ask1Size")),
        )
    ]


# ---------------------------------------------------------------- Gate.io


def _gateio_subscribe() -> list[str]:
    from mexc_monitor.gateio.client import GateioPublicClient

    contracts = [
        t.symbol for t in GateioPublicClient().book_tickers(market="futures")
    ]
    now = int(time.time())
    return [
        json.dumps(
            {
                "time": now,
                "channel": "futures.book_ticker",
                "event": "subscribe",
                "payload": [c],
            }
        )
        for c in contracts
    ]


def _parse_gateio(msg: Any) -> list[_Update] | None:
    from mexc_monitor.gateio.client import _normalize_symbol

    if not isinstance(msg, dict):
        return None
    if msg.get("channel") != "futures.book_ticker" or msg.get("event") != "update":
        return None
    r = msg.get("result")
    if not isinstance(r, dict):
        return None
    sym = r.get("s")
    bid, ask = _f(r.get("b")), _f(r.get("a"))
    if not sym or not bid or not ask:
        return None
    return [
        (
            _normalize_symbol(sym),
            bid,
            _f(r.get("B")) or 0.0,
            ask,
            _f(r.get("A")) or 0.0,
        )
    ]


def _gateio_ping() -> str:
    return json.dumps({"time": int(time.time()), "channel": "futures.ping"})


# ---------------------------------------------------------------- Bitget


def _bitget_subscribe() -> list[str]:
    from mexc_monitor.bitget.client import BitgetPublicClient

    symbols = [t.symbol for t in BitgetPublicClient().book_tickers()]
    msgs: list[str] = []
    for i in range(0, len(symbols), 50):
        args = [
            {"instType": "USDT-FUTURES", "channel": "ticker", "instId": s}
            for s in symbols[i : i + 50]
        ]
        msgs.append(json.dumps({"op": "subscribe", "args": args}))
    return msgs


def _parse_bitget(msg: Any) -> list[_Update] | None:
    if not isinstance(msg, dict):
        return None
    arg = msg.get("arg")
    data = msg.get("data")
    if not isinstance(arg, dict) or not isinstance(data, list):
        return None
    if arg.get("channel") != "ticker":
        return None
    out: list[_Update] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sym = item.get("instId") or arg.get("instId")
        bid, ask = _f(item.get("bidPr")), _f(item.get("askPr"))
        if not sym or not bid or not ask:
            continue
        out.append(
            (
                sym,
                bid,
                _f(item.get("bidSz")) or 0.0,
                ask,
                _f(item.get("askSz")) or 0.0,
            )
        )
    return out or None


# ---------------------------------------------------------------- HTX


def _htx_contract_codes() -> list[str]:
    from mexc_monitor.http_shared import shared_get

    r = shared_get(
        "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info",
        timeout=15,
    )
    data = r.json() if r.status_code == 200 else {}
    items = data.get("data", []) if isinstance(data, dict) else []
    return [
        it["contract_code"]
        for it in items
        if isinstance(it, dict)
        and it.get("contract_code")
        and it.get("contract_status") == 1
        and str(it.get("business_type", "swap")) in ("swap", "all")
    ]


def _htx_subscribe() -> list[str]:
    return [
        json.dumps({"sub": f"market.{code}.bbo", "id": code})
        for code in _htx_contract_codes()
    ]


def _htx_control(msg: Any) -> str | None:
    if isinstance(msg, dict) and "ping" in msg:
        return json.dumps({"pong": msg["ping"]})
    return None


def _parse_htx(msg: Any) -> list[_Update] | None:
    if not isinstance(msg, dict):
        return None
    ch = msg.get("ch", "")
    tick = msg.get("tick")
    if not ch.endswith(".bbo") or not isinstance(tick, dict):
        return None
    code = ch.split(".")[1] if ch.count(".") >= 2 else ""
    if not code:
        return None
    bid_arr = tick.get("bid") or []
    ask_arr = tick.get("ask") or []
    bid = _f(bid_arr[0]) if len(bid_arr) > 0 else None
    bq = _f(bid_arr[1]) if len(bid_arr) > 1 else None
    ask = _f(ask_arr[0]) if len(ask_arr) > 0 else None
    aq = _f(ask_arr[1]) if len(ask_arr) > 1 else None
    if not bid or not ask:
        return None
    sym = code.upper().replace("-", "")
    return [(sym, bid, bq or 0.0, ask, aq or 0.0)]


_feeds: dict[str, _Feed] = {
    "binance": _Feed(
        "binance", _BINANCE_FUTURES_WS_URL, _parse_binance, silent_timeout=10.0
    ),
    "okx": _Feed(
        "okx",
        _OKX_WS_URL,
        _parse_okx,
        subscribe=_okx_subscribe,
        ping_interval=20.0,
        ping_payload=lambda: "ping",
    ),
    "bybit": _Feed(
        "bybit",
        _BYBIT_WS_URL,
        _parse_bybit,
        subscribe=_bybit_subscribe,
        ping_interval=20.0,
        ping_payload=lambda: json.dumps({"op": "ping"}),
    ),
    "gateio": _Feed(
        "gateio",
        _GATEIO_WS_URL,
        _parse_gateio,
        subscribe=_gateio_subscribe,
        ping_interval=20.0,
        ping_payload=_gateio_ping,
    ),
    "bitget": _Feed(
        "bitget",
        _BITGET_WS_URL,
        _parse_bitget,
        subscribe=_bitget_subscribe,
        ping_interval=25.0,
        ping_payload=lambda: "ping",
    ),
    "htx": _Feed(
        "htx",
        _HTX_WS_URL,
        _parse_htx,
        subscribe=_htx_subscribe,
        control=_htx_control,
        gzip_frames=True,
    ),
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


def _okx_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.okx.client import OkxPublicClient, _normalize_symbol

    return {
        _normalize_symbol(t.symbol): (t.volume_24h_base, t.volume_24h_quote)
        for t in OkxPublicClient().book_tickers("SWAP")
    }


def _bybit_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.bybit.client import BybitPublicClient

    return {
        t.symbol: (t.volume_24h_base, t.volume_24h_quote)
        for t in BybitPublicClient().book_tickers("linear")
    }


def _gateio_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.gateio.client import GateioPublicClient, _normalize_symbol

    return {
        _normalize_symbol(t.symbol): (t.volume_24h_base, t.volume_24h_quote)
        for t in GateioPublicClient().book_tickers(market="futures")
    }


def _bitget_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.bitget.client import BitgetPublicClient

    return {
        t.symbol: (t.volume_24h_base or 0.0, t.volume_24h_quote or 0.0)
        for t in BitgetPublicClient().book_tickers()
    }


def _htx_volumes() -> dict[str, tuple[float, float]]:
    from mexc_monitor.htx.client import HtxPublicClient

    return {
        t.symbol: (t.volume_24h_base, t.volume_24h_quote)
        for t in HtxPublicClient().book_tickers(market="futures")
    }


_volume_loaders: dict[str, Callable[[], dict[str, tuple[float, float]]]] = {
    "binance": _binance_volumes,
    "okx": _okx_volumes,
    "bybit": _bybit_volumes,
    "gateio": _gateio_volumes,
    "bitget": _bitget_volumes,
    "htx": _htx_volumes,
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


def feeds_health() -> dict[str, dict[str, Any]]:
    """Состояние всех WS-фидов (для /api/health)."""
    from mexc_monitor.dydx.ws_feed import dydx_ws_health

    out = {name: feed.health() for name, feed in _feeds.items()}
    out["dydx"] = dydx_ws_health()
    return out


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
