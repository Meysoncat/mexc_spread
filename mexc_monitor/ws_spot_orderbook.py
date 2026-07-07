"""
WebSocket подписка на спотовые стаканы MEXC (bookTicker stream).

MEXC Spot WebSocket: wss://wbs.mexc.com/ws
Подписка: {"method":"SUBSCRIPTION","params":["spot@public.bookTicker.v3.api@BTCUSDT"]}
Push: {"s":"BTCUSDT","S":1,"b":"...","B":"...","a":"...","A":"..."}

Модуль хранит L1 (bid, ask, bid_qty, ask_qty) по каждому подписанному символу
и позволяет подменять данные в BookTickerRow при сборе снимка — аналогично
ws_futures_orderbook для фьючерсов.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any

from mexc_monitor.metrics import compute_mid_spread
from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

# MEXC spot WS: рекомендуется не более 30 подписок на одно соединение.
_MAX_SUBS_PER_CONNECTION = 30

_lock = threading.Lock()
# symbol (upper) -> (bid_price, ask_price, bid_qty, ask_qty)
_tops: dict[str, tuple[float, float, float, float]] = {}
_last_mono: dict[str, float] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None
_active_symbols: tuple[str, ...] = ()


def _norm_spot_symbol(s: str) -> str:
    return s.strip().upper()


def effective_spot_orderbook_symbols(settings: Any) -> tuple[str, ...]:
    """
    Нормализованные символы для подписки.
    Явный список из настроек или первые 30 из spot whitelist.
    """
    explicit = tuple(
        _norm_spot_symbol(s)
        for s in settings.spot_orderbook_ws_symbols
        if str(s).strip()
    )
    if explicit:
        seen: set[str] = set()
        out: list[str] = []
        for s in explicit:
            if s not in seen:
                seen.add(s)
                out.append(s)
        if len(out) > _MAX_SUBS_PER_CONNECTION:
            logger.warning(
                "spot orderbook WS: список усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(out[:_MAX_SUBS_PER_CONNECTION])
    if settings.spot_orderbook_ws_enabled and settings.spot_symbols_whitelist:
        wl = list(settings.spot_symbols_whitelist)
        if len(wl) > _MAX_SUBS_PER_CONNECTION:
            logger.warning(
                "spot orderbook WS: whitelist усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(wl[:_MAX_SUBS_PER_CONNECTION])
    return ()


def _parse_book_ticker_push(payload: dict[str, Any]) -> tuple[str, float, float, float, float] | None:
    """
    Парсинг push-сообщения bookTicker от MEXC spot WS.
    Формат: {"c":"spot@public.bookTicker.v3.api@BTCUSDT","d":{"s":"BTCUSDT","b":"...","B":"...","a":"...","A":"..."}}
    или напрямую: {"s":"BTCUSDT","b":"...","B":"...","a":"...","A":"..."}
    """
    # Вариант 1: обёрнутый формат с channel + data
    data = payload.get("d")
    if isinstance(data, dict):
        return _extract_top(data)
    # Вариант 2: плоский формат
    if "s" in payload and "b" in payload and "a" in payload:
        return _extract_top(payload)
    return None


def _extract_top(data: dict[str, Any]) -> tuple[str, float, float, float, float] | None:
    sym = data.get("s") or data.get("symbol")
    if not sym or not isinstance(sym, str):
        return None
    try:
        bid_p = float(data.get("b") or data.get("bidPrice") or 0)
        ask_p = float(data.get("a") or data.get("askPrice") or 0)
        bid_q = float(data.get("B") or data.get("bidQty") or 0)
        ask_q = float(data.get("A") or data.get("askQty") or 0)
    except (TypeError, ValueError):
        return None
    if bid_p <= 0 or ask_p <= 0 or ask_p < bid_p:
        return None
    return sym.upper(), bid_p, ask_p, bid_q, ask_q


def _apply_push(symbol: str, bid: float, ask: float, bid_qty: float, ask_qty: float) -> None:
    mono = time.monotonic()
    with _lock:
        _tops[symbol] = (bid, ask, bid_qty, ask_qty)
        _last_mono[symbol] = mono
    # Пишем в ring buffer для графиков спреда и SSE
    try:
        from mexc_monitor.spread_buffer import push_tick
        push_tick(symbol, bid, ask, bid_qty, ask_qty)
    except Exception:
        pass


def get_fresh_spot_tops(*, max_age_sec: float) -> dict[str, tuple[float, float, float, float]]:
    """L1 из WS: bid, ask, bid_qty, ask_qty — только непротухшие."""
    now = time.monotonic()
    with _lock:
        return {
            sym: tops
            for sym, tops in _tops.items()
            if now - _last_mono.get(sym, 0.0) <= max_age_sec
        }


def apply_spot_depth_top_to_rows(
    rows: list[BookTickerRow],
    settings: Any,
) -> list[BookTickerRow]:
    """Подмена bid/ask/qty с L1 WS-стакана для спотовых строк."""
    if not settings.spot_orderbook_ws_enabled:
        return rows
    tops = get_fresh_spot_tops(max_age_sec=settings.spot_orderbook_ws_stale_after_sec)
    if not tops:
        return rows
    out: list[BookTickerRow] = []
    for r in rows:
        t = tops.get(r.symbol)
        if t is None:
            out.append(r)
            continue
        bid, ask, bq, aq = t
        mid, spread_abs, spread_bps = compute_mid_spread(bid, ask)
        out.append(
            replace(
                r,
                bid=bid,
                ask=ask,
                bid_qty=bq,
                ask_qty=aq,
                mid=mid,
                spread_abs=spread_abs,
                spread_bps=spread_bps,
            ),
        )
    return out


def _run_loop(url: str, symbols: tuple[str, ...], connect_timeout: float) -> None:
    try:
        import websocket
        from websocket import WebSocketBadStatusException
    except ImportError:
        logger.error("websocket-client missing; pip install websocket-client")
        return

    reconnect_delay = 1.0
    while not _stop.is_set():
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=connect_timeout)
            ws.settimeout(25.0)
            # Подписка на bookTicker для каждого символа
            params = [f"spot@public.bookTicker.v3.api@{sym}" for sym in symbols]
            ws.send(
                json.dumps({"method": "SUBSCRIPTION", "params": params}),
            )
            reconnect_delay = 1.0
            last_ping = time.monotonic()
            while not _stop.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.monotonic() - last_ping >= 20.0:
                        ws.send(json.dumps({"method": "PING"}))
                        last_ping = time.monotonic()
                    continue
                except websocket.WebSocketConnectionClosedException:
                    break
                except OSError:
                    break
                if not isinstance(raw, str):
                    continue
                if raw in ("ping", "pong", "PONG"):
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                # Пропускаем pong и подтверждения подписки
                msg_type = obj.get("msg")
                if msg_type in ("PONG", "pong"):
                    continue
                # Парсим bookTicker push
                parsed = _parse_book_ticker_push(obj)
                if parsed is not None:
                    sym, bid, ask, bq, aq = parsed
                    _apply_push(sym, bid, ask, bq, aq)
        except WebSocketBadStatusException as e:
            code = int(e.status_code)
            logger.warning(
                "Spot orderbook WSS HTTP %s; retry in %.1fs",
                code,
                reconnect_delay,
            )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        except Exception:
            logger.exception(
                "Spot orderbook WebSocket error; retry in %.1fs",
                reconnect_delay,
            )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass


def stop_spot_orderbook_ws() -> None:
    _stop.set()


def ensure_spot_orderbook_ws_started(settings: Any) -> None:
    """Идемпотентный фоновый поток подписок bookTicker (спот)."""
    global _thread, _active_symbols

    if not settings.spot_orderbook_ws_enabled:
        return

    symbols = effective_spot_orderbook_symbols(settings)
    if not symbols:
        return

    with _lock:
        if (
            _thread is not None
            and _thread.is_alive()
            and symbols == _active_symbols
        ):
            return

        if _thread is not None and _thread.is_alive():
            _stop.set()
            _thread.join(timeout=8.0)

        _stop.clear()
        _active_symbols = symbols
        t = threading.Thread(
            target=_run_loop,
            args=(
                settings.spot_ws_url,
                symbols,
                max(5.0, float(settings.timeout_sec)),
            ),
            daemon=True,
            name="mexc-spot-depth-ws",
        )
        _thread = t

    logger.info(
        "Spot orderbook WS: подписка bookTicker на %s символ(ов): %s",
        len(symbols),
        ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
    )
    t.start()
