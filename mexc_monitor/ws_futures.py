from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from mexc_monitor.futures_rows import futures_ticker_item_to_row
from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rows: list[BookTickerRow] = []
_last_push_mono: float = 0.0
_stop = threading.Event()
_thread: threading.Thread | None = None


def _set_snapshot(rows: list[BookTickerRow], *, mono: float) -> None:
    global _rows, _last_push_mono
    with _lock:
        _rows = rows
        _last_push_mono = mono


def try_get_cached_rows(*, max_age_sec: float) -> list[BookTickerRow] | None:
    """Строки с фьючерсного WS, если буфер свежий; иначе None (нужен REST fallback)."""
    with _lock:
        if not _rows:
            return None
        if (time.monotonic() - _last_push_mono) > max_age_sec:
            return None
        return list(_rows)


def futures_ws_handshake_403(url: str) -> bool:
    """Пробный WSS handshake; True если ответ HTTP 403 (часто Akamai / блок IP)."""
    try:
        import websocket
        from websocket import WebSocketBadStatusException
    except ImportError:
        return False
    try:
        ws = websocket.create_connection(url, timeout=8.0)
        try:
            ws.close()
        except Exception:
            pass
        return False
    except WebSocketBadStatusException as e:
        return int(e.status_code) == 403
    except Exception:
        return False


def _parse_tickers_message(raw: str) -> list[BookTickerRow] | None:
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("channel") != "push.tickers":
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    out: list[BookTickerRow] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = futures_ticker_item_to_row(item)
        if row is not None:
            out.append(row)
    return out if out else None


def _run_loop(url: str, connect_timeout: float) -> None:
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
            ws.send(
                json.dumps({"method": "sub.tickers", "param": {}, "gzip": False}),
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
                if not isinstance(raw, str):
                    continue
                if raw == "ping" or raw == "pong":
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("channel") == "pong":
                    continue
                rows = _parse_tickers_message(raw)
                if rows is not None:
                    _set_snapshot(rows, mono=time.monotonic())
        except WebSocketBadStatusException as e:
            code = int(e.status_code)
            if code == 403:
                logger.warning(
                    "Futures WSS HTTP 403 (Akamai / IP or geo block on contract.mexc.com). "
                    "Not an API-key issue. Retry in %.1fs — use VPN or another network.",
                    reconnect_delay,
                )
            else:
                logger.warning(
                    "Futures WSS handshake HTTP %s; retry in %.1fs",
                    code,
                    reconnect_delay,
                )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        except Exception:
            logger.exception("Futures WebSocket error; retry in %.1fs", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass


def start_futures_tickers_ws(url: str, *, connect_timeout: float = 30.0) -> None:
    """Идемпотентный запуск фонового потока (один на процесс)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        t = threading.Thread(
            target=_run_loop,
            args=(url, connect_timeout),
            daemon=True,
            name="mexc-futures-ws",
        )
        _thread = t
    t.start()


def stop_futures_tickers_ws() -> None:
    _stop.set()


def ensure_started_from_settings(settings) -> None:
    """settings: mexc_monitor.config.Settings — избегаем циклического импорта через строку в аннотации не нужна."""
    if settings.futures_ticker_source != "websocket":
        return
    start_futures_tickers_ws(
        settings.futures_ws_url,
        connect_timeout=max(5.0, float(settings.timeout_sec)),
    )
