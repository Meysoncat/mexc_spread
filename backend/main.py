from __future__ import annotations

import hashlib
import json
from typing import Any
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from mexc_monitor.aster import AsterPublicClient
from mexc_monitor.client import MexcApiError
from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.history_store import init_db, query_recent, resolve_history_db_path
from mexc_monitor.history_worker import start_history_worker, stop_history_worker
from mexc_monitor.klines import fetch_klines_for_market
from mexc_monitor.orderbook import fetch_orderbook_depth
from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.ws_futures import ensure_started_from_settings
from mexc_monitor.ws_futures_orderbook import ensure_futures_orderbook_ws_started
from mexc_monitor.ws_spot_orderbook import ensure_spot_orderbook_ws_started, stop_spot_orderbook_ws

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_METRICS_REF_PUBLIC = _ROOT / "frontend" / "public" / "metrics-reference.json"
_METRICS_REF_SRC = _ROOT / "frontend" / "src" / "data" / "metrics-reference.json"

# Хуки старта/остановки регистрируются по ходу модуля и выполняются из lifespan
# (on_event в FastAPI устарел). Остановка — в обратном порядке.
_startup_hooks: list = []
_shutdown_hooks: list = []


def _on_startup(fn):
    _startup_hooks.append(fn)
    return fn


def _on_shutdown(fn):
    _shutdown_hooks.append(fn)
    return fn


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    for fn in _startup_hooks:
        fn()
    try:
        yield
    finally:
        for fn in reversed(_shutdown_hooks):
            try:
                fn()
            except Exception:  # noqa: BLE001 — остановить остальное важнее
                logger.exception("shutdown hook %s failed", fn.__name__)


app = FastAPI(title="MEXC Spread Screener API", version="0.3.0", lifespan=_lifespan)

from backend.api_v1 import router as api_v1_router, ws_router as api_v1_ws_router  # noqa: E402
from backend.screener import start_runtime, stop_runtime  # noqa: E402

app.include_router(api_v1_router)
app.include_router(api_v1_ws_router)


@_on_startup
def _startup_screener() -> None:
    start_runtime()


@_on_shutdown
def _shutdown_screener() -> None:
    stop_runtime()


@_on_startup
def _startup_prefetch_futures_ws() -> None:
    ensure_started_from_settings(DEFAULT_SETTINGS)
    ensure_futures_orderbook_ws_started(DEFAULT_SETTINGS)
    ensure_spot_orderbook_ws_started(DEFAULT_SETTINGS)
    if DEFAULT_SETTINGS.history_enabled:
        init_db(resolve_history_db_path(DEFAULT_SETTINGS))
    start_history_worker()


@_on_shutdown
def _shutdown_workers() -> None:
    from mexc_monitor.ws_bookticker import stop_all as stop_ws_booktickers

    stop_ws_booktickers()
    stop_history_worker()
    stop_spot_orderbook_ws()

_SNAPSHOT_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_SNAPSHOT_CACHE_TTL_SEC", "3")))
# Медленные биржи (DEX-индексеры) собираются секундами и лимитированы по rate limit —
# держим их снимок в кэше дольше, чтобы пользователь получал мгновенный ответ.
_SNAPSHOT_CACHE_TTL_DYDX_SEC = max(
    _SNAPSHOT_CACHE_TTL_SEC,
    float(os.environ.get("MEXC_SNAPSHOT_CACHE_TTL_DYDX_SEC", "30")),
)
_SNAPSHOT_CACHE_TTL_OVERRIDES: dict[str, float] = {
    "dydx": _SNAPSHOT_CACHE_TTL_DYDX_SEC,
}
_snapshot_cache_lock = threading.Lock()
_snapshot_cache: dict[str, tuple[float, dict]] = {}
# Per-key build locks — «single-flight», чтобы одновременные запросы одного ключа
# не собирали снимок параллельно (cache stampede).
_snapshot_build_locks: dict[str, threading.Lock] = {}


def _snapshot_ttl_for(exchange: str) -> float:
    return _SNAPSHOT_CACHE_TTL_OVERRIDES.get(exchange, _SNAPSHOT_CACHE_TTL_SEC)


# Фоновый префетч: ключи, запрошенные недавно, обновляются до истечения TTL,
# чтобы пользователь никогда не ждал холодную пересборку (особенно dYdX).
_SNAPSHOT_PREFETCH_ENABLED = str(
    os.environ.get("MEXC_SNAPSHOT_PREFETCH", "true")
).strip().lower() in ("true", "1", "yes")
# Ключ считается «горячим», если его запрашивали в последние N секунд.
_SNAPSHOT_PREFETCH_HOT_WINDOW_SEC = max(
    30.0, float(os.environ.get("MEXC_SNAPSHOT_PREFETCH_HOT_WINDOW_SEC", "180"))
)
# key -> (время последнего запроса, builder, ttl)
_snapshot_hot_keys: dict[str, tuple[float, object, float]] = {}
_snapshot_prefetch_stop = threading.Event()
_snapshot_prefetch_thread: threading.Thread | None = None


def _snapshot_prefetch_loop() -> None:
    while not _snapshot_prefetch_stop.wait(1.0):
        now = time.monotonic()
        with _snapshot_cache_lock:
            hot = {
                k: v
                for k, v in _snapshot_hot_keys.items()
                if now - v[0] <= _SNAPSHOT_PREFETCH_HOT_WINDOW_SEC
            }
            _snapshot_hot_keys.clear()
            _snapshot_hot_keys.update(hot)
            expiring = [
                (k, builder, ttl)
                for k, (_, builder, ttl) in hot.items()
                if (c := _snapshot_cache.get(k)) is None or c[0] - now <= 2.0
            ]
        for key, builder, ttl in expiring:
            if _snapshot_prefetch_stop.is_set():
                return
            try:
                payload = builder()  # type: ignore[operator]
                if payload.get("ok") and payload.get("count"):
                    with _snapshot_cache_lock:
                        _snapshot_cache[key] = (time.monotonic() + ttl, payload)
            except Exception as e:  # noqa: BLE001 — фоновый цикл не должен умирать
                logger.warning("snapshot prefetch %s failed: %s", key, e)


