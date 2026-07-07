from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from mexc_monitor.client import MexcApiError
from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.history_store import init_db, query_recent, resolve_history_db_path
from mexc_monitor.history_worker import start_history_worker, stop_history_worker
from mexc_monitor.klines import fetch_klines_for_market
from mexc_monitor.orderbook import fetch_orderbook_depth
from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.trading import TradingEngine, load_trading_settings
from mexc_monitor.trading.engine_registry import EngineRegistry
from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS
from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.ws_futures import ensure_started_from_settings
from mexc_monitor.ws_futures_orderbook import ensure_futures_orderbook_ws_started
from mexc_monitor.ws_spot_orderbook import ensure_spot_orderbook_ws_started, stop_spot_orderbook_ws

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_METRICS_REF_PUBLIC = _ROOT / "frontend" / "public" / "metrics-reference.json"
_METRICS_REF_SRC = _ROOT / "frontend" / "src" / "data" / "metrics-reference.json"

app = FastAPI(title="MEXC Spread Monitor API", version="0.2.0")
_registry = EngineRegistry()
# Backward-compatible alias: default MEXC/spot engine (created lazily via registry)
_trading_engine = _registry.get_or_create(Exchange.MEXC, Market.SPOT)

from mexc_monitor.spread_capture import SpreadCaptureEngine
_spread_capture_engine = SpreadCaptureEngine()

# ─── Portfolio Risk Manager ───────────────────────────────────────────────────

from mexc_monitor.portfolio_risk import PortfolioRiskManager, PortfolioRiskSettings


class _CaptureAdapter:
    """Adapter for SpreadCaptureEngine → PortfolioRiskManager."""
    @property
    def engine_name(self) -> str:
        return "spread_capture"
    def get_open_notional(self) -> float:
        pos = _spread_capture_engine._position
        if pos.state == "holding" and pos.entry_price > 0:
            return pos.entry_price * pos.entry_qty
        return 0.0
    def get_open_symbols(self) -> list[str]:
        pos = _spread_capture_engine._position
        if pos.state in ("holding", "pending_buy", "pending_sell"):
            return [_spread_capture_engine._settings.symbol]
        return []
    def trigger_kill_switch(self) -> None:
        _spread_capture_engine._settings.kill_switch = True
    def get_status(self) -> dict:
        return _spread_capture_engine.get_status()


class _ArbitrageAdapter:
    """Adapter for ArbitrageEngine → PortfolioRiskManager."""
    @property
    def engine_name(self) -> str:
        return "arbitrage"
    def get_open_notional(self) -> float:
        with _arbitrage_engine._lock:
            return sum(
                p.notional_usdt for p in _arbitrage_engine._positions.values()
                if p.state in ("pending_open", "open")
            )
    def get_open_symbols(self) -> list[str]:
        with _arbitrage_engine._lock:
            return [
                s for s, p in _arbitrage_engine._positions.items()
                if p.state in ("pending_open", "open")
            ]
    def trigger_kill_switch(self) -> None:
        _arbitrage_engine._settings.kill_switch = True
    def get_status(self) -> dict:
        return _arbitrage_engine.get_status()


class _FuturesArbAdapter:
    """Adapter for FuturesArbStrategyEngine → PortfolioRiskManager."""
    @property
    def engine_name(self) -> str:
        return "futures_arb"
    def get_open_notional(self) -> float:
        return _futures_arb_position_mgr.get_total_exposure()
    def get_open_symbols(self) -> list[str]:
        positions = _futures_arb_position_mgr.get_open_positions()
        return list({p.symbol for p in positions})
    def trigger_kill_switch(self) -> None:
        _futures_arb_risk.activate_kill_switch()
    def get_status(self) -> dict:
        return _futures_arb_engine.get_status()


_portfolio_risk = PortfolioRiskManager(PortfolioRiskSettings())


def _resolve_engine(
    exchange: str | None = None, market: str | None = None
) -> TradingEngine:
    """Resolve engine from query params, defaulting to mexc/spot."""
    try:
        ex = Exchange(exchange.lower()) if exchange else Exchange.MEXC
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")
    try:
        mk = Market(market.lower()) if market else Market.SPOT
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid market: {market}")
    return _registry.get_or_create(ex, mk)


@app.on_event("startup")
def _startup_prefetch_futures_ws() -> None:
    ensure_started_from_settings(DEFAULT_SETTINGS)
    ensure_futures_orderbook_ws_started(DEFAULT_SETTINGS)
    ensure_spot_orderbook_ws_started(DEFAULT_SETTINGS)
    if DEFAULT_SETTINGS.history_enabled:
        init_db(resolve_history_db_path(DEFAULT_SETTINGS))
    start_history_worker()
    _portfolio_risk.start()

    # Auto-start engines for exchanges with {EXCHANGE}_TRADING_ENABLED=true
    for ex in Exchange:
        config = EXCHANGE_CONFIGS[ex]
        enabled_val = os.environ.get(f"{config.env_prefix}_TRADING_ENABLED", "")
        if enabled_val.strip().lower() in ("true", "1", "yes"):
            engine = _registry.get_or_create(ex, Market.SPOT)
            st = engine.status().get("settings", {})
            if bool(st.get("enabled")):
                engine.start()


@app.on_event("shutdown")
def _shutdown_workers() -> None:
    _portfolio_risk.stop()
    _registry.shutdown_all()
    stop_history_worker()
    stop_spot_orderbook_ws()

