from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from dataclasses import replace
from typing import Any

from mexc_monitor.config import Settings, _norm_futures_symbol
from mexc_monitor.metrics import compute_mid_spread
from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

# Ограничение MEXC: до ~30 подписок на одно соединение.
_MAX_SUBS_PER_CONNECTION = 30

_lock = threading.Lock()
_tops: dict[str, tuple[float, float, float, float]] = {}
_last_mono: dict[str, float] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None
_active_symbols: tuple[str, ...] = ()


def effective_orderbook_symbols(settings: Settings) -> tuple[str, ...]:
    """Нормализованные BTC_USDT; явный список или первые 30 из whitelist фьючерсов."""
    explicit = tuple(
        _norm_futures_symbol(s)
        for s in settings.futures_orderbook_ws_symbols
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
                "futures orderbook WS: явный список усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(out[:_MAX_SUBS_PER_CONNECTION])
    if settings.futures_orderbook_ws_enabled and settings.futures_symbols_whitelist:
        wl = list(settings.futures_symbols_whitelist)
        if len(wl) > _MAX_SUBS_PER_CONNECTION:
            logger.warning(
                "futures orderbook WS: whitelist усечён до %s символов (лимит соединения)",
                _MAX_SUBS_PER_CONNECTION,
            )
        return tuple(wl[:_MAX_SUBS_PER_CONNECTION])
    return ()


def _parse_depth_top(data: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(data, dict):
        return None
    bids = data.get("bids")
    asks = data.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    if not bids or not asks:
        return None
    b0, a0 = bids[0], asks[0]
    if not isinstance(b0, (list, tuple)) or not isinstance(a0, (list, tuple)):
        return None
    if len(b0) < 3 or len(a0) < 3:
        return None
    try:
        bid_p = float(b0[0])
        bid_q = float(b0[2])
        ask_p = float(a0[0])
        ask_q = float(a0[2])
    except (TypeError, ValueError):
        return None
    if bid_p <= 0 or ask_p <= 0 or ask_p < bid_p:
        return None
    return bid_p, ask_p, bid_q, ask_q


def _apply_push_depth(payload: dict[str, Any]) -> None:
    if payload.get("channel") != "push.depth":
        return
    sym = payload.get("symbol")
    if not sym or not isinstance(sym, str):
        return
    data = payload.get("data")
    top = _parse_depth_top(data)
    if top is None:
        return
    sym_n = _norm_futures_symbol(sym)
    mono = time.monotonic()
    with _lock:
        _tops[sym_n] = top
        _last_mono[sym_n] = mono
    # Пишем в ring buffer для графиков спреда и SSE
    try:
        bid_p, ask_p, bid_q, ask_q = top
        from mexc_monitor.spread_buffer import push_tick
        push_tick(sym_n, bid_p, ask_p, bid_q, ask_q)
    except Exception:
        pass


def _decode_ws_payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, (bytes, bytearray)):
        blob = bytes(raw)
        try:
            text = gzip.decompress(blob).decode("utf-8")
        except Exception:
            try:
                text = blob.decode("utf-8")
            except Exception:
                return None
    else:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def get_fresh_depth_tops(*, max_age_sec: float) -> dict[str, tuple[float, float, float, float]]:
    """L1 из стакана: bid, ask, bid_qty, ask_qty — только непротухшие."""
    now = time.monotonic()
    with _lock:
        return {
            sym: tops
            for sym, tops in _tops.items()
            if now - _last_mono.get(sym, 0.0) <= max_age_sec
        }


def apply_futures_depth_top_to_rows(
    rows: list[BookTickerRow],
    settings: Settings,
) -> list[BookTickerRow]:
    """Подмена bid/ask/qty с L1 WS-стакана там, где есть свежий снимок."""
    if not settings.futures_orderbook_ws_enabled:
        return rows
    tops = get_fresh_depth_tops(max_age_sec=settings.futures_orderbook_ws_stale_after_sec)
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
            for sym in symbols:
                ws.send(
                    json.dumps(
                        {
                            "method": "sub.depth",
                            "param": {"symbol": sym},
                            "gzip": False,
                        },
                    ),
                )
            reconnect_delay = 1.0
            last_ping = time.monotonic()
            while not _stop.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.monotonic() - last_ping >= 12.0:
                        ws.send(json.dumps({"method": "ping"}))
                        last_ping = time.monotonic()
                    continue
                except websocket.WebSocketConnectionClosedException:
                    break
                except OSError:
                    break
                if raw in ("ping", "pong"):
                    continue
                obj = _decode_ws_payload(raw)
                if obj is None:
                    continue
                if obj.get("channel") == "pong":
                    continue
                _apply_push_depth(obj)
        except WebSocketBadStatusException as e:
            code = int(e.status_code)
            if code == 403:
                logger.warning(
                    "Futures orderbook WSS HTTP 403 (Akamai / IP block). Retry in %.1fs.",
                    reconnect_delay,
                )
            else:
                logger.warning(
                    "Futures orderbook WSS HTTP %s; retry in %.1fs",
                    code,
                    reconnect_delay,
                )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        except Exception:
            logger.exception(
                "Futures orderbook WebSocket error; retry in %.1fs",
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


def stop_futures_orderbook_ws() -> None:
    _stop.set()


def ensure_futures_orderbook_ws_started(settings: Settings) -> None:
    """Идемпотентный фоновый поток подписок sub.depth (отдельное соединение от тикеров)."""
    global _thread, _active_symbols

    symbols = effective_orderbook_symbols(settings)
    if not settings.futures_orderbook_ws_enabled or not symbols:
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
                settings.futures_ws_url,
                symbols,
                max(5.0, float(settings.timeout_sec)),
            ),
            daemon=True,
            name="mexc-futures-depth-ws",
        )
        _thread = t

    logger.info(
        "Futures orderbook WS: подписка sub.depth на %s символ(ов)",
        len(symbols),
    )
    t.start()