def _mark_snapshot_hot(cache_key: str, builder, ttl: float) -> None:
    if not _SNAPSHOT_PREFETCH_ENABLED or ttl <= 0:
        return
    with _snapshot_cache_lock:
        _snapshot_hot_keys[cache_key] = (time.monotonic(), builder, ttl)


@_on_startup
def _startup_snapshot_prefetch() -> None:
    global _snapshot_prefetch_thread
    if not _SNAPSHOT_PREFETCH_ENABLED:
        return
    _snapshot_prefetch_stop.clear()
    _snapshot_prefetch_thread = threading.Thread(
        target=_snapshot_prefetch_loop, name="snapshot-prefetch", daemon=True
    )
    _snapshot_prefetch_thread.start()
    # Прогреваем WS-фиды сразу: первый запрос dYdX получает данные из стрима
    # (~3-4 сек прогрева), а не через медленный REST-обход рынков.
    try:
        from mexc_monitor.dydx.ws_feed import ensure_dydx_ws_started
        from mexc_monitor.ws_bookticker import ensure_started as ensure_bookticker_ws

        ensure_dydx_ws_started()
        ensure_bookticker_ws()
    except Exception as e:  # noqa: BLE001
        logger.warning("ws feed warmup failed: %s", e)


@_on_shutdown
def _shutdown_snapshot_prefetch() -> None:
    _snapshot_prefetch_stop.set()

_DEPTH_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_DEPTH_CACHE_TTL_SEC", "1")))
_depth_cache_lock = threading.Lock()
_depth_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}


def _build_snapshot_payload(market: str) -> dict:
    df, err = safe_load_snapshot(market=market)
    if err:
        logger.warning("snapshot market=%s failed: %s", market, err)
        return {"ok": False, "error": err, "market": market, "rows": [], "count": 0}
    s = DEFAULT_SETTINGS
    if market == "cross":
        notes = [
            "Базис: basis_mid_abs = fut_mid − spot_mid; basis_mid_bps = 10_000 × basis / spot_mid",
            "Строка = пара спот (BTCUSDT) и USDT-M перп (BTC_USDT) с одинаковой базой",
            "Фильтр «мин. bps» на клиенте — по |basis_mid_bps|; объём — min на обеих ногах (котировка)",
            "Funding и спреды ног — с соответствующих рынков; снимок точечный",
        ]
    else:
        notes = [
            "net_spread_bps = spread_bps − 2×taker(one-way) для выбранного рынка",
            "l1_max_* только по лучшему уровню; фьючерсы часто без qty в тикере",
            "Снимок точечный: между опросом и ордером книга и спред меняются",
        ]
    execution_model = {
        "fee_model": "taker_round_trip",
        "spot_taker_fee_bps_one_way": s.exec_spot_taker_fee_bps,
        "futures_taker_fee_bps_one_way": s.exec_futures_taker_fee_bps,
        "reference_quote_notional": s.exec_reference_quote_notional,
        "notes": notes,
    }
    if df is None or df.empty:
        logger.info("snapshot market=%s ok rows=0 (empty)", market)
        return {
            "ok": True,
            "market": market,
            "rows": [],
            "count": 0,
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "execution_model": execution_model,
        }
    try:
        rows = json.loads(df.to_json(orient="records"))
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.exception("snapshot market=%s DataFrame.to_json/parse: %s", market, e)
        return {
            "ok": False,
            "error": f"snapshot_json: {type(e).__name__}: {e}",
            "market": market,
            "rows": [],
            "count": 0,
        }
    loaded_at: str | None = None
    if rows:
        oa = rows[0].get("observed_at")
        if isinstance(oa, str) and oa:
            loaded_at = oa
    if loaded_at is None:
        loaded_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "snapshot market=%s ok rows=%s loaded_at=%s",
        market,
        len(rows),
        loaded_at[:19] if loaded_at else "",
    )
    return {
        "ok": True,
        "market": market,
        "rows": rows,
        "count": len(rows),
        "loaded_at": loaded_at,
        "execution_model": execution_model,
    }


def _build_dex_snapshot_payload(exchange: str) -> dict:
    """Build snapshot payload for DEX exchanges (asterdex, lighter).

    DEPRECATED: Use _build_exchange_snapshot_payload() instead.
    Kept for backward compatibility.
    """
    return _build_exchange_snapshot_payload(exchange)


def _get_snapshot_payload(
    cache_key: str,
    *,
    bypass_cache: bool,
    builder,
    ttl: float | None = None,
) -> dict:
    ttl = _SNAPSHOT_CACHE_TTL_SEC if ttl is None else ttl
    if ttl <= 0 or bypass_cache:
        payload = builder()
        out = dict(payload)
        out["cache_hit"] = False
        return out

    def _fresh_cached() -> dict | None:
        now = time.monotonic()
        with _snapshot_cache_lock:
            cached = _snapshot_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                out = dict(cached[1])
                out["cache_hit"] = True
                return out
        return None

    hit = _fresh_cached()
    if hit is not None:
        return hit

    # single-flight: только один поток собирает снимок для ключа, остальные ждут
    # и получают свежий кэш вместо параллельной пересборки (cache stampede).
    with _snapshot_cache_lock:
        build_lock = _snapshot_build_locks.get(cache_key)
        if build_lock is None:
            build_lock = threading.Lock()
            _snapshot_build_locks[cache_key] = build_lock

    with build_lock:
        hit = _fresh_cached()
        if hit is not None:
            return hit
        payload = builder()
        # Пустой снимок (например, все запросы уперлись в 429) не кэшируем,
        # чтобы не залипать на TTL без данных.
        if payload.get("ok") and payload.get("count"):
            with _snapshot_cache_lock:
                _snapshot_cache[cache_key] = (time.monotonic() + ttl, payload)

    out = dict(payload)
    out["cache_hit"] = False
    return out