_SNAPSHOT_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_SNAPSHOT_CACHE_TTL_SEC", "3")))
_snapshot_cache_lock = threading.Lock()
_snapshot_cache: dict[str, tuple[float, dict]] = {}

_DEPTH_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_DEPTH_CACHE_TTL_SEC", "1")))
_depth_cache_lock = threading.Lock()
_depth_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_ADMIN_TOKEN = str(os.environ.get("ADMIN_TOKEN", "")).strip()


def _require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    if not _ADMIN_TOKEN:
        return
    provided = (x_admin_token or "").strip()
    if not provided or provided != _ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")


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


def _get_snapshot_payload(cache_key: str, *, bypass_cache: bool, builder) -> dict:
    if _SNAPSHOT_CACHE_TTL_SEC <= 0 or bypass_cache:
        payload = builder()
        out = dict(payload)
        out["cache_hit"] = False
        return out
    now = time.monotonic()
    with _snapshot_cache_lock:
        cached = _snapshot_cache.get(cache_key)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                out = dict(payload)
                out["cache_hit"] = True
                return out
    payload = builder()
    if payload.get("ok"):
        with _snapshot_cache_lock:
            _snapshot_cache[cache_key] = (now + _SNAPSHOT_CACHE_TTL_SEC, payload)
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
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    market: str = Query("spot", description="spot, futures или cross"),
    exchange: str = Query("mexc", description="Биржа: mexc, binance, bybit, okx, gateio, htx, bitget, asterdex, lighter, dydx, hyperliquid"),
    nocache: bool = Query(
        False,
        description="Пропустить серверный кэш снимка (принудительно сходить на биржу)",
    ),
) -> dict:
    ex = (exchange or "").strip().lower()
    if ex not in _SUPPORTED_EXCHANGES:
        from fastapi.responses import JSONResponse
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
        out = _get_snapshot_payload(
            cache_key,
            bypass_cache=nocache,
            builder=lambda: _build_exchange_snapshot_payload(ex, m),
        )

    if not out.get("ok"):
        logger.warning(
            "snapshot response not ok exchange=%s error=%s",
            ex,
            out.get("error"),
        )
    return out


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


@app.get("/api/trading/exchanges")
def trading_exchanges() -> dict:
    """Return all supported exchanges with availability status."""
    result = []
    for ex in Exchange:
        config = EXCHANGE_CONFIGS[ex]
        has_creds = bool(
            os.environ.get(f"{config.env_prefix}_API_KEY")
            and os.environ.get(f"{config.env_prefix}_API_SECRET")
        )
        result.append({
            "exchange": ex.value,
            "available": has_creds,
            "paper_only": not has_creds,
            "markets": ["spot", "futures"],
            "spot_base_url": config.spot_base_url,
            "futures_base_url": config.futures_base_url,
            "order_types": ["LIMIT", "MARKET"],
        })
    return {"ok": True, "exchanges": result}


@app.get("/api/trading/engines")
def trading_engines(_: None = Depends(_require_admin_token)) -> dict:
    """Return all registered engine instances."""
    return {"ok": True, "engines": _registry.list_engines()}


@app.get("/api/trading/status")
def trading_status(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.status()}


@app.post("/api/trading/start")
def trading_start(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.start()}


@app.post("/api/trading/stop")
def trading_stop(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.stop()}


@app.post("/api/trading/kill-switch")
def trading_kill_switch(
    enabled: bool = Query(..., description="true -> kill switch ON (stop orders)"),
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.set_kill_switch(enabled)}


@app.post("/api/trading/run-once")
def trading_run_once(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.run_once()}


