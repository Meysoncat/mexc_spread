"""
Полная L2-книга фьючерсов MEXC по WebSocket (канал ``sub.depth.full``).

Зачем отдельно от :mod:`mexc_monitor.ws_futures_orderbook`:
- ``ws_futures_orderbook`` подписан на ``sub.depth`` — это **инкрементальные
  дельты**, из которых модуль достаёт только L1 (top-of-book) для снапшота
  тикеров и графиков спреда. Собрать из дельт полный стакан без версионной
  реконструкции нельзя.
- Density Monitor и эндпоинты ``/api/density/*`` требуют **полную L2-глубину**
  и сейчас тянут её по REST (``fetch_orderbook_depth``). REST у MEXC часто
  геоблокирован (HTTP 451), а WS — жив.

Канал ``sub.depth.full`` присылает **самодостаточный снапшот топ-N уровней**
(N = 5/10/20) в каждом пуше — идеально для детекции стен без reassembly.

Формат уровня MEXC contract: ``[price, volume, order_count]`` — для notional
берём ``volume`` (индекс 1, объём в контрактах), а не число ордеров.
``get_fresh_depth_book`` отдаёт данные в том же виде, что и REST-путь
(``mexc_monitor.orderbook.fetch_orderbook_depth``), поэтому density-эндпоинты
подключаются без изменений в логике анализа.

Отдельное соединение (как spot/futures L1 уже сделаны отдельными модулями)
изолирует символьный набор и capacity этого фида от L1.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from typing import Any

from mexc_monitor.config import Settings, _norm_futures_symbol

logger = logging.getLogger(__name__)

# Канал full-depth поддерживает только 5/10/20 уровней. 20 — максимум.
_DEPTH_LIMIT = 20
# MEXC: до ~30 подписок на соединение (у нас 1 sub/символ → до 30 символов).
_MAX_SUBS_PER_CONNECTION = 30

_lock = threading.Lock()
# symbol (BTC_USDT) -> (version, ts_mono, bids, asks); bids/asks = list[(price, qty)]
_books: dict[str, tuple[int, float, list[tuple[float, float]], list[tuple[float, float]]]] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None
_active_symbols: tuple[str, ...] = ()

# Динамический watchlist (как в ws_spot_orderbook): скринер/монитор помечают
# интересные символы, они подмешиваются в подписку на ближайшем throttled
# reconcile. Sticky с TTL, чтобы подписка не «дребезжала».
_watchlist_seen: dict[str, float] = {}
_WATCHLIST_TTL_SEC: float = 120.0
_RECONCILE_MIN_INTERVAL_SEC: float = 20.0
_last_reconcile_mono: float = 0.0

# ── Размеры контрактов ────────────────────────────────────────────────────
# WS-объём указан в контрактах; истинный USDT-notional = price × vol ×
# contractSize. Эндпоинт contract/detail доступен даже там, где market-data
# REST геоблокирован (проверено), поэтому размеры тянем и кешируем отдельно.
_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
_CONTRACT_SIZES_TTL_SEC: float = 3600.0
_contract_sizes: dict[str, float] = {}
_contract_sizes_ts: float = 0.0
_contract_sizes_lock = threading.Lock()


def _load_contract_sizes(*, force: bool = False) -> None:
    """Загрузить и закешировать contractSize для всех фьючерсов (best-effort)."""
    global _contract_sizes_ts
    now = time.monotonic()
    with _contract_sizes_lock:
        fresh = _contract_sizes and (now - _contract_sizes_ts) < _CONTRACT_SIZES_TTL_SEC
    if fresh and not force:
        return
    try:
        import httpx

        r = httpx.get(_CONTRACT_DETAIL_URL, timeout=10)
        r.raise_for_status()
        data = r.json().get("data")
    except Exception as e:  # noqa: BLE001 — размеры опциональны, деградируем к 1.0
        logger.warning("depth-book: не удалось загрузить contractSize: %s", e)
        return
    if not isinstance(data, list):
        return
    sizes: dict[str, float] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        sym = row.get("symbol")
        cs = row.get("contractSize")
        if not sym or cs is None:
            continue
        try:
            size = float(cs)
        except (TypeError, ValueError):
            continue
        if size > 0:
            sizes[str(sym).upper()] = size
    if sizes:
        with _contract_sizes_lock:
            _contract_sizes.clear()
            _contract_sizes.update(sizes)
            _contract_sizes_ts = now
        logger.info("depth-book: загружено %d размеров контрактов", len(sizes))


def contract_size(symbol: str) -> float:
    """contractSize для символа (base-единиц на 1 контракт). 1.0 если неизвестно."""
    sym = _norm_futures_symbol(symbol)
    with _contract_sizes_lock:
        cs = _contract_sizes.get(sym)
    if cs is None:
        _load_contract_sizes()
        with _contract_sizes_lock:
            cs = _contract_sizes.get(sym)
    return cs if cs and cs > 0 else 1.0


def effective_depth_book_symbols(settings: Settings) -> tuple[str, ...]:
    """Базовый набор: явный список orderbook-символов или whitelist фьючерсов."""
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
        return tuple(out[:_MAX_SUBS_PER_CONNECTION])
    if settings.futures_symbols_whitelist:
        wl = list(settings.futures_symbols_whitelist)
        return tuple(wl[:_MAX_SUBS_PER_CONNECTION])
    return ()


def touch_watchlist(symbols: Any) -> None:
    """Пометить символы как интересные — подмешаются в подписку на reconcile."""
    now = time.monotonic()
    cutoff = now - _WATCHLIST_TTL_SEC
    with _lock:
        for s in symbols or ():
            sym = _norm_futures_symbol(str(s))
            if sym:
                _watchlist_seen[sym] = now
        stale = [k for k, t in _watchlist_seen.items() if t < cutoff]
        for k in stale:
            del _watchlist_seen[k]


def desired_depth_book_symbols(settings: Settings) -> tuple[str, ...]:
    """Базовый набор ∪ свежий watchlist, с ограничением по соединению."""
    base = effective_depth_book_symbols(settings)
    cutoff = time.monotonic() - _WATCHLIST_TTL_SEC
    with _lock:
        fresh = [s for s, t in _watchlist_seen.items() if t >= cutoff]
    out: list[str] = list(base)
    seen = set(out)
    for s in fresh:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return tuple(out[:_MAX_SUBS_PER_CONNECTION])


def _parse_levels(raw: Any) -> list[tuple[float, float]]:
    """[[price, volume, order_count], ...] -> [(price, volume), ...].

    qty = volume (индекс 1). Отбрасываем нулевые/битые уровни.
    """
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, float]] = []
    for lv in raw:
        if not isinstance(lv, (list, tuple)) or len(lv) < 2:
            continue
        try:
            price = float(lv[0])
            qty = float(lv[1])
        except (TypeError, ValueError):
            continue
        if price > 0 and qty > 0:
            out.append((price, qty))
    return out


def _apply_full_depth(payload: dict[str, Any]) -> None:
    if payload.get("channel") != "push.depth.full":
        return
    sym = payload.get("symbol")
    if not sym or not isinstance(sym, str):
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    bids = _parse_levels(data.get("bids"))
    asks = _parse_levels(data.get("asks"))
    if not bids or not asks:
        return
    try:
        version = int(data.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    sym_n = _norm_futures_symbol(sym)
    with _lock:
        _books[sym_n] = (version, time.monotonic(), bids, asks)


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


def get_fresh_depth_book(symbol: str, *, max_age_sec: float) -> dict[str, Any] | None:
    """Полная L2-книга в REST-совместимом виде или ``None`` если протухла/нет.

    Формат совпадает с :func:`mexc_monitor.orderbook.fetch_orderbook_depth`
    (market='futures'), поэтому density-эндпоинты потребляют его напрямую.
    """
    sym_n = _norm_futures_symbol(symbol)
    now = time.monotonic()
    with _lock:
        entry = _books.get(sym_n)
        if entry is None:
            return None
        version, ts_mono, bids, asks = entry
        if now - ts_mono > max_age_sec:
            return None
        bids = list(bids)
        asks = list(asks)
    # Объём в контрактах → базовый актив, чтобы notional был в истинных USDT
    # (сопоставимо со spot / Binance-fallback). cs=1.0 если размер неизвестен.
    cs = contract_size(sym_n)
    bids_out = [{"price": p, "qty": q * cs, "notional": p * q * cs} for p, q in bids]
    asks_out = [{"price": p, "qty": q * cs, "notional": p * q * cs} for p, q in asks]
    best_bid = bids_out[0]["price"] if bids_out else None
    best_ask = asks_out[0]["price"] if asks_out else None
    mid = None
    if best_bid is not None and best_ask is not None:
        mid = (best_bid + best_ask) / 2.0
    return {
        "market": "futures",
        "symbol": sym_n,
        "limit": _DEPTH_LIMIT,
        "last_update_id": None,
        "version": version,
        "timestamp_ms": None,
        "bids": bids_out,
        "asks": asks_out,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "contract_size": cs,
        "source": "ws",
    }


def depth_book_health(*, max_age_sec: float = 5.0) -> dict[str, Any]:
    """Диагностика: какие символы имеют свежую WS-книгу."""
    now = time.monotonic()
    with _lock:
        active = _active_symbols
        symbols = {
            sym: {
                "version": version,
                "age_sec": round(now - ts_mono, 2),
                "bid_levels": len(bids),
                "ask_levels": len(asks),
                "fresh": (now - ts_mono) <= max_age_sec,
            }
            for sym, (version, ts_mono, bids, asks) in _books.items()
        }
    fresh = sum(1 for s in symbols.values() if s["fresh"])
    return {
        "subscribed": list(active),
        "tracked": len(symbols),
        "fresh": fresh,
        "symbols": symbols,
        "live": fresh > 0,
    }


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
                            "method": "sub.depth.full",
                            "param": {"symbol": sym, "limit": _DEPTH_LIMIT},
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
                _apply_full_depth(obj)
        except WebSocketBadStatusException as e:
            code = int(e.status_code)
            if code == 403:
                logger.warning(
                    "Futures depth-book WSS HTTP 403 (Akamai / IP block). Retry in %.1fs.",
                    reconnect_delay,
                )
            else:
                logger.warning(
                    "Futures depth-book WSS HTTP %s; retry in %.1fs",
                    code,
                    reconnect_delay,
                )
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        except Exception:
            logger.exception(
                "Futures depth-book WebSocket error; retry in %.1fs",
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


def stop_futures_depth_book_ws() -> None:
    _stop.set()


def ensure_futures_depth_book_ws_started(settings: Settings) -> None:
    """Идемпотентный фоновый поток подписок ``sub.depth.full``.

    Гейтится тем же флагом, что и L1-фид (``futures_orderbook_ws_enabled``):
    включение фьючерсного orderbook WS включает и полную L2-книгу.
    """
    global _thread, _active_symbols

    if not settings.futures_orderbook_ws_enabled:
        return

    symbols = desired_depth_book_symbols(settings)
    if not symbols:
        return

    # Прогрев размеров контрактов, чтобы первый density-запрос сразу дал USDT.
    _load_contract_sizes()

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
            name="mexc-futures-depth-book-ws",
        )
        _thread = t

    logger.info(
        "Futures depth-book WS: подписка sub.depth.full (limit=%d) на %d символ(ов): %s",
        _DEPTH_LIMIT,
        len(symbols),
        ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
    )
    t.start()


def reconcile_futures_depth_book_ws(settings: Settings | None = None) -> None:
    """Пересобрать подписку под base ∪ свежий watchlist (throttled)."""
    global _last_reconcile_mono
    if settings is None:
        from mexc_monitor.config import DEFAULT_SETTINGS  # noqa: PLC0415

        settings = DEFAULT_SETTINGS
    if not settings.futures_orderbook_ws_enabled:
        return
    desired = desired_depth_book_symbols(settings)
    if not desired:
        return
    now = time.monotonic()
    with _lock:
        changed = set(desired) != set(_active_symbols)
        too_soon = (now - _last_reconcile_mono) < _RECONCILE_MIN_INTERVAL_SEC
    if not changed or too_soon:
        return
    with _lock:
        _last_reconcile_mono = now
    logger.info(
        "Futures depth-book WS: reconcile watchlist → %d symbol(s)",
        len(desired),
    )
    ensure_futures_depth_book_ws_started(settings)