app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    from mexc_monitor.ws_bookticker import feeds_health

    return {"status": "ok", "ws_feeds": feeds_health()}


@app.get("/api/debug/mexc-connectivity")
def debug_mexc_connectivity(
    timeout_sec: float = Query(
        8.0,
        ge=1.0,
        le=30.0,
        description="Timeout per check in seconds",
    ),
) -> dict:
    """
    Быстрая диагностика доступности MEXC endpoints из текущего runtime API.
    """
    import httpx

    checks: dict[str, dict] = {}

    def _check_http(name: str, url: str) -> None:
        started = time.monotonic()
        try:
            r = httpx.get(url, timeout=timeout_sec)
            checks[name] = {
                "ok": r.status_code == 200,
                "status_code": r.status_code,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "url": url,
                "body_preview": r.text[:180],
            }
        except Exception as e:
            checks[name] = {
                "ok": False,
                "status_code": None,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "url": url,
                "error": f"{type(e).__name__}: {e}",
            }

    _check_http("spot_book_ticker", DEFAULT_SETTINGS.book_ticker_url)
    _check_http("futures_ticker", DEFAULT_SETTINGS.contract_ticker_url)

    ws_url = DEFAULT_SETTINGS.futures_ws_url
    started = time.monotonic()
    try:
        import websocket
        from websocket import WebSocketBadStatusException

        ws = websocket.create_connection(ws_url, timeout=timeout_sec)
        try:
            ws.close()
        except Exception:
            pass
        checks["futures_ws_handshake"] = {
            "ok": True,
            "http_status": 101,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "url": ws_url,
        }
    except ImportError:
        checks["futures_ws_handshake"] = {
            "ok": False,
            "http_status": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "url": ws_url,
            "error": "websocket-client is not installed",
        }
    except WebSocketBadStatusException as e:
        checks["futures_ws_handshake"] = {
            "ok": False,
            "http_status": int(e.status_code),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "url": ws_url,
            "error": str(e),
        }
    except Exception as e:
        checks["futures_ws_handshake"] = {
            "ok": False,
            "http_status": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "url": ws_url,
            "error": f"{type(e).__name__}: {e}",
        }

    all_ok = all(item.get("ok") for item in checks.values())
    return {
        "ok": all_ok,
        "timeout_sec": timeout_sec,
        "settings": {
            "spot_base_url": DEFAULT_SETTINGS.base_url,
            "futures_base_url": DEFAULT_SETTINGS.futures_base_url,
            "futures_ws_url": DEFAULT_SETTINGS.futures_ws_url,
            "futures_ticker_source": DEFAULT_SETTINGS.futures_ticker_source,
        },
        "checks": checks,
    }


@app.get("/api/metrics-reference")
def metrics_reference() -> dict:
    """Отдаёт JSON справки по метрикам (редактируется без пересборки UI при dev proxy)."""
    path = _METRICS_REF_PUBLIC if _METRICS_REF_PUBLIC.is_file() else _METRICS_REF_SRC
    if not path.is_file():
        raise HTTPException(status_code=404, detail="metrics-reference.json not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("metrics-reference invalid JSON: %s", e)
        raise HTTPException(status_code=500, detail="invalid metrics-reference.json") from e


@app.get("/api/klines")
def klines(
    market: str = Query("spot", description="spot или futures"),
    symbol: str = Query(..., min_length=3, max_length=40, description="Тикер как в снимке (BTCUSDT или BTC_USDT)"),
    interval: str = Query(
        "1h",
        description="Интервал: 5m, 15m, 1h, 4h, 1d",
    ),
    limit: int | None = Query(
        None,
        ge=1,
        le=1000,
        description="Макс. число свечей (мини-графики; на споте по умолчанию 500 без параметра)",
    ),
) -> dict:
    m = market if market in ("spot", "futures") else "spot"
    try:
        candles = fetch_klines_for_market(m, symbol, interval=interval, limit=limit)
    except MexcApiError as e:
        return {"ok": False, "error": str(e), "market": m, "symbol": symbol.strip(), "interval": interval, "candles": []}
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "market": m,
            "symbol": symbol.strip(),
            "interval": interval,
            "candles": [],
        }
    return {
        "ok": True,
        "market": m,
        "symbol": symbol.strip(),
        "interval": interval,
        "count": len(candles),
        "candles": candles,
    }


# ─── Batch Klines with in-memory cache ─────────────────────────────────────────

import concurrent.futures as _cf

_KLINES_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_KLINES_CACHE_TTL_SEC", "60")))
_klines_cache_lock = threading.Lock()
_klines_cache: dict[str, tuple[float, list[dict]]] = {}  # key → (expires_at, candles)


def _klines_cache_key(market: str, symbol: str, interval: str, limit: int) -> str:
    return f"{market}:{symbol.upper()}:{interval}:{limit}"


def _fetch_klines_cached(market: str, symbol: str, interval: str, limit: int) -> list[dict]:
    """Fetch klines with in-memory cache (TTL = _KLINES_CACHE_TTL_SEC)."""
    key = _klines_cache_key(market, symbol, interval, limit)
    now = time.monotonic()

    if _KLINES_CACHE_TTL_SEC > 0:
        with _klines_cache_lock:
            cached = _klines_cache.get(key)
            if cached is not None:
                expires_at, candles = cached
                if expires_at > now:
                    return candles

    try:
        candles = fetch_klines_for_market(market, symbol, interval=interval, limit=limit)
    except Exception as e:
        logger.debug("klines_batch fetch %s/%s failed: %s", market, symbol, e)
        return []

    if _KLINES_CACHE_TTL_SEC > 0 and candles:
        with _klines_cache_lock:
            _klines_cache[key] = (now + _KLINES_CACHE_TTL_SEC, candles)

    return candles