@app.post("/api/trading/reconcile")
def trading_reconcile(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Reconcile in-memory positions with exchange (live mode)."""
    engine = _resolve_engine(exchange, market)
    return engine.reconcile()


@app.get("/api/trading/runtime-settings")
def trading_runtime_settings(
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, "settings": engine.status().get("settings", {})}


@app.patch("/api/trading/runtime-settings")
def trading_runtime_settings_update(
    payload: dict,
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    try:
        out = engine.update_runtime_settings(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **out}


@app.get("/api/trading/events")
def trading_events(
    limit: int = Query(100, ge=1, le=1000),
    exchange: str | None = Query(None, description="Exchange name (default: mexc)"),
    market: str | None = Query(None, description="Market type (default: spot)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    rows = engine.read_recent_events(limit=limit)
    return {"ok": True, "count": len(rows), "rows": rows}


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
def spread_stream(
    symbol: str = Query(..., min_length=2, max_length=40),
) -> StreamingResponse:
    """
    SSE (Server-Sent Events) поток обновлений спреда в реальном времени.
    Клиент подключается и получает события при каждом изменении bid/ask.
    """
    import asyncio
    import queue

    sym = symbol.strip().upper()
    q: queue.Queue[SpreadTick | None] = queue.Queue(maxsize=500)

    def on_tick(_symbol: str, tick: SpreadTick) -> None:
        try:
            q.put_nowait(tick)
        except queue.Full:
            # Отбрасываем старые если клиент не успевает
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(tick)
            except queue.Full:
                pass

    sb_subscribe(sym, on_tick)

    def event_generator():
        try:
            # Отправляем последний известный тик сразу
            latest = sb_get_latest(sym)
            if latest:
                data = json.dumps(_tick_to_dict(latest))
                yield f"data: {data}\n\n"
            while True:
                try:
                    tick = q.get(timeout=15.0)
                except queue.Empty:
                    # Keepalive
                    yield ": keepalive\n\n"
                    continue
                if tick is None:
                    break
                data = json.dumps(_tick_to_dict(tick))
                yield f"data: {data}\n\n"
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


@app.get("/api/capture/status")
def capture_status() -> dict:
    """Статус движка сбора спреда."""
    return {"ok": True, **_spread_capture_engine.get_status()}


@app.patch("/api/capture/settings")
def capture_update_settings(payload: dict, _: None = Depends(_require_admin_token)) -> dict:
    """Обновить настройки стратегии."""
    try:
        return {"ok": True, **_spread_capture_engine.update_settings(payload)}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/capture/start")
def capture_start(_: None = Depends(_require_admin_token)) -> dict:
    """Запустить движок сбора спреда."""
    return {"ok": True, **_spread_capture_engine.start()}


@app.post("/api/capture/stop")
def capture_stop(_: None = Depends(_require_admin_token)) -> dict:
    """Остановить движок."""
    return {"ok": True, **_spread_capture_engine.stop()}


@app.post("/api/capture/reset-position")
def capture_reset_position(_: None = Depends(_require_admin_token)) -> dict:
    """Аварийный сброс позиции."""
    return {"ok": True, **_spread_capture_engine.reset_position()}


@app.post("/api/capture/reset-stats")
def capture_reset_stats(_: None = Depends(_require_admin_token)) -> dict:
    """Сброс статистики."""
    return {"ok": True, **_spread_capture_engine.reset_stats()}


@app.get("/api/capture/pnl")
def capture_current_pnl() -> dict:
    """Текущий PNL открытой позиции."""
    pnl = _spread_capture_engine.get_current_pnl()
    return {"ok": True, "pnl": pnl}


@app.get("/api/capture/trades")
def capture_trades(limit: int = Query(50, ge=1, le=500)) -> dict:
    """История сделок."""
    trades = _spread_capture_engine.get_trades(limit=limit)
    return {"ok": True, "count": len(trades), "trades": trades}


@app.get("/api/capture/events")
def capture_events(limit: int = Query(50, ge=1, le=200)) -> dict:
    """Лог событий движка."""
    events = _spread_capture_engine.get_events(limit=limit)
    return {"ok": True, "count": len(events), "events": events}


@app.get("/api/capture/signals")
def capture_signals(limit: int = Query(20, ge=1, le=100)) -> dict:
    """Последние сигналы входа."""
    signals = _spread_capture_engine.get_signals(limit=limit)
    return {"ok": True, "count": len(signals), "signals": signals}


# ─── AsterDEX Integration endpoints ───────────────────────────────────────────

from mexc_monitor.aster import AsterPublicClient, AsterApiError as AsterError

_aster_public = AsterPublicClient()


@app.get("/api/aster/ping")
def aster_ping() -> dict:
    """Проверка связи с AsterDEX."""
    ok = _aster_public.ping()
    return {"ok": ok, "exchange": "asterdex"}


@app.get("/api/aster/symbols")
def aster_symbols() -> dict:
    """Список торгуемых символов на AsterDEX."""
    try:
        symbols = _aster_public.get_symbols()
        return {"ok": True, "symbols": symbols, "count": len(symbols)}
    except AsterError as e:
        return {"ok": False, "error": str(e), "symbols": [], "count": 0}


@app.get("/api/aster/book-ticker")
def aster_book_ticker(
    symbol: str | None = Query(None, description="Символ (BTCUSDT) или пусто для всех"),
) -> dict:
    """Лучшие bid/ask на AsterDEX."""
    try:
        tickers = _aster_public.book_ticker(symbol=symbol)
        rows = [
            {
                "symbol": t.symbol,
                "bid_price": t.bid_price,
                "bid_qty": t.bid_qty,
                "ask_price": t.ask_price,
                "ask_qty": t.ask_qty,
                "time_ms": t.time_ms,
                "spread_abs": t.ask_price - t.bid_price,
                "mid": (t.bid_price + t.ask_price) / 2,
                "spread_bps": (
                    10_000 * (t.ask_price - t.bid_price) / ((t.bid_price + t.ask_price) / 2)
                    if (t.bid_price + t.ask_price) > 0 else None
                ),
            }
            for t in tickers
        ]
        return {"ok": True, "count": len(rows), "tickers": rows}
    except AsterError as e:
        return {"ok": False, "error": str(e), "count": 0, "tickers": []}


@app.get("/api/aster/ticker-24h")
def aster_ticker_24h(
    symbol: str | None = Query(None, description="Символ или пусто для всех"),
) -> dict:
    """24h статистика AsterDEX."""
    try:
        tickers = _aster_public.ticker_24h(symbol=symbol)
        rows = [
            {
                "symbol": t.symbol,
                "last_price": t.last_price,
                "price_change_percent": t.price_change_percent,
                "high_price": t.high_price,
                "low_price": t.low_price,
                "volume": t.volume,
                "quote_volume": t.quote_volume,
            }
            for t in tickers
        ]
        return {"ok": True, "count": len(rows), "tickers": rows}
    except AsterError as e:
        return {"ok": False, "error": str(e), "count": 0, "tickers": []}


@app.get("/api/aster/depth")
def aster_depth(
    symbol: str = Query(..., min_length=3, max_length=40),
    limit: int = Query(20, ge=5, le=1000),
) -> dict:
    """Стакан AsterDEX."""
    try:
        data = _aster_public.depth(symbol, limit=limit)
        return {"ok": True, "symbol": symbol.upper(), **data}
    except AsterError as e:
        return {"ok": False, "error": str(e), "symbol": symbol.upper(), "bids": [], "asks": []}


@app.get("/api/aster/klines")
def aster_klines(
    symbol: str = Query(..., min_length=3, max_length=40),
    interval: str = Query("1h"),
    limit: int = Query(500, ge=1, le=1500),
) -> dict:
    """Свечи AsterDEX."""
    try:
        raw = _aster_public.klines(symbol, interval=interval, limit=limit)
        candles = []
        for c in raw:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                continue
            candles.append({
                "time": int(c[0]) // 1000,  # ms -> sec for lightweight-charts
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "interval": interval,
            "count": len(candles),
            "candles": candles,
        }
    except AsterError as e:
        return {"ok": False, "error": str(e), "symbol": symbol.upper(), "interval": interval, "candles": []}


@app.get("/api/aster/funding")
def aster_funding(
    symbol: str | None = Query(None, description="Символ или пусто для всех"),
) -> dict:
    """Mark price и funding rate AsterDEX."""
    try:
        data = _aster_public.premium_index(symbol=symbol)
        rows = [
            {
                "symbol": f.symbol,
                "mark_price": f.mark_price,
                "index_price": f.index_price,
                "last_funding_rate": f.last_funding_rate,
                "next_funding_time": f.next_funding_time,
            }
            for f in data
        ]
        return {"ok": True, "count": len(rows), "funding": rows}
    except AsterError as e:
        return {"ok": False, "error": str(e), "count": 0, "funding": []}


@app.get("/api/aster/cross-spread")
def aster_cross_spread(
    symbol: str = Query(..., min_length=3, max_length=40, description="Символ (BTCUSDT)"),
) -> dict:
    """
    Межбиржевой спред MEXC ↔ AsterDEX для одного символа.
    Сравнивает лучшие bid/ask на обеих площадках.
    """
    sym = symbol.strip().upper()
    result: dict[str, Any] = {"ok": True, "symbol": sym, "mexc": None, "aster": None, "cross_spread": None}

    # AsterDEX
    try:
        aster_tickers = _aster_public.book_ticker(symbol=sym)
        if aster_tickers:
            t = aster_tickers[0]
            result["aster"] = {
                "bid": t.bid_price,
                "ask": t.ask_price,
                "bid_qty": t.bid_qty,
                "ask_qty": t.ask_qty,
                "mid": (t.bid_price + t.ask_price) / 2,
                "spread_bps": (
                    10_000 * (t.ask_price - t.bid_price) / ((t.bid_price + t.ask_price) / 2)
                    if (t.bid_price + t.ask_price) > 0 else None
                ),
            }
    except AsterError as e:
        result["aster_error"] = str(e)

    # MEXC (из spread buffer или snapshot)
    from mexc_monitor.spread_buffer import get_latest as sb_latest
    mexc_tick = sb_latest(sym)
    if mexc_tick:
        result["mexc"] = {
            "bid": mexc_tick.bid,
            "ask": mexc_tick.ask,
            "bid_qty": mexc_tick.bid_qty,
            "ask_qty": mexc_tick.ask_qty,
            "mid": mexc_tick.mid,
            "spread_bps": mexc_tick.spread_bps,
        }
    else:
        # Fallback: try futures symbol format for MEXC
        from mexc_monitor.spread_buffer import get_latest as sb_latest2
        fut_sym = sym.replace("USDT", "_USDT") if "USDT" in sym and "_" not in sym else sym
        mexc_tick2 = sb_latest2(fut_sym)
        if mexc_tick2:
            result["mexc"] = {
                "bid": mexc_tick2.bid,
                "ask": mexc_tick2.ask,
                "bid_qty": mexc_tick2.bid_qty,
                "ask_qty": mexc_tick2.ask_qty,
                "mid": mexc_tick2.mid,
                "spread_bps": mexc_tick2.spread_bps,
            }

    # Cross-spread calculation
    if result.get("mexc") and result.get("aster"):
        mexc_data = result["mexc"]
        aster_data = result["aster"]
        # Арбитраж: купить дешевле на одной, продать дороже на другой
        # Buy MEXC bid, Sell Aster ask (или наоборот)
        mexc_mid = mexc_data["mid"]
        aster_mid = aster_data["mid"]
        basis_abs = aster_mid - mexc_mid
        basis_bps = (10_000 * basis_abs / mexc_mid) if mexc_mid > 0 else None
        # Executable spread: buy on cheaper ask, sell on more expensive bid
        buy_mexc_sell_aster = aster_data["bid"] - mexc_data["ask"]  # profit if positive
        buy_aster_sell_mexc = mexc_data["bid"] - aster_data["ask"]  # profit if positive
        result["cross_spread"] = {
            "basis_abs": basis_abs,
            "basis_bps": basis_bps,
            "buy_mexc_sell_aster_abs": buy_mexc_sell_aster,
            "buy_mexc_sell_aster_bps": (10_000 * buy_mexc_sell_aster / mexc_mid) if mexc_mid > 0 else None,
            "buy_aster_sell_mexc_abs": buy_aster_sell_mexc,
            "buy_aster_sell_mexc_bps": (10_000 * buy_aster_sell_mexc / aster_mid) if aster_mid > 0 else None,
        }

    return result


# ─── AsterDEX Private (Trading) endpoints ─────────────────────────────────────

import os as _os
_ASTER_API_KEY = _os.environ.get("ASTER_API_KEY", "").strip()
_ASTER_API_SECRET = _os.environ.get("ASTER_API_SECRET", "").strip()


@app.get("/api/aster/account")
def aster_account(_: None = Depends(_require_admin_token)) -> dict:
    """Информация об аккаунте AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            data = client.get_account()
        return {"ok": True, "account": data}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/aster/positions")
def aster_positions(
    symbol: str | None = Query(None),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Открытые позиции на AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            positions = client.get_positions(symbol=symbol)
        # Filter non-zero positions
        active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        return {"ok": True, "positions": active, "count": len(active)}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e), "positions": [], "count": 0}


