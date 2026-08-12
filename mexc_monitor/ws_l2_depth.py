"""
Полная L2-книга OKX и Bybit по WebSocket (снапшот + инкрементальные дельты).

Зачем: density-эндпоинты (`/api/density/*`, Density Monitor) для не-MEXC бирж
падают на Binance REST, который геоблокируется (HTTP 451) → density пуст. У
OKX и Bybit публичный WS жив и отдаёт глубокий стакан. По аналогии с
:mod:`mexc_monitor.ws_futures_depth_book` (MEXC ``sub.depth.full``) собираем
здесь полную книгу и отдаём её в REST-совместимом виде.

Отличие от MEXC: там канал шлёт самодостаточный снапшот в каждом пуше, здесь —
**снапшот + дельты**, поэтому книгу нужно поддерживать (reassembly):

- **OKX** ``books`` (``wss://ws.okx.com:8443/ws/v5/public``): первый пуш
  ``action=snapshot`` (400 уровней), далее ``action=update`` с изменениями.
  Уровень ``[price, size, "0", order_count]``; ``size`` — в контрактах,
  поэтому истинный USDT = ``price × size × ctVal`` (contract value в базовой
  монете, из ``/api/v5/public/instruments``; кешируется).
- **Bybit** ``orderbook.50`` (``wss://stream.bybit.com/v5/public/linear``):
  первый пуш ``type=snapshot`` (50 уровней), далее ``type=delta``. Уровень
  ``[price, size]``; для linear USDT-перпов ``size`` уже в базовой монете,
  поэтому USDT = ``price × size`` (множитель 1.0).

Дельта-правило у обеих бирж одинаковое: уровень с ``size == 0`` удаляется,
иначе — вставка/замена по цене.

Канонический символ — ``BTCUSDT`` (верхний регистр, без разделителя): именно в
таком виде символы приходят из snapshot-строк OKX/Bybit и дефолтов
density-эндпоинтов. Маппинг в нативный формат делает адаптер биржи.

Важно про геоблок (из песочницы Vercel):

- **OKX** — и WS, и REST (``OkxPublicClient``: список инструментов, ctVal,
  объёмы) доступны, поэтому density-overview для OKX работает полностью и
  считается из WS-книги.
- **Bybit** — WS-хост ``stream.bybit.com`` **жив** (эта книга собирается по
  нему), но REST ``api.bybit.com`` / зеркало ``api.bytick.com`` отдают
  CloudFront ``403`` по стране. Список символов и 24h-объёмы для overview
  берутся именно из REST (``BybitPublicClient``), поэтому Bybit-overview из
  песочницы стартовать не может. Сама L2-книга при этом рабочая: как только
  символы заданы (через watchlist/overview при живом REST или напрямую),
  notional считается корректно. Практический обход — прокси в неблокируемом
  регионе (см. ``docs/PROXY_XRAY.md``): при нём Bybit REST оживает и overview
  начинает питаться из этой WS-книги.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Сколько уровней отдаём наружу (density-анализу больше и не нужно).
_OUTPUT_LIMIT = 50
_MAX_SUBS_PER_CONNECTION = 40
_WATCHLIST_TTL_SEC = 120.0
_RECONCILE_MIN_INTERVAL_SEC = 20.0

_OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_BYBIT_DEPTH = 50

# Известные котируемые валюты для разбора BASEQUOTE → (base, quote).
_QUOTES = ("USDT", "USDC", "USD")


def canonical_symbol(sym: str) -> str:
    """Привести к каноническому виду ``BTCUSDT`` (upper, без -/_)."""
    return str(sym or "").upper().replace("-", "").replace("_", "").replace("SWAP", "")


def split_base_quote(canonical: str) -> tuple[str, str] | None:
    """``BTCUSDT`` → ``(BTC, USDT)``. None если суффикс неизвестен."""
    c = canonical_symbol(canonical)
    for q in _QUOTES:
        if c.endswith(q) and len(c) > len(q):
            return c[: -len(q)], q
    return None


# ── Contract value cache (OKX) ─────────────────────────────────────────────
_OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"
_CTVAL_TTL_SEC = 3600.0
_okx_ctval: dict[str, float] = {}
_okx_ctval_ts = 0.0
_okx_ctval_lock = threading.Lock()


def _load_okx_ctval(*, force: bool = False) -> None:
    """Закешировать ctVal (base-единиц на 1 контракт) для OKX SWAP."""
    global _okx_ctval_ts
    now = time.monotonic()
    with _okx_ctval_lock:
        fresh = _okx_ctval and (now - _okx_ctval_ts) < _CTVAL_TTL_SEC
    if fresh and not force:
        return
    try:
        import httpx

        r = httpx.get(_OKX_INSTRUMENTS_URL, params={"instType": "SWAP"}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as e:  # noqa: BLE001 — ctVal опционален, деградируем к 1.0
        logger.warning("l2-depth okx: не удалось загрузить ctVal: %s", e)
        return
    sizes: dict[str, float] = {}
    for row in data if isinstance(data, list) else ():
        if not isinstance(row, dict):
            continue
        inst = row.get("instId")
        ct = row.get("ctVal")
        if not inst or ct is None:
            continue
        try:
            v = float(ct)
        except (TypeError, ValueError):
            continue
        if v > 0:
            sizes[str(inst).upper()] = v
    if sizes:
        with _okx_ctval_lock:
            _okx_ctval.clear()
            _okx_ctval.update(sizes)
            _okx_ctval_ts = now
        logger.info("l2-depth okx: загружено %d ctVal", len(sizes))


def _okx_multiplier(canonical: str) -> float:
    """ctVal для канонического символа OKX (BTCUSDT → инстр. BTC-USDT-SWAP)."""
    native = _okx_to_native(canonical)
    with _okx_ctval_lock:
        v = _okx_ctval.get(native)
    if v is None:
        _load_okx_ctval()
        with _okx_ctval_lock:
            v = _okx_ctval.get(native)
    return v if v and v > 0 else 1.0


# ── Symbol mapping ─────────────────────────────────────────────────────────
def _okx_to_native(canonical: str) -> str:
    bq = split_base_quote(canonical)
    if not bq:
        return canonical_symbol(canonical)
    base, quote = bq
    return f"{base}-{quote}-SWAP"


def _okx_from_native(inst_id: str) -> str:
    # BTC-USDT-SWAP → BTCUSDT
    return canonical_symbol(inst_id)


def _bybit_to_native(canonical: str) -> str:
    return canonical_symbol(canonical)


def _bybit_from_native(sym: str) -> str:
    return canonical_symbol(sym)


# ── Parsed message shape ───────────────────────────────────────────────────
# (canonical_symbol, action, bids, asks) где action ∈ {"snapshot","delta"},
# bids/asks = list[(price, qty)].
_Parsed = tuple[str, str, list[tuple[float, float]], list[tuple[float, float]]]


def _parse_okx_levels(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for lv in raw:
        if not isinstance(lv, (list, tuple)) or len(lv) < 2:
            continue
        try:
            price = float(lv[0])
            qty = float(lv[1])
        except (TypeError, ValueError):
            continue
        if price > 0 and qty >= 0:  # qty==0 => удаление (для дельт)
            out.append((price, qty))
    return out


def _parse_okx(msg: Any) -> list[_Parsed]:
    if not isinstance(msg, dict):
        return []
    arg = msg.get("arg") or {}
    if arg.get("channel") != "books":
        return []
    action = msg.get("action") or "snapshot"
    data = msg.get("data")
    if not isinstance(data, list):
        return []
    inst = arg.get("instId", "")
    canon = _okx_from_native(inst)
    out: list[_Parsed] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        bids = _parse_okx_levels(d.get("bids"))
        asks = _parse_okx_levels(d.get("asks"))
        out.append((canon, "snapshot" if action == "snapshot" else "delta", bids, asks))
    return out


def _parse_bybit(msg: Any) -> list[_Parsed]:
    if not isinstance(msg, dict):
        return []
    topic = msg.get("topic", "")
    if not topic.startswith("orderbook."):
        return []
    typ = msg.get("type")
    data = msg.get("data")
    if not isinstance(data, dict):
        return []
    sym = data.get("s") or topic.rsplit(".", 1)[-1]
    canon = _bybit_from_native(sym)
    bids = _parse_okx_levels(data.get("b"))  # тот же формат [price, size]
    asks = _parse_okx_levels(data.get("a"))
    action = "snapshot" if typ == "snapshot" else "delta"
    return [(canon, action, bids, asks)]


@dataclass
class _Adapter:
    name: str
    ws_url: str
    to_native: Callable[[str], str]
    subscribe_msgs: Callable[[list[str]], list[str]]
    parse: Callable[[Any], list[_Parsed]]
    multiplier: Callable[[str], float]
    ping_payload: str
    ping_interval: float = 18.0
    warmup: Callable[[], None] | None = None


def _okx_subscribe(natives: list[str]) -> list[str]:
    args = [{"channel": "books", "instId": n} for n in natives]
    return [json.dumps({"op": "subscribe", "args": args})]


def _bybit_subscribe(natives: list[str]) -> list[str]:
    args = [f"orderbook.{_BYBIT_DEPTH}.{n}" for n in natives]
    # Bybit допускает до 10 topics на запрос — бьём на батчи.
    out: list[str] = []
    for i in range(0, len(args), 10):
        out.append(json.dumps({"op": "subscribe", "args": args[i : i + 10]}))
    return out


_ADAPTERS: dict[str, _Adapter] = {
    "okx": _Adapter(
        name="okx",
        ws_url=_OKX_WS_URL,
        to_native=_okx_to_native,
        subscribe_msgs=_okx_subscribe,
        parse=_parse_okx,
        multiplier=_okx_multiplier,
        ping_payload="ping",
        warmup=_load_okx_ctval,
    ),
    "bybit": _Adapter(
        name="bybit",
        ws_url=_BYBIT_WS_URL,
        to_native=_bybit_to_native,
        subscribe_msgs=_bybit_subscribe,
        parse=_parse_bybit,
        multiplier=lambda _c: 1.0,  # linear: size уже в базовой монете
        ping_payload=json.dumps({"op": "ping"}),
    ),
}


# ── Per-exchange book state ────────────────────────────────────────────────
@dataclass
class _ExchangeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    # canonical -> {"bids": {price: qty}, "asks": {price: qty}, "ts": mono}
    books: dict[str, dict[str, Any]] = field(default_factory=dict)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    active_symbols: tuple[str, ...] = ()
    watchlist_seen: dict[str, float] = field(default_factory=dict)
    last_reconcile_mono: float = 0.0


_states: dict[str, _ExchangeState] = {ex: _ExchangeState() for ex in _ADAPTERS}


def _apply_parsed(ex: str, parsed: list[_Parsed]) -> None:
    st = _states[ex]
    now = time.monotonic()
    with st.lock:
        for canon, action, bids, asks in parsed:
            book = st.books.get(canon)
            if action == "snapshot" or book is None:
                book = {"bids": {}, "asks": {}, "ts": now}
                st.books[canon] = book
            for price, qty in bids:
                if qty <= 0:
                    book["bids"].pop(price, None)
                else:
                    book["bids"][price] = qty
            for price, qty in asks:
                if qty <= 0:
                    book["asks"].pop(price, None)
                else:
                    book["asks"][price] = qty
            book["ts"] = now


def get_fresh_depth_book(
    exchange: str, symbol: str, *, max_age_sec: float
) -> dict[str, Any] | None:
    """L2-книга OKX/Bybit в REST-совместимом виде или None (протухла/нет).

    Формат совпадает с ``fetch_orderbook_depth`` — notional в истинных USDT.
    """
    ex = (exchange or "").lower()
    if ex not in _ADAPTERS:
        return None
    st = _states[ex]
    canon = canonical_symbol(symbol)
    now = time.monotonic()
    with st.lock:
        book = st.books.get(canon)
        if book is None or (now - book["ts"]) > max_age_sec:
            return None
        bids_raw = sorted(book["bids"].items(), key=lambda kv: kv[0], reverse=True)
        asks_raw = sorted(book["asks"].items(), key=lambda kv: kv[0])
    if not bids_raw or not asks_raw:
        return None
    mult = _ADAPTERS[ex].multiplier(canon)
    bids = [
        {"price": p, "qty": q * mult, "notional": p * q * mult}
        for p, q in bids_raw[:_OUTPUT_LIMIT]
    ]
    asks = [
        {"price": p, "qty": q * mult, "notional": p * q * mult}
        for p, q in asks_raw[:_OUTPUT_LIMIT]
    ]
    best_bid = bids[0]["price"]
    best_ask = asks[0]["price"]
    return {
        "market": "futures",
        "symbol": canon,
        "exchange": ex,
        "limit": _OUTPUT_LIMIT,
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": (best_bid + best_ask) / 2.0,
        "multiplier": mult,
        "source": "ws",
    }


# ── Watchlist / desired symbols ────────────────────────────────────────────
def touch_watchlist(exchange: str, symbols: Any) -> None:
    ex = (exchange or "").lower()
    if ex not in _ADAPTERS:
        return
    st = _states[ex]
    now = time.monotonic()
    cutoff = now - _WATCHLIST_TTL_SEC
    with st.lock:
        for s in symbols or ():
            canon = canonical_symbol(str(s))
            if canon and split_base_quote(canon):
                st.watchlist_seen[canon] = now
        for k in [k for k, t in st.watchlist_seen.items() if t < cutoff]:
            del st.watchlist_seen[k]


def _desired_symbols(ex: str) -> tuple[str, ...]:
    st = _states[ex]
    cutoff = time.monotonic() - _WATCHLIST_TTL_SEC
    with st.lock:
        fresh = [s for s, t in st.watchlist_seen.items() if t >= cutoff]
    # порядок сохраняем по времени добавления (свежие важнее не нужно — берём все)
    return tuple(fresh[:_MAX_SUBS_PER_CONNECTION])


# ── Run loop ───────────────────────────────────────────────────────────────
def _run_loop(ex: str, symbols: tuple[str, ...], connect_timeout: float) -> None:
    try:
        import websocket
    except ImportError:
        logger.error("websocket-client missing; pip install websocket-client")
        return

    ad = _ADAPTERS[ex]
    st = _states[ex]
    natives = [ad.to_native(s) for s in symbols]
    reconnect_delay = 1.0
    while not st.stop.is_set():
        ws = None
        try:
            ws = websocket.create_connection(ad.ws_url, timeout=connect_timeout)
            ws.settimeout(5.0)
            for msg in ad.subscribe_msgs(natives):
                ws.send(msg)
            reconnect_delay = 1.0
            last_ping = time.monotonic()
            while not st.stop.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.monotonic() - last_ping >= ad.ping_interval:
                        ws.send(ad.ping_payload)
                        last_ping = time.monotonic()
                    continue
                except (websocket.WebSocketConnectionClosedException, OSError):
                    break
                if not raw:
                    continue
                if raw in ("pong", "ping"):
                    continue
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(obj, dict):
                    # ack/подтверждения подписки и pong пропускаем
                    if obj.get("event") in ("subscribe", "error") or obj.get("op") == "pong":
                        if obj.get("event") == "error":
                            logger.warning("l2-depth %s sub error: %s", ex, obj.get("msg"))
                        continue
                    parsed = ad.parse(obj)
                    if parsed:
                        _apply_parsed(ex, parsed)
        except Exception:
            logger.exception("l2-depth %s WS error; retry in %.1fs", ex, reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(60.0, reconnect_delay * 1.8)
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass


def _ensure_started(ex: str, symbols: tuple[str, ...], connect_timeout: float = 8.0) -> None:
    st = _states[ex]
    ad = _ADAPTERS[ex]
    if not symbols:
        return
    if ad.warmup is not None:
        try:
            ad.warmup()
        except Exception:  # noqa: BLE001
            pass
    with st.lock:
        if st.thread is not None and st.thread.is_alive() and symbols == st.active_symbols:
            return
        if st.thread is not None and st.thread.is_alive():
            st.stop.set()
            st.thread.join(timeout=8.0)
        st.stop.clear()
        st.active_symbols = symbols
        # очищаем книги символов, которые больше не подписаны
        keep = set(symbols)
        for canon in [c for c in st.books if c not in keep]:
            del st.books[canon]
        t = threading.Thread(
            target=_run_loop,
            args=(ex, symbols, max(5.0, connect_timeout)),
            daemon=True,
            name=f"{ex}-l2-depth-ws",
        )
        st.thread = t
    logger.info(
        "l2-depth %s: подписка на %d символ(ов): %s",
        ex,
        len(symbols),
        ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
    )
    t.start()


def reconcile(exchange: str, connect_timeout: float = 8.0) -> None:
    """Пересобрать подписку биржи под свежий watchlist (throttled)."""
    ex = (exchange or "").lower()
    if ex not in _ADAPTERS:
        return
    st = _states[ex]
    desired = _desired_symbols(ex)
    if not desired:
        return
    now = time.monotonic()
    with st.lock:
        changed = set(desired) != set(st.active_symbols)
        too_soon = (now - st.last_reconcile_mono) < _RECONCILE_MIN_INTERVAL_SEC
        if not changed or too_soon:
            return
        st.last_reconcile_mono = now
    _ensure_started(ex, desired, connect_timeout)


def stop_all() -> None:
    for st in _states.values():
        st.stop.set()


def l2_depth_health(*, max_age_sec: float = 8.0) -> dict[str, Any]:
    """Диагностика по обеим биржам: подписки и свежесть книг."""
    now = time.monotonic()
    out: dict[str, Any] = {}
    for ex, st in _states.items():
        with st.lock:
            active = st.active_symbols
            syms = {
                canon: {
                    "age_sec": round(now - b["ts"], 2),
                    "bid_levels": len(b["bids"]),
                    "ask_levels": len(b["asks"]),
                    "fresh": (now - b["ts"]) <= max_age_sec,
                }
                for canon, b in st.books.items()
            }
        fresh = sum(1 for s in syms.values() if s["fresh"])
        out[ex] = {
            "subscribed": list(active),
            "tracked": len(syms),
            "fresh": fresh,
            "live": fresh > 0,
            "symbols": syms,
        }
    return out