# Interval mapping for AsterDEX (Binance-compatible)
_INTERVAL_TO_ASTER: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}

# Interval mapping for Lighter (resolution in minutes)
_INTERVAL_TO_LIGHTER_RES: dict[str, str] = {
    "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1440",
}

# Interval mappings for new exchanges
_INTERVAL_TO_BINANCE: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}
_INTERVAL_TO_BYBIT: dict[str, str] = {
    "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D",
}
_INTERVAL_TO_OKX: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D",
}
_INTERVAL_TO_GATEIO: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}
_INTERVAL_TO_HTX: dict[str, str] = {
    "5m": "5min", "15m": "15min", "1h": "60min", "4h": "4hour", "1d": "1day",
}
_INTERVAL_TO_BITGET: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}
_INTERVAL_TO_DYDX: dict[str, str] = {
    "5m": "5MINS", "15m": "15MINS", "1h": "1HOUR", "4h": "4HOURS", "1d": "1DAY",
}
_INTERVAL_TO_HYPERLIQUID: dict[str, str] = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}


_aster_public = AsterPublicClient()


def _fetch_klines_for_exchange(exchange: str, market: str, symbol: str, interval: str, limit: int) -> list[dict]:
    """Fetch klines for any supported exchange."""
    if exchange == "mexc":
        return _fetch_klines_cached(market, symbol, interval, limit)

    elif exchange == "asterdex":
        aster_interval = _INTERVAL_TO_ASTER.get(interval, "1h")
        try:
            raw = _aster_public.klines(symbol, interval=aster_interval, limit=limit)
            candles = []
            for c in raw:
                if not isinstance(c, (list, tuple)) or len(c) < 6:
                    continue
                candles.append({
                    "time": int(c[0]) // 1000,  # ms -> sec
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                })
            return candles
        except Exception as e:
            logger.debug("klines asterdex/%s failed: %s", symbol, e)
            return []

    elif exchange == "lighter":
        from mexc_monitor.lighter.client import LighterPublicClient, LighterApiError
        resolution = _INTERVAL_TO_LIGHTER_RES.get(interval, "60")
        try:
            # Need to resolve symbol → market_id
            client = LighterPublicClient()
            details = client.orderbook_details(filter="perp")
            # Find market_id for this symbol (symbol comes as "BTCUSDT", Lighter uses "BTC")
            market_id = None
            sym_upper = symbol.upper().replace("USDT", "").replace("USD", "")
            for d in details:
                if d.symbol.upper().replace("-PERP", "").replace("_PERP", "") == sym_upper:
                    market_id = d.market_id
                    break
            if market_id is None:
                return []
            candles = client.candles(market_id, resolution=resolution, limit=limit)
            return candles
        except Exception as e:
            logger.debug("klines lighter/%s failed: %s", symbol, e)
            return []

    elif exchange == "binance":
        from mexc_monitor.binance.client import BinancePublicClient
        binance_interval = _INTERVAL_TO_BINANCE.get(interval, "1h")
        try:
            client = BinancePublicClient()
            raw = client.klines(symbol, interval=binance_interval, limit=limit, market=market)
            candles = []
            for c in raw:
                if not isinstance(c, (list, tuple)) or len(c) < 6:
                    continue
                candles.append({
                    "time": int(c[0]) // 1000,  # ms -> sec
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                })
            return candles
        except Exception as e:
            logger.debug("klines binance/%s failed: %s", symbol, e)
            return []

    elif exchange == "bybit":
        from mexc_monitor.bybit.client import BybitPublicClient
        bybit_interval = _INTERVAL_TO_BYBIT.get(interval, "60")
        try:
            client = BybitPublicClient()
            return client.klines(symbol, interval=bybit_interval, limit=limit)
        except Exception as e:
            logger.debug("klines bybit/%s failed: %s", symbol, e)
            return []

    elif exchange == "okx":
        from mexc_monitor.okx.client import OkxPublicClient
        okx_interval = _INTERVAL_TO_OKX.get(interval, "1H")
        try:
            client = OkxPublicClient()
            # OKX uses instId format: "BTC-USDT" for spot, "BTC-USDT-SWAP" for futures
            sym_upper = symbol.upper()
            if "-" not in sym_upper:
                # Convert "BTCUSDT" → "BTC-USDT" or "BTC-USDT-SWAP"
                if sym_upper.endswith("USDT"):
                    base = sym_upper[:-4]
                    okx_symbol = f"{base}-USDT-SWAP" if market == "futures" else f"{base}-USDT"
                elif sym_upper.endswith("USD"):
                    base = sym_upper[:-3]
                    okx_symbol = f"{base}-USD-SWAP" if market == "futures" else f"{base}-USD"
                else:
                    okx_symbol = sym_upper
            else:
                okx_symbol = sym_upper
            return client.klines(okx_symbol, interval=okx_interval, limit=limit)
        except Exception as e:
            logger.debug("klines okx/%s failed: %s", symbol, e)
            return []

    elif exchange == "gateio":
        from mexc_monitor.gateio.client import GateioPublicClient
        gateio_interval = _INTERVAL_TO_GATEIO.get(interval, "1h")
        try:
            client = GateioPublicClient()
            # Gate.io uses "BTC_USDT" for spot, "BTC_USDT" for futures contract
            sym_upper = symbol.upper()
            if "_" not in sym_upper:
                # Convert "BTCUSDT" → "BTC_USDT"
                if sym_upper.endswith("USDT"):
                    base = sym_upper[:-4]
                    gateio_symbol = f"{base}_USDT"
                elif sym_upper.endswith("USD"):
                    base = sym_upper[:-3]
                    gateio_symbol = f"{base}_USD"
                else:
                    gateio_symbol = sym_upper
            else:
                gateio_symbol = sym_upper
            return client.klines(gateio_symbol, interval=gateio_interval, limit=limit, market=market)
        except Exception as e:
            logger.debug("klines gateio/%s failed: %s", symbol, e)
            return []

    elif exchange == "htx":
        from mexc_monitor.htx.client import HtxPublicClient
        htx_interval = _INTERVAL_TO_HTX.get(interval, "60min")
        try:
            client = HtxPublicClient()
            return client.klines(symbol, interval=htx_interval, limit=limit, market=market)
        except Exception as e:
            logger.debug("klines htx/%s failed: %s", symbol, e)
            return []

    elif exchange == "bitget":
        from mexc_monitor.bitget.client import BitgetPublicClient
        bitget_interval = _INTERVAL_TO_BITGET.get(interval, "1h")
        try:
            client = BitgetPublicClient()
            return client.klines(symbol, interval=bitget_interval, limit=limit)
        except Exception as e:
            logger.debug("klines bitget/%s failed: %s", symbol, e)
            return []

    elif exchange == "dydx":
        from mexc_monitor.dydx.client import DydxPublicClient
        dydx_interval = _INTERVAL_TO_DYDX.get(interval, "1HOUR")
        try:
            client = DydxPublicClient()
            # dYdX uses "BTC-USD" format
            sym_upper = symbol.upper()
            if "-" not in sym_upper:
                if sym_upper.endswith("USDT"):
                    base = sym_upper[:-4]
                    dydx_symbol = f"{base}-USD"
                elif sym_upper.endswith("USD"):
                    base = sym_upper[:-3]
                    dydx_symbol = f"{base}-USD"
                else:
                    dydx_symbol = sym_upper
            else:
                dydx_symbol = sym_upper
            return client.klines(dydx_symbol, interval=dydx_interval, limit=limit)
        except Exception as e:
            logger.debug("klines dydx/%s failed: %s", symbol, e)
            return []

    elif exchange == "hyperliquid":
        from mexc_monitor.hyperliquid.client import HyperliquidPublicClient
        hl_interval = _INTERVAL_TO_HYPERLIQUID.get(interval, "1h")
        try:
            client = HyperliquidPublicClient()
            return client.klines(symbol, interval=hl_interval, limit=limit)
        except Exception as e:
            logger.debug("klines hyperliquid/%s failed: %s", symbol, e)
            return []

    return []