@app.get("/api/aster/open-orders")
def aster_open_orders(
    symbol: str | None = Query(None),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Открытые ордера на AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            orders = client.get_open_orders(symbol=symbol)
        return {"ok": True, "orders": orders, "count": len(orders)}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e), "orders": [], "count": 0}


@app.post("/api/aster/order")
def aster_place_order(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Размещение ордера на AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    symbol = str(payload.get("symbol", "")).strip().upper()
    side = str(payload.get("side", "")).strip().upper()
    order_type = str(payload.get("type", "LIMIT")).strip().upper()
    quantity = float(payload.get("quantity", 0))
    price = payload.get("price")
    time_in_force = str(payload.get("timeInForce", "GTC")).strip().upper()
    reduce_only = bool(payload.get("reduceOnly", False))
    client_order_id = payload.get("newClientOrderId")

    if not symbol or not side or quantity <= 0:
        return {"ok": False, "error": "symbol, side, quantity are required"}

    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            result = client.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=float(price) if price else None,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                client_order_id=client_order_id,
            )
        return {"ok": True, "order": result}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e)}


@app.delete("/api/aster/order")
def aster_cancel_order(
    symbol: str = Query(..., min_length=3),
    orderId: int | None = Query(None),
    origClientOrderId: str | None = Query(None),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Отмена ордера на AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            result = client.cancel_order(
                symbol=symbol,
                order_id=orderId,
                client_order_id=origClientOrderId,
            )
        return {"ok": True, "result": result}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/aster/leverage")
def aster_set_leverage(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Установить плечо на AsterDEX."""
    if not _ASTER_API_KEY or not _ASTER_API_SECRET:
        return {"ok": False, "error": "ASTER_API_KEY/ASTER_API_SECRET not configured"}
    from mexc_monitor.aster.private_client import AsterPrivateClient, AsterPrivateApiError
    symbol = str(payload.get("symbol", "")).strip().upper()
    leverage = int(payload.get("leverage", 1))
    if not symbol:
        return {"ok": False, "error": "symbol is required"}
    try:
        with AsterPrivateClient(api_key=_ASTER_API_KEY, api_secret=_ASTER_API_SECRET) as client:
            result = client.set_leverage(symbol, leverage)
        return {"ok": True, "result": result}
    except AsterPrivateApiError as e:
        return {"ok": False, "error": str(e)}


# ─── Telegram Alerts endpoints ─────────────────────────────────────────────────

from mexc_monitor.alerts import AlertService, load_alert_config

_alert_service = AlertService(load_alert_config())


@app.get("/api/alerts/settings")
def alerts_get_settings(_: None = Depends(_require_admin_token)) -> dict:
    """Получить настройки алертов (токен маскирован)."""
    return {"ok": True, "config": _alert_service.get_config()}


@app.patch("/api/alerts/settings")
def alerts_update_settings(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Обновить настройки алертов."""
    try:
        config = _alert_service.update_config(payload)
        return {"ok": True, "config": config}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/alerts/test")
def alerts_test(_: None = Depends(_require_admin_token)) -> dict:
    """Отправить тестовое сообщение в Telegram."""
    success = _alert_service.test_connection()
    if success:
        return {"ok": True, "message": "Тестовое сообщение отправлено"}
    return {"ok": False, "message": "Не удалось отправить. Проверьте bot_token и chat_id."}


# ─── AsterDEX WebSocket Management endpoints ──────────────────────────────────

from mexc_monitor.aster.ws_client import (
    ensure_aster_ws_started,
    get_aster_ws_client,
    stop_aster_ws,
)


def _start_aster_ws_from_config() -> None:
    """Запуск AsterDEX WS из конфигурации (вызывается на startup)."""
    try:
        config_path = Path(__file__).resolve().parent.parent / "config" / "external_apis.json"
        if not config_path.is_file():
            return
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        aster_cfg = raw.get("asterdex", {})
        ws_cfg = aster_cfg.get("ws", {})
        if not ws_cfg.get("enabled", False):
            return
        url = str(ws_cfg.get("url", "wss://fstream.asterdex.com/ws"))
        symbols = ws_cfg.get("symbols", [])
        ping = float(ws_cfg.get("ping_interval_sec", 30))
        if symbols:
            ensure_aster_ws_started(url=url, symbols=symbols, ping_interval_sec=ping)
            logger.info("AsterDEX WS: auto-started with %d symbols", len(symbols))
    except Exception as e:
        logger.warning("AsterDEX WS auto-start failed: %s", e)


# Hook into existing startup
_original_startup = _startup_prefetch_futures_ws


@app.on_event("startup")
def _startup_with_aster_ws() -> None:
    _start_aster_ws_from_config()


@app.on_event("shutdown")
def _shutdown_aster_ws() -> None:
    stop_aster_ws()


@app.get("/api/aster/ws/status")
def aster_ws_status() -> dict:
    """Статус WebSocket-подключения к AsterDEX."""
    client = get_aster_ws_client()
    if client is None:
        return {"ok": True, "connected": False, "subscribed_symbols": [], "count": 0}
    return {
        "ok": True,
        "connected": client.connected,
        "subscribed_symbols": client.get_subscribed_symbols(),
        "count": len(client.get_subscribed_symbols()),
    }


@app.post("/api/aster/ws/subscribe")
def aster_ws_subscribe(
    symbol: str = Query(..., min_length=2, max_length=40),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Подписаться на bookTicker AsterDEX для символа."""
    sym = symbol.strip().upper()
    client = get_aster_ws_client()
    if client is None:
        # Запустить клиент с этим символом
        client = ensure_aster_ws_started(symbols=[sym])
    else:
        client.subscribe(sym)
    return {"ok": True, "symbol": sym, "subscribed_symbols": client.get_subscribed_symbols()}


@app.post("/api/aster/ws/unsubscribe")
def aster_ws_unsubscribe(
    symbol: str = Query(..., min_length=2, max_length=40),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Отписаться от bookTicker AsterDEX."""
    sym = symbol.strip().upper()
    client = get_aster_ws_client()
    if client is None:
        return {"ok": True, "symbol": sym, "subscribed_symbols": []}
    client.unsubscribe(sym)
    return {"ok": True, "symbol": sym, "subscribed_symbols": client.get_subscribed_symbols()}


# ─── Cross-Exchange Arbitrage Engine endpoints ─────────────────────────────────

from mexc_monitor.arbitrage.engine import ArbitrageEngine
from mexc_monitor.arbitrage.models import ArbitrageSettings

_arbitrage_engine = ArbitrageEngine()


@app.get("/api/arbitrage/status")
def arbitrage_status(_: None = Depends(_require_admin_token)) -> dict:
    """Статус арбитражного движка."""
    return {"ok": True, **_arbitrage_engine.get_status()}


@app.post("/api/arbitrage/start")
def arbitrage_start(_: None = Depends(_require_admin_token)) -> dict:
    """Запустить арбитражный движок."""
    return {"ok": True, **_arbitrage_engine.start()}


@app.post("/api/arbitrage/stop")
def arbitrage_stop(_: None = Depends(_require_admin_token)) -> dict:
    """Остановить арбитражный движок."""
    return {"ok": True, **_arbitrage_engine.stop()}


@app.post("/api/arbitrage/kill-switch")
def arbitrage_kill_switch(
    enabled: bool = Query(...),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Kill switch арбитража."""
    return {"ok": True, **_arbitrage_engine.set_kill_switch(enabled)}


@app.patch("/api/arbitrage/settings")
def arbitrage_update_settings(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Обновить настройки арбитража."""
    try:
        return {"ok": True, **_arbitrage_engine.update_settings(payload)}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/arbitrage/positions")
def arbitrage_positions(_: None = Depends(_require_admin_token)) -> dict:
    """Открытые арбитражные позиции."""
    positions = _arbitrage_engine.get_positions()
    return {"ok": True, "positions": positions, "count": len(positions)}


@app.get("/api/arbitrage/trades")
def arbitrage_trades(
    limit: int = Query(50, ge=1, le=500),
    _: None = Depends(_require_admin_token),
) -> dict:
    """История арбитражных сделок."""
    trades = _arbitrage_engine.get_trades(limit=limit)
    return {"ok": True, "trades": trades, "count": len(trades)}


@app.get("/api/arbitrage/events")
def arbitrage_events(
    limit: int = Query(50, ge=1, le=200),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Лог событий арбитража."""
    events = _arbitrage_engine.get_events(limit=limit)
    return {"ok": True, "events": events, "count": len(events)}


# ─── Cross-Spread History endpoints ───────────────────────────────────────────

from mexc_monitor.cross_spread_store import (
    query_cross_spread_history,
    start_cross_spread_worker,
    stop_cross_spread_worker,
)


def _start_cross_spread_from_config() -> None:
    """Запуск CrossSpreadWorker из конфигурации."""
    try:
        config_path = Path(__file__).resolve().parent.parent / "config" / "external_apis.json"
        if not config_path.is_file():
            return
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        cs_cfg = raw.get("cross_spread_history", {})
        if not cs_cfg.get("enabled", False):
            return
        db_path = str(cs_cfg.get("db_path", "data/cross_spread_history.sqlite"))
        interval = float(cs_cfg.get("interval_sec", 60))
        retention = int(cs_cfg.get("retention_days", 30))
        start_cross_spread_worker(db_path=db_path, interval_sec=interval, retention_days=retention)
        logger.info("CrossSpreadWorker: auto-started")
    except Exception as e:
        logger.warning("CrossSpreadWorker auto-start failed: %s", e)


@app.on_event("startup")
def _startup_cross_spread_worker() -> None:
    _start_cross_spread_from_config()


@app.on_event("shutdown")
def _shutdown_cross_spread_worker() -> None:
    stop_cross_spread_worker()


@app.get("/api/cross-spread/history")
def cross_spread_history(
    symbol: str | None = Query(None, description="Символ (BTCUSDT)"),
    since: str | None = Query(None, description="ISO8601 начало периода"),
    until: str | None = Query(None, description="ISO8601 конец периода"),
    limit: int = Query(2000, ge=10, le=5000),
) -> dict:
    """История межбиржевого спреда MEXC ↔ AsterDEX."""
    # Determine db path from config
    db_path = "data/cross_spread_history.sqlite"
    try:
        config_path = Path(__file__).resolve().parent.parent / "config" / "external_apis.json"
        if config_path.is_file():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            db_path = raw.get("cross_spread_history", {}).get("db_path", db_path)
    except Exception:
        pass

    rows = query_cross_spread_history(
        db_path=db_path,
        symbol=symbol,
        since_iso=since,
        until_iso=until,
        limit=limit,
    )
    return {
        "ok": True,
        "symbol": symbol.upper() if symbol else None,
        "count": len(rows),
        "rows": rows,
    }


# ─── Futures/Spot Arbitrage endpoints ─────────────────────────────────────────

from mexc_monitor.futures_arb.config import load_futures_arb_settings
from mexc_monitor.futures_arb.strategy_engine import FuturesArbStrategyEngine
from mexc_monitor.futures_arb.basis_calculator import BasisCalculator
from mexc_monitor.futures_arb.funding_tracker import FundingTracker
from mexc_monitor.futures_arb.position_manager import PositionManager
from mexc_monitor.futures_arb.risk_controller import RiskController
from mexc_monitor.futures_arb.basis_store import BasisHistoryStore

# Initialize futures-arb components (lazy — engine starts on POST /start)
_futures_arb_settings = load_futures_arb_settings(validate=False)
_futures_arb_basis_calc = BasisCalculator(_futures_arb_settings)
_futures_arb_funding = FundingTracker(_futures_arb_settings)
_futures_arb_position_mgr = PositionManager(state_file="data/futures_arb_state.json")
_futures_arb_risk = RiskController(_futures_arb_settings)
_futures_arb_basis_store = BasisHistoryStore(
    db_path="data/basis_history.db",
    interval_sec=_futures_arb_settings.basis_history_interval_sec,
    retention_days=_futures_arb_settings.basis_history_retention_days,
    basis_calculator=_futures_arb_basis_calc,
)
_futures_arb_engine = FuturesArbStrategyEngine(
    settings=_futures_arb_settings,
    basis_calculator=_futures_arb_basis_calc,
    funding_tracker=_futures_arb_funding,
    position_manager=_futures_arb_position_mgr,
    risk_controller=_futures_arb_risk,
)

# Register all engines with PortfolioRiskManager
_portfolio_risk.register_engine(_CaptureAdapter())
_portfolio_risk.register_engine(_ArbitrageAdapter())
_portfolio_risk.register_engine(_FuturesArbAdapter())


@app.get("/api/portfolio-risk/status")
def portfolio_risk_status() -> dict:
    """Portfolio risk status: aggregated exposure, drawdown, alerts."""
    status = _portfolio_risk.get_status()
    return {
        "ok": True,
        "total_exposure_usdt": status.total_exposure_usdt,
        "engine_count": status.engine_count,
        "positions_by_symbol": status.positions_by_symbol,
        "daily_drawdown_usdt": status.daily_drawdown_usdt,
        "kill_switch_active": status.kill_switch_active,
        "alerts": status.alerts,
        "all_clear": status.all_clear,
    }


@app.post("/api/portfolio-risk/kill-switch")
def portfolio_risk_kill_switch(
    _: None = Depends(_require_admin_token),
) -> dict:
    """Activate global kill switch across all engines."""
    _portfolio_risk.activate_kill_switch(reason="api_request")
    return {"ok": True, "kill_switch_active": True}


@app.post("/api/portfolio-risk/deactivate-kill-switch")
def portfolio_risk_deactivate(
    _: None = Depends(_require_admin_token),
) -> dict:
    """Deactivate global kill switch."""
    _portfolio_risk.deactivate_kill_switch()
    return {"ok": True, "kill_switch_active": False}


@app.get("/api/futures-arb/status")
def futures_arb_status() -> dict:
    """Статус движка Futures/Spot Arbitrage + текущие базисы."""
    status = _futures_arb_engine.get_status()
    stats = _futures_arb_position_mgr.get_stats()
    return {
        "ok": True,
        **status,
        "stats": {
            "total_trades": stats.total_trades,
            "win_rate": stats.win_rate,
            "total_net_pnl_usdt": stats.total_net_pnl_usdt,
            "total_funding_earned": stats.total_funding_earned,
        },
    }


@app.get("/api/futures-arb/positions")
def futures_arb_positions() -> dict:
    """Открытые позиции с real-time PNL."""
    from dataclasses import asdict
    positions = _futures_arb_position_mgr.get_open_positions()
    return {
        "ok": True,
        "positions": [asdict(p) for p in positions],
        "count": len(positions),
    }


@app.get("/api/futures-arb/history")
def futures_arb_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Закрытые позиции с полной разбивкой PNL."""
    from dataclasses import asdict
    positions = _futures_arb_position_mgr.get_closed_positions(limit=limit, offset=offset)
    return {
        "ok": True,
        "positions": [asdict(p) for p in positions],
        "count": len(positions),
    }


@app.post("/api/futures-arb/start")
def futures_arb_start(_: None = Depends(_require_admin_token)) -> dict:
    """Запустить движок Futures/Spot Arbitrage."""
    _futures_arb_basis_calc.start()
    _futures_arb_funding.start()
    _futures_arb_basis_store.start()
    result = _futures_arb_engine.start()
    return {"ok": True, **result}


@app.post("/api/futures-arb/stop")
def futures_arb_stop(_: None = Depends(_require_admin_token)) -> dict:
    """Остановить движок Futures/Spot Arbitrage."""
    result = _futures_arb_engine.stop()
    _futures_arb_basis_store.stop()
    _futures_arb_funding.stop()
    _futures_arb_basis_calc.stop()
    _futures_arb_position_mgr.serialize_state()
    return {"ok": True, **result}


@app.patch("/api/futures-arb/settings")
def futures_arb_update_settings(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Обновить конфигурацию в runtime."""
    result = _futures_arb_engine.update_settings(payload)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return {"ok": True, **result}


@app.get("/api/futures-arb/basis-history")
def futures_arb_basis_history(
    symbol: str = Query(...),
    exchange_combo: str = Query("mexc_spot+mexc_futures"),
    since: str | None = Query(None),
    until: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    """История базиса для графика."""
    rows = _futures_arb_basis_store.query_history(
        symbol=symbol.upper(),
        exchange_combo=exchange_combo,
        since=since,
        until=until,
        limit=limit,
    )
    return {"ok": True, "rows": rows, "count": len(rows)}


@app.post("/api/futures-arb/close-position")
def futures_arb_close_position(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Ручное закрытие позиции."""
    position_id = payload.get("position_id")
    if not position_id:
        raise HTTPException(status_code=400, detail="position_id required")
    result = _futures_arb_engine.close_position_manual(position_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {"ok": True, **result}


# ─── Lead-Lag Arbitrage endpoints ───────────────────────────────────────────────

from mexc_monitor.lead_lag.engine import LeadLagEngine

_lead_lag_engine: LeadLagEngine | None = None
_lead_lag_engine_lock = threading.Lock()


def _get_lead_lag_engine() -> LeadLagEngine:
    """Lazy-initialize the LeadLagEngine singleton."""
    global _lead_lag_engine
    if _lead_lag_engine is None:
        with _lead_lag_engine_lock:
            if _lead_lag_engine is None:
                _lead_lag_engine = LeadLagEngine()
    return _lead_lag_engine


@app.get("/api/lead-lag/status")
def lead_lag_status() -> dict:
    """Статус движка lead-lag: running, connections, symbols, signals, uptime."""
    engine = _get_lead_lag_engine()
    return engine.get_status_info()


@app.get("/api/lead-lag/signals")
def lead_lag_signals(
    active: bool = Query(False, description="Только активные сигналы"),
    symbol: str | None = Query(None, description="Фильтр по символу"),
    limit: int = Query(50, ge=1, le=1000, description="Лимит записей (1-1000)"),
) -> list[dict]:
    """Список сигналов, отсортированных по created_at DESC."""
    engine = _get_lead_lag_engine()

    if active:
        signals = engine.get_active_signals()
    else:
        signals = engine.get_recent_signals(limit=limit)

    # Filter by symbol if specified
    if symbol:
        signals = [s for s in signals if s.symbol == symbol]

    # Apply limit
    signals = signals[:limit]

    # Serialize
    from dataclasses import asdict
    result = []
    for sig in signals:
        d = asdict(sig)
        # Convert enums to string values
        d["direction"] = sig.direction.value if hasattr(sig.direction, "value") else sig.direction
        d["status"] = sig.status.value if hasattr(sig.status, "value") else sig.status
        result.append(d)

    return result


@app.get("/api/lead-lag/stats")
def lead_lag_stats(
    window_hours: int = Query(24, ge=1, le=168, description="Окно статистики (1-168 часов)"),
) -> dict:
    """Агрегированная статистика за указанное окно."""
    engine = _get_lead_lag_engine()
    stats = engine.get_stats(window_hours=window_hours)

    if stats is None:
        return {
            "window_hours": window_hours,
            "total_signals": 0,
            "resolved_signals": 0,
            "expired_signals": 0,
            "win_rate": None,
            "avg_lag_ms": None,
            "median_lag_ms": None,
            "avg_theoretical_pnl_bps": None,
            "total_theoretical_pnl_bps": 0.0,
            "signals_per_hour": 0.0,
            "top_symbols": [],
        }

    from dataclasses import asdict
    return asdict(stats)


@app.get("/api/lead-lag/prices")
def lead_lag_prices(
    symbol: str = Query(..., min_length=1, description="Символ (BTCUSDT)"),
) -> dict:
    """Mid-цены по всем биржам для указанного символа."""
    engine = _get_lead_lag_engine()
    prices = engine.get_prices(symbol.strip().upper())

    if prices is None:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found in lead-lag monitoring",
        )

    return {
        "symbol": symbol.strip().upper(),
        "prices": prices,
    }


@app.get("/api/lead-lag/lag-estimates")
def lead_lag_estimates() -> list[dict]:
    """Текущие оценки lag для всех символов."""
    engine = _get_lead_lag_engine()
    return engine.get_lag_estimates()


@app.post("/api/lead-lag/start")
def lead_lag_start(_: None = Depends(_require_admin_token)) -> dict:
    """Запуск движка lead-lag (идемпотентно)."""
    engine = _get_lead_lag_engine()
    error = engine.start()
    if error:
        raise HTTPException(status_code=400, detail=error)
    return engine.get_status_info()


@app.post("/api/lead-lag/stop")
def lead_lag_stop(_: None = Depends(_require_admin_token)) -> dict:
    """Остановка движка lead-lag (идемпотентно)."""
    engine = _get_lead_lag_engine()
    engine.stop()
    return engine.get_status_info()


# ─── SPA Fallback (must be AFTER all /api/ routes) ──────────────────────────────

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
