"""WebSocket подписка на спотовые сделки MEXC (deals stream).

MEXC Spot WebSocket: wss://wbs.mexc.com/ws
Подписка: {"method":"SUBSCRIPTION","params":["spot@public.deals.v3.api@BTCUSDT"]}
Push (root): {"symbol":"BTCUSDT","sendTime":..., "publicDeals":{"deals":[
  {"price":"...","quantity":"...","tradeType":1|2,"time":"...","tradeId":"..."}, ...]}}
(парсер защитно принимает также обёртки publicAggreDeals / плоский deals).

Каждая сделка пишется в ``trade_buffer`` - источник плотности/имбаланса/VWAP
сделок для AI-ассессора и (в перспективе) фильтра скринера.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from mexc_monitor import trade_buffer

logger = logging.getLogger(__name__)

# MEXC рекомендует не более 30 подписок на одно соединение.
_MAX_SUBS_PER_CONNECTION = 30

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_active_symbols: tuple[str, ...] = ()


def _norm_spot_symbol(s: str) -> str:
    return s.strip().upper()


def effective_spot_deals_symbols(settings: Any) -> tuple[str, ...]:
    """Нормализованные символы для подписки: явный список или первые 30 whitelist."""
    explicit = tuple(
        _norm_spot_symbol(s) for s in settings.spot_deals_ws_symbols if str(s).strip()
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
                "spot deals WS: список усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(out[:_MAX_SUBS_PER_CONNECTION])
    if settings.spot_deals_ws_enabled and settings.spot_symbols_whitelist:
        wl = list(settings.spot_symbols_whitelist)
        if len(wl) > _MAX_SUBS_PER_CONNECTION:
            logger.warning(
                "spot deals WS: whitelist усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(wl[:_MAX_SUBS_PER_CONNECTION])
    return ()


def _extract_deals(payload: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Достать (symbol, deals[]) из push'а, защитно принимая разные обёртки."""
    data = payload.get("d")
    if not isinstance(data, dict):
        data = payload.get("data")
    if not isinstance(data, dict):
        data = payload

    symbol = (
        payload.get("symbol")
        or payload.get("s")
        or data.get("symbol")
        or data.get("s")
    )
    if not symbol:
        channel = payload.get("channel") or payload.get("c") or data.get("channel")
        if isinstance(channel, str) and "@" in channel:
            symbol = channel.rsplit("@", 1)[-1]

    deals: list[dict[str, Any]] | None = None
    for key in ("publicDeals", "publicAggreDeals", "deals"):
        node = data.get(key)
        if isinstance(node, dict) and isinstance(node.get("deals"), list):
            deals = [d for d in node["deals"] if isinstance(d, dict)]
            break
        if isinstance(node, list):
            deals = [d for d in node if isinstance(d, dict)]
            break

    return (str(symbol).upper() if symbol else None, deals or [])


def _handle_message(text: str) -> None:
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return
    if msg.get("msg") in ("PONG", "pong"):
        return

    symbol, deals = _extract_deals(msg)
    if not symbol or not deals:
        return

    for d in deals:
        try:
            price = float(d.get("price", 0) or 0)
            quantity = float(d.get("quantity", 0) or 0)
            side = int(d.get("tradeType") or d.get("Side") or 0)
            ts_ms = int(d.get("time") or d.get("T") or 0)
        except (TypeError, ValueError):
            continue
        trade_buffer.push_trade(symbol, price, quantity, side, ts_ms or None)


def _run_loop(url: str, symbols: tuple[str, ...], connect_timeout: float) -> None:
    try:
        import websocket
        from websocket import WebSocketBadStatusException
    except ImportError:
        logger.error("websocket-client missing; pip install websocket-client")
        return

    reconnect_delay = 1.0
    params = [f"spot@public.deals.v3.api@{sym}" for sym in symbols]

    while not _stop.is_set():
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=connect_timeout)
            ws.settimeout(25.0)
            ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
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
                _handle_message(raw)
        except WebSocketBadStatusException as e:
            code = int(e.status_code)
            logger.warning(
                "Spot deals WSS HTTP %s; retry in %.1fs",
                code,
                reconnect_delay,
            )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        except Exception:
            logger.exception("Spot deals WebSocket error; retry in %.1fs", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass


def ensure_spot_deals_ws_started(settings: Any) -> None:
    """Идемпотентный фоновый поток подписок deals (спот)."""
    global _thread, _active_symbols

    if not settings.spot_deals_ws_enabled:
        return

    symbols = effective_spot_deals_symbols(settings)
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
            name="mexc-spot-deals-ws",
        )
        _thread = t

    logger.info(
        "Spot deals WS: подписка deals на %s символ(ов): %s",
        len(symbols),
        ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
    )
    t.start()


def stop_spot_deals_ws() -> None:
    global _thread
    _stop.set()
    t = _thread
    _thread = None
    if t is not None and t.is_alive():
        t.join(timeout=5.0)