@app.get("/api/klines/batch")
def klines_batch(
    market: str = Query("spot", description="spot или futures"),
    symbols: str = Query(..., description="Символы через запятую (макс. 50)"),
    interval: str = Query("1h", description="Интервал: 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(96, ge=1, le=500, description="Макс. свечей на символ"),
    exchange: str = Query("mexc", description="mexc, asterdex или lighter"),
) -> dict:
    """
    Batch-загрузка klines для нескольких символов одним запросом.
    Использует in-memory кэш (TTL 60s по умолчанию) и параллельные запросы.
    Поддерживает все биржи: mexc, asterdex, lighter.
    """
    ex = (exchange or "").strip().lower()
    if ex not in _SUPPORTED_EXCHANGES:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"Unknown exchange: {exchange}", "supported": _SUPPORTED_EXCHANGES},
        )

    m = market if market in ("spot", "futures") else "spot"
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    sym_list = sym_list[:50]  # Лимит 50 символов

    if not sym_list:
        return {"ok": True, "market": m, "interval": interval, "results": {}, "count": 0}

    lim = max(1, min(limit, 500))
    results: dict[str, list[dict]] = {}

    # Separate cached vs uncached
    uncached_symbols: list[str] = []
    now = time.monotonic()

    for sym in sym_list:
        key = _klines_cache_key(f"{ex}:{m}", sym, interval, lim)
        if _KLINES_CACHE_TTL_SEC > 0:
            with _klines_cache_lock:
                cached = _klines_cache.get(key)
                if cached is not None and cached[0] > now:
                    results[sym] = cached[1]
                    continue
        uncached_symbols.append(sym)

    # Fetch uncached in parallel (max 8 workers)
    if uncached_symbols:
        def _fetch_one(sym: str) -> tuple[str, list[dict]]:
            return sym, _fetch_klines_for_exchange(ex, m, sym, interval, lim)

        with _cf.ThreadPoolExecutor(max_workers=min(8, len(uncached_symbols))) as executor:
            future_map = {
                executor.submit(_fetch_one, sym): sym
                for sym in uncached_symbols
            }
            for future in _cf.as_completed(future_map):
                sym = future_map[future]
                try:
                    _, candles = future.result()
                    results[sym] = candles
                    # Cache the result
                    if _KLINES_CACHE_TTL_SEC > 0 and candles:
                        key = _klines_cache_key(f"{ex}:{m}", sym, interval, lim)
                        with _klines_cache_lock:
                            _klines_cache[key] = (time.monotonic() + _KLINES_CACHE_TTL_SEC, candles)
                except Exception:
                    results[sym] = []

    return {
        "ok": True,
        "market": m,
        "exchange": ex,
        "interval": interval,
        "count": len(results),
        "results": results,
    }


@app.get("/api/depth")
def orderbook_depth(
    market: str = Query("spot", description="spot или futures"),
    symbol: str = Query(..., min_length=3, max_length=40),
    limit: int = Query(100, ge=5, le=1000),
    nocache: bool = Query(
        False,
        description="Пропустить кратковременный кэш стакана на сервере",
    ),
) -> dict:
    m = market if market in ("spot", "futures") else "spot"
    sym = symbol.strip()
    lim = int(limit)
    key = (m, sym.upper(), lim)
    now = time.monotonic()
    if not nocache and _DEPTH_CACHE_TTL_SEC > 0:
        with _depth_cache_lock:
            hit = _depth_cache.get(key)
            if hit is not None:
                exp, cached = hit
                if exp > now:
                    out = dict(cached)
                    out["cache_hit"] = True
                    return out
    try:
        data = fetch_orderbook_depth(m, sym, limit=lim)
    except MexcApiError as e:
        return {
            "ok": False,
            "error": str(e),
            "market": m,
            "symbol": sym,
            "limit": lim,
            "bids": [],
            "asks": [],
            "cache_hit": False,
        }
    except Exception as e:
        logger.exception("depth market=%s symbol=%s", m, sym)
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "market": m,
            "symbol": sym,
            "limit": lim,
            "bids": [],
            "asks": [],
            "cache_hit": False,
        }
    out: dict = {"ok": True, **data, "cache_hit": False}

    # VWAP execution estimates from L2 depth
    if out.get("ok") and out.get("bids") and out.get("asks"):
        from mexc_monitor.vwap import compute_depth_summary
        ref_notional = float(DEFAULT_SETTINGS.exec_reference_quote_notional)
        vwap_summary = compute_depth_summary(
            out["bids"], out["asks"], reference_notional=ref_notional,
        )
        out["vwap"] = {
            "vwap_buy_price": vwap_summary["vwap_buy_price"],
            "vwap_sell_price": vwap_summary["vwap_sell_price"],
            "slippage_buy_bps": vwap_summary["slippage_buy_bps"],
            "slippage_sell_bps": vwap_summary["slippage_sell_bps"],
            "executable_buy_notional": vwap_summary["executable_buy_notional"],
            "executable_sell_notional": vwap_summary["executable_sell_notional"],
            "depth_levels": vwap_summary["depth_levels"],
        }

    if _DEPTH_CACHE_TTL_SEC > 0 and out.get("ok"):
        with _depth_cache_lock:
            _depth_cache[key] = (now + _DEPTH_CACHE_TTL_SEC, dict(out))
    return out


_SUPPORTED_EXCHANGES = [
    "mexc", "asterdex", "lighter",
    "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid",
]

# Биржи с поддержкой нескольких рынков (spot/futures)
_MULTI_MARKET_EXCHANGES = {"mexc", "binance", "okx", "gateio", "htx"}


def _build_exchange_snapshot_payload(exchange: str, market: str | None = None) -> dict:
    """Build snapshot payload for any non-MEXC exchange."""
    # Lazy imports to avoid circular imports and startup overhead
    from mexc_monitor.aster import aster_snapshot_rows
    from mexc_monitor.lighter import lighter_snapshot_rows
    from mexc_monitor.binance import binance_snapshot_rows
    from mexc_monitor.bybit import bybit_snapshot_rows
    from mexc_monitor.okx import okx_snapshot_rows
    from mexc_monitor.gateio import gateio_snapshot_rows
    from mexc_monitor.htx import htx_snapshot_rows
    from mexc_monitor.bitget import bitget_snapshot_rows
    from mexc_monitor.dydx import dydx_snapshot_rows
    from mexc_monitor.hyperliquid import hyperliquid_snapshot_rows

    _exchange_snapshot_map: dict[str, tuple] = {
        "asterdex": (aster_snapshot_rows, "perp"),
        "lighter": (lighter_snapshot_rows, "perp"),
        "binance": (binance_snapshot_rows, "futures"),
        "bybit": (bybit_snapshot_rows, "perp"),
        "okx": (okx_snapshot_rows, "futures"),
        "gateio": (gateio_snapshot_rows, "futures"),
        "htx": (htx_snapshot_rows, "futures"),
        "bitget": (bitget_snapshot_rows, "perp"),
        "dydx": (dydx_snapshot_rows, "perp"),
        "hyperliquid": (hyperliquid_snapshot_rows, "perp"),
    }

    snapshot_fn, default_market = _exchange_snapshot_map[exchange]
    actual_market = market or default_market

    try:
        from mexc_monitor.ws_bookticker import try_ws_snapshot_rows

        rows_list = try_ws_snapshot_rows(exchange, actual_market)
        if rows_list is None:
            if exchange in _MULTI_MARKET_EXCHANGES:
                rows_list = snapshot_fn(market=actual_market)
            else:
                rows_list = snapshot_fn()
    except Exception as e:
        logger.warning("snapshot exchange=%s failed: %s", exchange, e)
        return {
            "ok": False,
            "error": f"{exchange} API error: {type(e).__name__}: {e}",
            "market": actual_market,
            "rows": [],
            "count": 0,
        }

    if not rows_list:
        return {
            "ok": True,
            "market": actual_market,
            "rows": [],
            "count": 0,
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        }

    from dataclasses import asdict

    rows = [asdict(r) for r in rows_list]
    loaded_at: str | None = None
    if rows:
        oa = rows[0].get("observed_at")
        if isinstance(oa, str) and oa:
            loaded_at = oa
    if loaded_at is None:
        loaded_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "snapshot exchange=%s ok rows=%s loaded_at=%s",
        exchange,
        len(rows),
        loaded_at[:19] if loaded_at else "",
    )
    return {
        "ok": True,
        "market": actual_market,
        "rows": rows,
        "count": len(rows),
        "loaded_at": loaded_at,
    }


@app.get("/api/snapshot")
def snapshot(
    request: Request,
    market: str = Query("spot", description="spot, futures или cross"),
    exchange: str = Query("mexc", description="Биржа: mexc, binance, bybit, okx, gateio, htx, bitget, asterdex, lighter, dydx, hyperliquid"),
    nocache: bool = Query(
        False,
        description="Пропустить серверный кэш снимка (принудительно сходить на биржу)",
    ),
) -> Response:
    ex = (exchange or "").strip().lower()
    if ex not in _SUPPORTED_EXCHANGES:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": f"Unknown exchange: {exchange}",
                "supported": _SUPPORTED_EXCHANGES,
            },
        )

    if ex == "mexc":
        raw = (market or "").strip().lower()
        m = raw if raw in ("spot", "futures", "cross") else "spot"
        if m != raw:
            logger.warning(
                "snapshot unknown market=%r normalized to %s",
                market,
                m,
            )
        cache_key = f"mexc:{m}"
        _mark_snapshot_hot(cache_key, lambda: _build_snapshot_payload(m), _snapshot_ttl_for("mexc"))
        out = _get_snapshot_payload(
            cache_key,
            bypass_cache=nocache,
            builder=lambda: _build_snapshot_payload(m),
        )
    else:
        # All non-MEXC exchanges use the generic dispatch
        raw_market = (market or "").strip().lower()
        # For multi-market exchanges, pass the market parameter
        if ex in _MULTI_MARKET_EXCHANGES:
            m = raw_market if raw_market in ("spot", "futures") else None
        else:
            m = None
        cache_key = f"{ex}:{m or 'default'}"
        _mark_snapshot_hot(
            cache_key,
            lambda: _build_exchange_snapshot_payload(ex, m),
            _snapshot_ttl_for(ex),
        )
        out = _get_snapshot_payload(
            cache_key,
            bypass_cache=nocache,
            builder=lambda: _build_exchange_snapshot_payload(ex, m),
            ttl=_snapshot_ttl_for(ex),
        )

    if not out.get("ok"):
        logger.warning(
            "snapshot response not ok exchange=%s error=%s",
            ex,
            out.get("error"),
        )
        return JSONResponse(content=out)

    # ETag по содержимому снимка: если кэш не обновлялся, отдаём 304 без тела —
    # браузер переиспользует уже полученный ответ (экономия сотен КБ на запрос).
    etag = 'W/"{}"'.format(
        hashlib.md5(
            f"{ex}:{out.get('market')}:{out.get('loaded_at')}:{out.get('count')}".encode()
        ).hexdigest()
    )
    if_none_match = request.headers.get("if-none-match", "")
    if etag in if_none_match:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )
    return JSONResponse(
        content=out,
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.get("/api/snapshot/stream")
async def snapshot_stream(
    request: Request,
    market: str = Query("spot", description="spot, futures или cross"),
    exchange: str = Query("mexc"),
    interval_sec: float = Query(2.0, ge=0.5, le=60.0),
) -> Response:
    """SSE-поток снимков: новое событие только когда снимок обновился.

    Заменяет поллинг с фронта: бэкенд сам проверяет кэш (который греется
    WS-фидами и префетчем) и пушит payload при смене loaded_at.
    """
    ex = (exchange or "").strip().lower()
    if ex not in _SUPPORTED_EXCHANGES:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"Unknown exchange: {exchange}"},
        )

    raw_market = (market or "").strip().lower()
    if ex == "mexc":
        m: str | None = raw_market if raw_market in ("spot", "futures", "cross") else "spot"
        cache_key = f"mexc:{m}"
        builder = lambda: _build_snapshot_payload(m)  # noqa: E731
    else:
        if ex in _MULTI_MARKET_EXCHANGES:
            m = raw_market if raw_market in ("spot", "futures") else None
        else:
            m = None
        cache_key = f"{ex}:{m or 'default'}"
        builder = lambda: _build_exchange_snapshot_payload(ex, m)  # noqa: E731
    ttl = _snapshot_ttl_for(ex)

    def _poll(last_sent: str) -> tuple[str, str | None]:
        """Синхронная часть (кэш/сборка снимка + сериализация) — в пуле потоков."""
        _mark_snapshot_hot(cache_key, builder, ttl)
        try:
            out = _get_snapshot_payload(cache_key, bypass_cache=False, builder=builder, ttl=ttl)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": str(e)}
        marker = f"{out.get('loaded_at')}:{out.get('count')}:{out.get('ok')}"
        if marker == last_sent:
            return marker, None
        return marker, f"data: {json.dumps(out)}\n\n"

    async def event_generator():
        # Асинхронный генератор: между опросами соединение не держит поток
        # из пула (раньше каждая вкладка блокировала поток на time.sleep).
        import asyncio

        from starlette.concurrency import run_in_threadpool

        last_sent = ""
        last_keepalive = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            last_sent, chunk = await run_in_threadpool(_poll, last_sent)
            if chunk is not None:
                last_keepalive = time.monotonic()
                yield chunk
            elif time.monotonic() - last_keepalive > 15.0:
                last_keepalive = time.monotonic()
                yield ": keepalive\n\n"
            await asyncio.sleep(interval_sec)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history/recent")
def history_recent(
    market: str = Query("spot", description="spot или futures"),
    symbol: str | None = Query(None, description="Точный символ как в снимке"),
    since: str | None = Query(
        None,
        description="ISO8601 нижняя граница observed_at (включительно)",
    ),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    m = market if market in ("spot", "futures") else "spot"
    path = resolve_history_db_path(DEFAULT_SETTINGS)
    if not path.is_file():
        return {"ok": True, "market": m, "rows": [], "count": 0, "db_path": str(path)}
    rows = query_recent(
        path,
        market=m,
        symbol=symbol,
        since_iso=since,
        limit=limit,
    )
    return {"ok": True, "market": m, "rows": rows, "count": len(rows), "db_path": str(path)}


# ─── Spread Buffer & Streaming endpoints ───────────────────────────────────────

from mexc_monitor.spread_buffer import (
    SpreadTick,
    get_history as sb_get_history,
    get_latest as sb_get_latest,
    get_stats as sb_get_stats,
    get_tracked_symbols as sb_get_tracked_symbols,
    subscribe as sb_subscribe,
    unsubscribe as sb_unsubscribe,
)
from starlette.responses import StreamingResponse


def _tick_to_dict(t: SpreadTick) -> dict:
    return {
        "timestamp_ms": t.timestamp_ms,
        "bid": t.bid,
        "ask": t.ask,
        "bid_qty": t.bid_qty,
        "ask_qty": t.ask_qty,
        "mid": t.mid,
        "spread_abs": t.spread_abs,
        "spread_bps": t.spread_bps,
    }


@app.get("/api/spread/symbols")
def spread_tracked_symbols() -> dict:
    """Список символов с данными в spread buffer."""
    symbols = sb_get_tracked_symbols()
    return {"ok": True, "symbols": symbols, "count": len(symbols)}


@app.get("/api/spread/history")
def spread_history(
    symbol: str = Query(..., min_length=2, max_length=40),
    last_n: int | None = Query(None, ge=1, le=10000),
    since_ms: int | None = Query(None, description="Unix ms нижняя граница"),
    max_points: int = Query(1000, ge=10, le=5000),
) -> dict:
    """История спреда из in-memory ring buffer."""
    ticks = sb_get_history(
        symbol.strip(),
        last_n=last_n,
        since_ms=since_ms,
        max_points=max_points,
    )
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "count": len(ticks),
        "ticks": [_tick_to_dict(t) for t in ticks],
    }


@app.get("/api/spread/latest")
def spread_latest(
    symbol: str = Query(..., min_length=2, max_length=40),
) -> dict:
    """Последний тик спреда."""
    tick = sb_get_latest(symbol.strip())
    if tick is None:
        return {"ok": True, "symbol": symbol.strip().upper(), "tick": None}
    return {"ok": True, "symbol": symbol.strip().upper(), "tick": _tick_to_dict(tick)}


@app.get("/api/spread/stats")
def spread_stats(
    symbol: str = Query(..., min_length=2, max_length=40),
    period_sec: float = Query(300.0, ge=10, le=3600),
    threshold_bps: float | None = Query(None, ge=0),
) -> dict:
    """Статистика спреда за период."""
    stats = sb_get_stats(
        symbol.strip(),
        period_sec=period_sec,
        threshold_bps=threshold_bps,
    )
    if stats is None:
        return {"ok": True, "symbol": symbol.strip().upper(), "stats": None}
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "stats": {
            "period_sec": stats.period_sec,
            "ticks_count": stats.ticks_count,
            "avg_spread_bps": stats.avg_spread_bps,
            "min_spread_bps": stats.min_spread_bps,
            "max_spread_bps": stats.max_spread_bps,
            "std_spread_bps": stats.std_spread_bps,
            "current_spread_bps": stats.current_spread_bps,
            "current_bid": stats.current_bid,
            "current_ask": stats.current_ask,
            "current_mid": stats.current_mid,
            "pct_above_threshold": stats.pct_above_threshold,
        },
    }


@app.get("/api/spread/stream")
async def spread_stream(
    request: Request,
    symbol: str = Query(..., min_length=2, max_length=40),
) -> StreamingResponse:
    """
    SSE (Server-Sent Events) поток обновлений спреда в реальном времени.
    Клиент подключается и получает события при каждом изменении bid/ask.

    Асинхронный: соединение не занимает поток из пула, тики из WS-потока
    передаются через call_soon_threadsafe; при переполнении старые
    отбрасываются (клиенту важна свежая цена, а не каждый тик).
    """
    import asyncio

    sym = symbol.strip().upper()
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[SpreadTick] = asyncio.Queue(maxsize=500)

    def _offer(tick: SpreadTick) -> None:
        if q.full():
            q.get_nowait()
        q.put_nowait(tick)

    def on_tick(_symbol: str, tick: SpreadTick) -> None:
        try:
            loop.call_soon_threadsafe(_offer, tick)
        except RuntimeError:  # цикл закрыт
            pass

    sb_subscribe(sym, on_tick)

    async def event_generator():
        try:
            # Отправляем последний известный тик сразу
            latest = sb_get_latest(sym)
            if latest:
                yield f"data: {json.dumps(_tick_to_dict(latest))}\n\n"
            while True:
                try:
                    tick = await asyncio.wait_for(q.get(), 15.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(_tick_to_dict(tick))}\n\n"
        finally:
            sb_unsubscribe(sym, on_tick)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Spread Capture Engine endpoints ───────────────────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_FRONTEND_DIST = _ROOT / "frontend" / "dist"
_FRONTEND_ASSETS = _FRONTEND_DIST / "assets"

# Mount static assets (JS, CSS, fonts, images from Vite build)
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_ASSETS)), name="static-assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA fallback: return index.html for all non-API, non-static paths.

    - /api/* requests that don't match a registered endpoint get 404.
    - Static files in frontend/dist are served if they exist.
    - All other paths get index.html so react-router can handle client-side routing.
    """
    # /api/* paths that reach here have no matching endpoint → 404
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(status_code=404, detail="Not Found")

    # Prevent path traversal: resolve and verify within frontend/dist
    index_html = _FRONTEND_DIST / "index.html"

    if full_path:
        # Try to serve the exact file from frontend/dist (e.g. favicon.ico, manifest.json)
        requested = (_FRONTEND_DIST / full_path).resolve()
        # Security: ensure resolved path is within frontend/dist
        if requested.is_file() and str(requested).startswith(str(_FRONTEND_DIST.resolve())):
            return FileResponse(str(requested))

    # Fallback: return index.html for client-side routing
    if not index_html.is_file():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_html))
