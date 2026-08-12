from __future__ import annotations

import hashlib
import json
from typing import Any
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from mexc_monitor.client import MexcApiError
from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.history_store import init_db, query_recent, resolve_history_db_path
from mexc_monitor.history_worker import start_history_worker, stop_history_worker
from mexc_monitor.klines import fetch_klines_for_market
from mexc_monitor.orderbook import fetch_orderbook_depth
from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.trading.engine import TradingEngine, load_trading_settings
from mexc_monitor.trading.engine_registry import EngineRegistry
from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS
from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.ws_futures import ensure_started_from_settings
from mexc_monitor.ws_futures_orderbook import ensure_futures_orderbook_ws_started
from mexc_monitor.ws_futures_depth_book import (
    ensure_futures_depth_book_ws_started,
    get_fresh_depth_book as get_fresh_futures_depth_book,
    reconcile_futures_depth_book_ws,
    stop_futures_depth_book_ws,
    touch_watchlist as touch_futures_depth_book_watchlist,
)
from mexc_monitor.ws_l2_depth import (
    get_fresh_depth_book as get_fresh_l2_depth_book,
    l2_depth_health,
    reconcile as reconcile_l2_depth,
    stop_all as stop_l2_depth_ws,
    touch_watchlist as touch_l2_depth_watchlist,
)
from mexc_monitor.ws_spot_orderbook import ensure_spot_orderbook_ws_started, stop_spot_orderbook_ws
from mexc_monitor.ws_spot_deals import ensure_spot_deals_ws_started, stop_spot_deals_ws
from mexc_monitor.http_utils import effective_http_proxy, set_runtime_http_proxy
from mexc_monitor.http_shared import reset_clients, set_proxy_resolver
from mexc_monitor.proxy_registry import REGISTRY, KNOWN_EXCHANGES, ProxyValidationError

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent

# ─── Environment & Auth ───────────────────────────────────────────────────────

_ENV_FILE = _ROOT / ".env"
if not _ENV_FILE.exists():
    _generated_token = secrets.token_urlsafe(32)
    _ENV_FILE.write_text(f"ADMIN_TOKEN={_generated_token}\n", encoding="utf-8")
    print(f"[auth] Generated new ADMIN_TOKEN in .env")

load_dotenv(_ENV_FILE)
_ADMIN_TOKEN = str(os.environ.get("ADMIN_TOKEN", "")).strip()

_METRICS_REF_PUBLIC = _ROOT / "frontend" / "public" / "metrics-reference.json"
_METRICS_REF_SRC = _ROOT / "frontend" / "src" / "data" / "metrics-reference.json"

app = FastAPI(title="MEXC Spread Monitor API", version="0.2.0")
_registry = EngineRegistry()
# Backward-compatible alias: default MEXC/spot engine (created lazily via registry)
_trading_engine = _registry.get_or_create(Exchange.MEXC, Market.SPOT)

from mexc_monitor.spread_capture import SpreadCaptureEngine
_spread_capture_engine = SpreadCaptureEngine()

from mexc_monitor.metascalp.client import MetaScalpClient
from mexc_monitor.metascalp.cache import MetaScalpCache
from mexc_monitor.metascalp.poller import MetaScalpPoller
from mexc_monitor.metascalp.ws_bridge import MetaScalpWSBridge
from mexc_monitor.metascalp.auto_trader import MetaScalpAutoTrader
from mexc_monitor.metascalp.density_scanner import MetaScalpDensityScanner
from mexc_monitor.metascalp.participant_detector import ParticipantDetector
from mexc_monitor.metascalp.signal_worker import SignalWorker

_metascalp_client = MetaScalpClient()
_metascalp_cache = MetaScalpCache(default_ttl_sec=10.0)
_metascalp_poller = MetaScalpPoller(cache=_metascalp_cache, client=_metascalp_client, interval_sec=5.0)
# ParticipantDetector — shared между WS bridge (принимает trades) и SignalWorker (читает сигналы)
_metascalp_participant_detector = ParticipantDetector()
_metascalp_ws_bridge = MetaScalpWSBridge(
    cache=_metascalp_cache,
    participant_detector=_metascalp_participant_detector,
)
_metascalp_auto_trader = MetaScalpAutoTrader(client=_metascalp_client)
_metascalp_density_scanner = MetaScalpDensityScanner(_metascalp_client)
_metascalp_signal_worker = SignalWorker(
    client=_metascalp_client,
    alert_service=None,  # Lazy: подключается после старта AlertService (см. startup)
    participant_detector=_metascalp_participant_detector,
)

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
        base = _futures_arb_engine.get_status()
        # Feed realized PnL into the portfolio drawdown calc
        # (_aggregate_pnl reads stats.net_pnl_usdt from each registered engine).
        try:
            pnl = _futures_arb_position_mgr.get_stats().total_net_pnl_usdt
        except Exception:
            pnl = 0.0
        stats = base.get("stats") or {}
        stats["net_pnl_usdt"] = pnl
        base["stats"] = stats
        return base


class _MetaScalpAdapter:
    """Adapter for MetaScalp → PortfolioRiskManager."""
    @property
    def engine_name(self) -> str:
        return "metascalp"
    def get_open_notional(self) -> float:
        total = 0.0
        for conn in _metascalp_client.connections():
            for pos in _metascalp_client.positions(conn.id):
                if pos.status in ("Open", "open"):
                    total += pos.size * pos.avg_price
        return total
    def get_open_symbols(self) -> list[str]:
        symbols: set[str] = set()
        for conn in _metascalp_client.connections():
            for pos in _metascalp_client.positions(conn.id):
                if pos.status in ("Open", "open"):
                    symbols.add(pos.ticker)
        return list(symbols)
    def trigger_kill_switch(self) -> None:
        # Cancel all open orders on all connections
        for conn in _metascalp_client.connections():
            _metascalp_client.cancel_all_orders(conn.id)
    def get_status(self) -> dict:
        positions_count = 0
        orders_count = 0
        for conn in _metascalp_client.connections():
            positions_count += len(_metascalp_client.positions(conn.id))
            orders_count += len(_metascalp_client.orders(conn.id))
        return {
            "stats": {"net_pnl_usdt": 0.0},
            "positions_count": positions_count,
            "orders_count": orders_count,
        }


class _TradingAdapter:
    """Adapter for TradingEngine (default MEXC/spot) → PortfolioRiskManager.

    TradingEngine tracks only a count of open orders, not per-position notional,
    so exposure is estimated as open_orders * order_quote_notional (conservative:
    every open limit order counts as full notional exposure). Realized PnL is not
    tracked by this engine, so it does not contribute to the drawdown calc.
    """
    @property
    def engine_name(self) -> str:
        return "trading"

    def _snapshot(self) -> tuple[int, float, str]:
        st = _trading_engine.status()
        state = st.get("state", {}) or {}
        settings = st.get("settings", {}) or {}
        open_orders = int(state.get("open_orders", 0) or 0)
        notional_per = float(settings.get("order_quote_notional", 0.0) or 0.0)
        symbol = str(settings.get("symbol", "") or "")
        return open_orders, notional_per, symbol

    def get_open_notional(self) -> float:
        open_orders, notional_per, _ = self._snapshot()
        return open_orders * notional_per

    def get_open_symbols(self) -> list[str]:
        open_orders, _, symbol = self._snapshot()
        return [symbol] if open_orders > 0 and symbol else []

    def trigger_kill_switch(self) -> None:
        _trading_engine.set_kill_switch(True)

    def get_status(self) -> dict:
        return _trading_engine.status()


_portfolio_risk = PortfolioRiskManager(PortfolioRiskSettings())

from mexc_monitor.screener import ScreenerEngine
_screener_engine = ScreenerEngine(
    history_db_path=(
        resolve_history_db_path(DEFAULT_SETTINGS)
        if DEFAULT_SETTINGS.history_enabled
        else None
    )
)


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
    ensure_futures_depth_book_ws_started(DEFAULT_SETTINGS)
    ensure_spot_orderbook_ws_started(DEFAULT_SETTINGS)
    ensure_spot_deals_ws_started(DEFAULT_SETTINGS)
    # Seed the proxy registry default from static config, then wire the
    # per-exchange resolver into the shared REST client pool so smart routing
    # applies to every exchange client via shared_get(exchange=...).
    try:
        REGISTRY.replace_all(
            default=DEFAULT_SETTINGS.http_proxy_url or None,
            per_exchange=dict(DEFAULT_SETTINGS.http_proxy_per_exchange),
        )
    except ProxyValidationError as e:
        logger.warning("Invalid proxy in config, ignoring: %s", e)
    set_proxy_resolver(lambda ex: effective_http_proxy(DEFAULT_SETTINGS, ex))
    reset_clients()
    if DEFAULT_SETTINGS.history_enabled:
        init_db(resolve_history_db_path(DEFAULT_SETTINGS))
    start_history_worker()
    _portfolio_risk.start()
    _screener_engine.start()
    _metascalp_poller.start()
    _metascalp_ws_bridge.start()
    _metascalp_auto_trader.start()
    # SignalWorker для стратегии ProBoyScalp (density + participant детекторы).
    # AlertService ещё не создан на моменте импорта, подключаем здесь.
    _metascalp_signal_worker._alert_service = _alert_service
    _metascalp_signal_worker.start()
    # Start basis calculator for WS feeds + REST fallback
    _futures_arb_basis_calc.start()

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
    from mexc_monitor.ws_bookticker import stop_all as stop_ws_booktickers

    stop_ws_booktickers()
    _portfolio_risk.stop()
    _screener_engine.stop()
    _registry.shutdown_all()
    stop_history_worker()
    stop_futures_depth_book_ws()
    stop_l2_depth_ws()
    stop_spot_orderbook_ws()
    stop_spot_deals_ws()
    _metascalp_poller.stop()
    _metascalp_ws_bridge.stop()
    _metascalp_auto_trader.stop()
    _metascalp_signal_worker.stop()

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


def _fetch_binance_depth(market: str, symbol: str, *, limit: int = 100) -> dict:
    """Fallback: получить стакан с Binance Spot API."""
    import httpx
    sym = symbol.strip().upper().replace("_USDT", "USDT").replace("/", "")
    url = "https://api.binance.com/api/v3/depth"
    params = {"symbol": sym, "limit": min(limit, 1000)}
    r = httpx.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    bids_raw = data.get("bids", [])
    asks_raw = data.get("asks", [])
    bids = [{"price": float(b[0]), "qty": float(b[1])} for b in bids_raw if len(b) >= 2]
    asks = [{"price": float(a[0]), "qty": float(a[1])} for a in asks_raw if len(a) >= 2]
    best_bid = bids[0]["price"] if bids else 0
    best_ask = asks[0]["price"] if asks else 0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0
    return {
        "market": market,
        "symbol": sym,
        "limit": limit,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "bids": bids,
        "asks": asks,
        "source": "binance_fallback",
    }


def _get_depth_snapshot(market: str, symbol: str, *, limit: int = 100) -> dict:
    """Единая точка получения L2-стакана для density-эндпоинтов.

    Порядок источников:
    1. WS-книга MEXC futures (``sub.depth.full``) — без сетевого запроса,
       всегда свежая, работает даже когда REST геоблокирован;
    2. REST MEXC (``fetch_orderbook_depth``);
    3. Binance-fallback.

    Возвращает dict со списками bids/asks (``{price, qty[, notional]}``) и
    полем ``source`` (``ws`` / ``rest`` / ``binance_fallback``).
    """
    from mexc_monitor.orderbook import fetch_orderbook_depth

    m = (market or "").strip().lower()
    # 1. WS-книга: только MEXC USDT-перпы, для которых крутится depth-фид.
    if m in ("futures", "perp"):
        book = get_fresh_futures_depth_book(symbol, max_age_sec=8.0)
        if book and book.get("bids") and book.get("asks"):
            return book
    # 2. REST MEXC → 3. Binance.
    try:
        return fetch_orderbook_depth(m or "spot", symbol, limit=limit)
    except Exception:
        return _fetch_binance_depth(m or "spot", symbol, limit=limit)


def _run_with_timeout(fn, *, timeout_sec: float):
    """Выполнить fn() в отдельном ����отоке с таймаутом."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        return future.result(timeout=timeout_sec)


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


@app.on_event("startup")
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


@app.on_event("shutdown")
def _shutdown_snapshot_prefetch() -> None:
    _snapshot_prefetch_stop.set()

_DEPTH_CACHE_TTL_SEC = max(0.0, float(os.environ.get("MEXC_DEPTH_CACHE_TTL_SEC", "1")))
_depth_cache_lock = threading.Lock()
_depth_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}


def _require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    if not _ADMIN_TOKEN:
        # Без явно заданного ADMIN_TOKEN торговые эндпоинты недоступны
        raise HTTPException(
            status_code=403,
            detail="trading API disabled: set ADMIN_TOKEN environment variable",
        )
    provided = (x_admin_token or "").strip()
    if not provided or not secrets.compare_digest(provided, _ADMIN_TOKEN):
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
        # Timeout для сборки снимка: 15 секунд
        try:
            payload = _run_with_timeout(builder, timeout_sec=15.0)
        except TimeoutError:
            logger.warning("snapshot build timeout for %s", cache_key)
            return {"ok": False, "error": "Snapshot build timeout", "rows": [], "count": 0}
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
    from mexc_monitor.ws_futures_depth_book import depth_book_health

    l2 = l2_depth_health()
    return {
        "status": "ok",
        "ws_feeds": feeds_health(),
        "orderbook_ws": {
            "mexc_futures_depth": depth_book_health(),
            "okx_l2_depth": l2.get("okx"),
            "bybit_l2_depth": l2.get("bybit"),
        },
    }


@app.get("/api/diagnostics/sources")
def diagnostics_sources(
    timeout_sec: float = Query(8.0, ge=1.0, le=30.0),
) -> dict[str, Any]:
    """Диагностика источников: REST-латентность + геоблок и свежесть WS-фида.

    По каждой бирже возвращает:
    - ``rest``: {status, status_code, elapsed_ms, url} — проба REST (через
      прокси, если настроен);
    - ``ws``: {running, live, symbols, last_message_age_sec} — состояние
      WebSocket-фида (если для биржи он есть);
    - ``recommended``: какой путь сейчас предпочтителен для снимка.
    """
    from mexc_monitor.source_probes import PROBES, probe_all
    from mexc_monitor.ws_bookticker import feeds_health

    rest = probe_all(DEFAULT_SETTINGS, timeout_sec=timeout_sec)
    ws = feeds_health()

    sources: list[dict[str, Any]] = []
    for name in PROBES:
        rest_res = rest.get(name, {})
        ws_res = ws.get(name)  # futures-фид под именем биржи
        ws_spot_res = ws.get(f"{name}_spot")  # spot-фид (okx/gateio/htx)
        ws_live = bool(ws_res and ws_res.get("live"))
        rest_ok = rest_res.get("status") == "ok"
        if ws_live:
            recommended = "ws"
        elif rest_ok:
            recommended = "rest"
        else:
            recommended = "none"
        sources.append(
            {
                "exchange": name,
                "rest": rest_res,
                "ws": ws_res,
                "ws_spot": ws_spot_res,
                "recommended": recommended,
                "proxy": effective_http_proxy(DEFAULT_SETTINGS, name),
            }
        )

    active_proxy = effective_http_proxy(DEFAULT_SETTINGS, "generic")
    reachable = sum(1 for s in sources if s["rest"].get("status") == "ok")
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_proxy": active_proxy,
        "summary": {
            "total": len(sources),
            "rest_reachable": reachable,
            "ws_live": sum(1 for s in sources if s["ws"] and s["ws"].get("live")),
            "ws_spot_live": sum(
                1 for s in sources if s["ws_spot"] and s["ws_spot"].get("live")
            ),
        },
        "sources": sources,
    }


_WITHDRAWAL_FEES_PATH = _ROOT / "config" / "withdrawal_fees.json"


@app.get("/api/withdrawal-fees")
def withdrawal_fees() -> dict:
    """Комиссии на вывод токенов по сетям из конфига."""
    if not _WITHDRAWAL_FEES_PATH.exists():
        return {"ok": False, "error": "withdrawal_fees.json not found", "tokens": {}}
    try:
        data = json.loads(_WITHDRAWAL_FEES_PATH.read_text(encoding="utf-8"))
        return {"ok": True, **data}
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": str(e), "tokens": {}}


@app.get("/api/coin-networks")
def coin_networks(
    coins: str = Query("", description="Список монет через запятую: BTC,ETH,SOL"),
    force: bool = Query(False, description="Игнорировать кэш и запросить биржи заново"),
) -> dict:
    """Сети депозита/вывода по монетам с бирж с публичным currency-API.

    Поддерживаются только Gate.io и Bitget (у остальных данные о с��тях
    доступны лишь через подписанные эндпоинты). Ответ:
    ``{coins: {BTC: {gateio: [{network, deposit, withdraw}], ...}}}``.
    """
    from mexc_monitor.coin_networks import (  # noqa: PLC0415
        SUPPORTED_EXCHANGES,
        get_coin_networks,
    )

    coin_list = [c for c in coins.split(",") if c.strip()]
    if not coin_list:
        return {"ok": True, "coins": {}, "supported_exchanges": list(SUPPORTED_EXCHANGES)}
    try:
        data = get_coin_networks(coin_list, force=force)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "coins": {}}
    return {
        "ok": True,
        "coins": data,
        "supported_exchanges": list(SUPPORTED_EXCHANGES),
    }


@app.get("/api/withdrawal-fees/calculate")
def calculate_withdrawal_cost(
    token: str = Query("USDT", description="Токен: USDT, BTC, ETH, SOL, XRP"),
    src_exchange: str = Query("mexc", description="Биржа отправления"),
    dst_exchange: str = Query("binance", description="Биржа назначения"),
    network: str = Query("", description="Сеть (TRC20, BEP20, ERC20, ...)"),
    spread_bps: float = Query(0, description="Текущий спред в bps"),
    notional_usdt: float = Query(1000, description="Размер сделки в USDT"),
) -> dict:
    """Расчёт чистой прибыли после withdrawal fees."""
    if not _WITHDRAWAL_FEES_PATH.exists():
        return {"ok": False, "error": "withdrawal_fees.json not found"}
    try:
        cfg = json.loads(_WITHDRAWAL_FEES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": str(e)}

    token = token.upper()
    src = src_exchange.lower()
    dst = dst_exchange.lower()
    net = network.upper() if network else ""

    # Базовые комиссии из конфига
    token_data = cfg.get("tokens", {}).get(token, {})
    networks = token_data.get("networks", {})
    overrides = cfg.get("exchange_overrides", {})

    def _fee(exchange: str, net_name: str) -> float | None:
        # Override для биржи
        ex_override = overrides.get(exchange, {}).get(token, {}).get(net_name)
        if ex_override is not None:
            return float(ex_override)
        # Базовая комиссия
        net_data = networks.get(net_name, {})
        return float(net_data.get("fee", 0)) if net_data else None

    # Если сеть не указана — найти лучшую
    if not net:
        results = []
        for net_name in networks:
            src_fee = _fee(src, net_name)
            dst_fee = _fee(dst, net_name)
            if src_fee is not None and dst_fee is not None:
                total_fee = src_fee + dst_fee
                net_profit_bps = spread_bps - (total_fee / notional_usdt * 10000) if notional_usdt > 0 else 0
                results.append({
                    "network": net_name,
                    "src_fee_usdt": src_fee,
                    "dst_fee_usdt": dst_fee,
                    "total_fee_usdt": total_fee,
                    "net_profit_bps": round(net_profit_bps, 2),
                    "net_profit_usdt": round(spread_bps / 10000 * notional_usdt - total_fee, 4),
                    "eta_min": networks[net_name].get("eta_min", 0),
                })
        results.sort(key=lambda x: x["total_fee_usdt"])
        return {
            "ok": True,
            "token": token,
            "src_exchange": src_exchange,
            "dst_exchange": dst_exchange,
            "spread_bps": spread_bps,
            "notional_usdt": notional_usdt,
            "networks": results,
            "best_network": results[0] if results else None,
        }

    # Конкретная сеть
    src_fee = _fee(src, net)
    dst_fee = _fee(dst, net)
    if src_fee is None or dst_fee is None:
        return {"ok": False, "error": f"Network {net} not found for {token}"}
    total_fee = src_fee + dst_fee
    net_profit_bps = spread_bps - (total_fee / notional_usdt * 10000) if notional_usdt > 0 else 0
    return {
        "ok": True,
        "token": token,
        "src_exchange": src_exchange,
        "dst_exchange": dst_exchange,
        "network": net,
        "src_fee_usdt": src_fee,
        "dst_fee_usdt": dst_fee,
        "total_fee_usdt": total_fee,
        "spread_bps": spread_bps,
        "notional_usdt": notional_usdt,
        "net_profit_bps": round(net_profit_bps, 2),
        "net_profit_usdt": round(spread_bps / 10000 * notional_usdt - total_fee, 4),
        "eta_min": networks.get(net, {}).get("eta_min", 0),
    }


@app.get("/api/slippage-estimate")
def slippage_estimate(
    symbol: str = Query("BTCUSDT", description="Символ (BTCUSDT, ETH_USDT, ...)"),
    market: str = Query("spot", description="spot или futures"),
    exchange: str = Query("mexc", description="Биржа"),
    notional_usdt: float = Query(1000, description="Размер ордера в USDT"),
    side: str = Query("buy", description="buy или sell"),
) -> dict:
    """On-demand оценка проскальзывания по L2 стакану."""
    from mexc_monitor.orderbook import fetch_orderbook_depth

    try:
        depth = fetch_orderbook_depth(market, symbol, limit=50)
    except Exception as e:
        # Fallback: Binance
        try:
            depth = _fetch_binance_depth(market, symbol, limit=50)
        except Exception:
            return {"ok": False, "error": f"MEXC: {e}. Binance fallback failed.", "symbol": symbol}

    levels = depth.get("asks" if side == "buy" else "bids", [])
    if not levels:
        return {"ok": False, "error": "No depth data", "symbol": symbol}

    # Найти best price и mid
    bids = depth.get("bids", [])
    asks = depth.get("asks", [])
    if not bids or not asks:
        return {"ok": False, "error": "Incomplete depth", "symbol": symbol}

    best_bid = float(bids[0][0]) if isinstance(bids[0], (list, tuple)) else float(bids[0].get("price", 0))
    best_ask = float(asks[0][0]) if isinstance(asks[0], (list, tuple)) else float(asks[0].get("price", 0))
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0

    # Конвертировать USDT в qty
    if side == "buy":
        # Для buy: notional / best_ask = qty
        qty = notional_usdt / best_ask if best_ask > 0 else 0
        levels_normalized = [(float(l[0]) if isinstance(l, (list, tuple)) else float(l.get("price", 0)),
                              float(l[1]) if isinstance(l, (list, tuple)) else float(l.get("qty", 0)))
                             for l in asks]
    else:
        qty = notional_usdt / best_bid if best_bid > 0 else 0
        levels_normalized = [(float(l[0]) if isinstance(l, (list, tuple)) else float(l.get("price", 0)),
                              float(l[1]) if isinstance(l, (list, tuple)) else float(l.get("qty", 0)))
                             for l in bids]

    # Walk the book
    remaining = qty
    total_cost = 0.0
    total_filled = 0.0
    levels_consumed = 0

    for price, level_qty in levels_normalized:
        if remaining <= 0 or price <= 0:
            break
        fill = min(level_qty, remaining)
        total_cost += price * fill
        total_filled += fill
        remaining -= fill
        levels_consumed += 1

    if total_filled == 0:
        return {"ok": False, "error": "No fill possible", "symbol": symbol}

    vwap_price = total_cost / total_filled
    if side == "buy":
        slippage_bps = (vwap_price - best_ask) / mid * 10000 if mid > 0 else 0
    else:
        slippage_bps = (best_bid - vwap_price) / mid * 10000 if mid > 0 else 0

    filled_notional = total_cost
    unfilled_notional = remaining * (best_ask if side == "buy" else best_bid)

    return {
        "ok": True,
        "symbol": symbol,
        "market": market,
        "exchange": exchange,
        "side": side,
        "notional_usdt": notional_usdt,
        "best_price": best_ask if side == "buy" else best_bid,
        "vwap_price": round(vwap_price, 8),
        "slippage_bps": round(slippage_bps, 2),
        "filled_notional_usdt": round(filled_notional, 2),
        "unfilled_notional_usdt": round(unfilled_notional, 2),
        "fully_filled": remaining <= 0,
        "levels_consumed": levels_consumed,
        "mid": round(mid, 8),
    }


@app.get("/api/admin-token")
def get_admin_token(request: Request) -> dict:
    """
    Отдать ADMIN_TOKEN фронтенду.
    Разрешено только для localhost, чтобы токен не утек вовне.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(status_code=403, detail="Forbidden: Localhost only")
    return {"ok": True, "token": _ADMIN_TOKEN}


@app.get("/api/open-interest")
def open_interest(
    symbol: str = Query("BTCUSDT", description="Символ (BTCUSDT, ETHUSDT, ...)"),
) -> dict:
    """Open Interest для фьючерсного контракта. MEXC с fallback на Binance."""
    import httpx
    sym = symbol.strip().upper().replace("/", "_").replace("-", "_")
    if "_" not in sym:
        sym_usdt = sym + "_USDT"
    else:
        sym_usdt = sym

    # Попробовать MEXC
    mexc_url = f"https://api.mexc.com/api/v1/contract/open_interest/{sym_usdt}"
    try:
        r = httpx.get(mexc_url, timeout=8)
        r.raise_for_status()
        data = r.json()
        return {
            "ok": True,
            "symbol": symbol.strip().upper(),
            "open_interest": data.get("openInterest", data.get("holdVol", 0)),
            "currency": data.get("currency", "USDT"),
            "source": "mexc",
        }
    except Exception:
        pass

    # Fallback: Binance Futures
    binance_sym = sym.replace("_USDT", "USDT").replace("_", "")
    binance_url = "https://fapi.binance.com/fapi/v1/openInterest"
    try:
        r = httpx.get(binance_url, params={"symbol": binance_sym}, timeout=8)
        r.raise_for_status()
        data = r.json()
        return {
            "ok": True,
            "symbol": symbol.strip().upper(),
            "open_interest": float(data.get("openInterest", 0)),
            "currency": "USDT",
            "source": "binance_fallback",
        }
    except Exception as e:
        return {"ok": False, "error": f"MEXC + Binance failed: {e}", "symbol": symbol.strip().upper()}


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
    Batch-загрузка klines для не��кольких символов одним запросом.
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
    # WS-книга MEXC futures — приоритетный источник (без сети, обходит геоблок).
    if m == "futures":
        ws_book = get_fresh_futures_depth_book(sym, max_age_sec=8.0)
        if ws_book and ws_book.get("bids") and ws_book.get("asks"):
            out = dict(ws_book)
            out["ok"] = True
            out["cache_hit"] = False
            if _DEPTH_CACHE_TTL_SEC > 0:
                with _depth_cache_lock:
                    _depth_cache[key] = (now + _DEPTH_CACHE_TTL_SEC, dict(out))
            return out
    try:
        data = fetch_orderbook_depth(m, sym, limit=lim)
    except (MexcApiError, Exception) as e:
        # Fallback: если MEXC вернул 403, попробовать Binance
        err_str = str(e)
        if "403" in err_str or "Forbidden" in err_str or isinstance(e, MexcApiError):
            try:
                data = _fetch_binance_depth(m, sym, limit=lim)
            except Exception as e2:
                return {
                    "ok": False,
                    "error": f"MEXC: {err_str}. Binance: {e2}",
                    "market": m,
                    "symbol": sym,
                    "limit": lim,
                    "bids": [],
                    "asks": [],
                    "cache_hit": False,
                }
        else:
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


@app.get("/api/density/walls")
def density_walls(
    symbol: str = Query("BTCUSDT", description="Символ"),
    market: str = Query("spot", description="spot или futures"),
    multiplier: float = Query(5.0, ge=1.5, le=50.0, description="Порог в разах от медианы"),
    min_notional: float = Query(10000, ge=0, description="Мин. нотация в USDT"),
) -> dict:
    """Поиск стен в стакане — уровни с аномально крупными ордерами."""
    from mexc_monitor.density import detect_walls, wall_to_dict
    try:
        data = _get_depth_snapshot(market, symbol, limit=100)
    except Exception as e:
        return {"ok": False, "error": str(e), "walls": []}

    walls = detect_walls(
        data.get("bids", []),
        data.get("asks", []),
        multiplier=multiplier,
        min_notional_usdt=min_notional,
    )
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "market": market,
        "multiplier": multiplier,
        "walls": [wall_to_dict(w) for w in walls],
        "count": len(walls),
        "source": data.get("source"),
    }


@app.get("/api/density/stats")
def density_stats(
    symbol: str = Query("BTCUSDT", description="Символ"),
    market: str = Query("spot", description="spot или futures"),
) -> dict:
    """Статистика плотности стакана."""
    from mexc_monitor.density import compute_density_stats, stats_to_dict
    try:
        data = _get_depth_snapshot(market, symbol, limit=100)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    stats = compute_density_stats(data.get("bids", []), data.get("asks", []))
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "market": market,
        "source": data.get("source"),
        **stats_to_dict(stats),
    }


@app.get("/api/density/compare")
def density_compare(
    symbol: str = Query("BTCUSDT", description="Символ"),
    exchanges: str = Query("mexc,binance,bybit", description="Через запятую"),
) -> dict:
    """Сравнение ликвидности стакана между биржами."""
    import concurrent.futures
    from mexc_monitor.density import compute_density_stats, stats_to_dict

    ex_list = [e.strip().lower() for e in exchanges.split(",") if e.strip()]
    if not ex_list:
        return {"ok": False, "error": "No exchanges", "results": {}}

    def _fetch_one(ex: str) -> tuple[str, dict]:
        try:
            if ex == "mexc":
                data = fetch_orderbook_depth("spot", symbol, limit=50)
            else:
                data = _fetch_binance_depth("spot", symbol, limit=50)
            stats = compute_density_stats(data.get("bids", []), data.get("asks", []))
            return ex, {"ok": True, **stats_to_dict(stats)}
        except Exception as e:
            return ex, {"ok": False, "error": str(e)}

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ex_list)) as pool:
        futures = {pool.submit(_fetch_one, ex): ex for ex in ex_list}
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                ex, payload = future.result(timeout=12)
                results[ex] = payload
            except Exception as e:
                ex = futures[future]
                results[ex] = {"ok": False, "error": str(e)}

    return {"ok": True, "symbol": symbol.strip().upper(), "results": results, "exchanges": ex_list}


@app.get("/api/density/history")
def density_history(
    symbol: str = Query("BTCUSDT", description="Символ"),
    since_ms: int | None = Query(None, description="Unix ms нижняя граница"),
    max_points: int = Query(500, ge=10, le=5000),
) -> dict:
    """История плотности стакана из in-memory буфера."""
    from mexc_monitor.density_buffer import get_history, snapshot_to_dict
    snapshots = get_history(symbol.strip().upper(), since_ms=since_ms, max_points=max_points)
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "count": len(snapshots),
        "snapshots": [snapshot_to_dict(s) for s in snapshots],
    }


@app.get("/api/density/changes")
def density_changes(
    symbol: str = Query("BTCUSDT", description="Символ"),
    since_ms: int | None = Query(None, description="Unix ms нижняя граница"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict:
    """История изменений стен (появление/исчезновение/рост/уменьшение)."""
    from mexc_monitor.density_buffer import get_wall_changes, wall_change_to_dict
    changes = get_wall_changes(symbol.strip().upper(), since_ms=since_ms, limit=limit)
    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "count": len(changes),
        "changes": [wall_change_to_dict(c) for c in changes],
    }


@app.get("/api/density/overview")
def density_overview(
    exchange: str = Query("mexc", description="Биржа"),
    market: str = Query("futures", description="spot или futures"),
    limit: int = Query(50, ge=5, le=200, description="Количество символов"),
    min_volume: float = Query(0, description="Мин. объём 24h (USDT)"),
) -> dict:
    """Обзор плотности по топ символам — таблица для Density Monitor."""
    import concurrent.futures
    from mexc_monitor.density import detect_walls, compute_density_stats, wall_to_dict, stats_to_dict

    # Получить список символов из snapshot
    if exchange == "mexc":
        raw = _get_snapshot_payload(
            f"mexc:{market}",
            bypass_cache=False,
            builder=lambda: _build_snapshot_payload(market),
        )
    else:
        raw = _get_snapshot_payload(
            f"{exchange}:{market}",
            bypass_cache=False,
            builder=lambda: _build_exchange_snapshot_payload(exchange, market),
            ttl=_snapshot_ttl_for(exchange),
        )

    if not raw.get("ok") or not raw.get("rows"):
        err = raw.get("error", "No data")
        # Bybit REST (список символов + объёмы) геоблокируется CloudFront 403 из
        # песочницы. WS-книга при этом жива — нужен лишь прокси для REST.
        if exchange == "bybit" and ("403" in str(err) or "CloudFront" in str(err)):
            err = (
                "Bybit REST геоблокирован (CloudFront 403): список символов и "
                "объёмы недоступны. WS-книга работает — настройте прокси "
                "(см. docs/PROXY_XRAY.md или страницу «Сеть / Прокси»)."
            )
        return {"ok": False, "error": err, "symbols": []}

    rows = raw["rows"]
    # Фильтр по объёму и сортировка
    if min_volume > 0:
        rows = [r for r in rows if (r.get("volume_24h_quote") or 0) >= min_volume]
    rows = rows[:limit]

    # Часто просматриваемые символы подмешиваем в подписку WS-книги, чтобы при
    # следующем reconcile density по ним считался из WS, а не Binance REST
    # (который геоблокируется). MEXC — свой depth-фид, OKX/Bybit — общий l2-фид.
    _row_symbols = [r.get("symbol", "") for r in rows]
    if exchange == "mexc" and market in ("futures", "perp"):
        try:
            touch_futures_depth_book_watchlist(_row_symbols)
            reconcile_futures_depth_book_ws(DEFAULT_SETTINGS)
        except Exception:  # noqa: BLE001 — best-effort, не критично для ответа
            pass
    elif exchange in ("okx", "bybit") and market in ("futures", "perp"):
        try:
            touch_l2_depth_watchlist(exchange, _row_symbols)
            reconcile_l2_depth(exchange)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _fetch_density(row: dict) -> dict:
        sym = row.get("symbol", "")
        try:
            # Сперва WS-книга (без сети, обходит геоблок): MEXC — свой фид,
            # OKX/Bybit — общий l2-фид. Иначе — Binance REST.
            depth = None
            if market in ("futures", "perp"):
                if exchange == "mexc":
                    depth = get_fresh_futures_depth_book(sym, max_age_sec=8.0)
                elif exchange in ("okx", "bybit"):
                    depth = get_fresh_l2_depth_book(exchange, sym, max_age_sec=8.0)
            if not depth:
                depth = _fetch_binance_depth(market, sym, limit=50)
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            stats = compute_density_stats(bids, asks)
            walls = detect_walls(bids, asks, multiplier=5, min_notional_usdt=50000)
            wall_dicts = [wall_to_dict(w) for w in walls]
            return {
                "symbol": sym,
                "mid": row.get("mid", 0),
                "spread_bps": row.get("spread_bps"),
                "volume_24h_quote": row.get("volume_24h_quote", 0),
                "density": stats_to_dict(stats),
                "source": depth.get("source"),
                "walls": wall_dicts[:5],  # top 5 стен
                "wall_count": len(wall_dicts),
                "largest_wall": wall_dicts[0] if wall_dicts else None,
            }
        except Exception:
            return {
                "symbol": sym,
                "mid": row.get("mid", 0),
                "spread_bps": row.get("spread_bps"),
                "volume_24h_quote": row.get("volume_24h_quote", 0),
                "density": None,
                "walls": [],
                "wall_count": 0,
                "largest_wall": None,
            }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_density, r): r for r in rows}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            try:
                results.append(future.result(timeout=25))
            except Exception:
                pass

    results.sort(key=lambda r: (r.get("largest_wall") or {}).get("notional_usdt", 0), reverse=True)

    return {
        "ok": True,
        "exchange": exchange,
        "market": market,
        "count": len(results),
        "symbols": results,
    }


@app.get("/api/density/heatmap")
def density_heatmap(
    symbol: str = Query("BTCUSDT", description="Символ"),
    market: str = Query("spot", description="spot или futures"),
    levels: int = Query(50, ge=10, le=200, description="Количество уровней с каждой стороны"),
) -> dict:
    """Данные стакана для тепловой карты плотности.

    Возвращает ценовые уровни с нотацией — фронтенд накапливает
    снимки во времени и рендерит heatmap (X=время, Y=цена, цвет=нотация).
    """
    try:
        data = _get_depth_snapshot(market, symbol, limit=levels)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    bids_raw = data.get("bids", [])
    asks_raw = data.get("asks", [])

    def _parse(levels_raw):
        out = []
        for lv in levels_raw:
            if isinstance(lv, (list, tuple)) and len(lv) >= 2:
                try:
                    p, q = float(lv[0]), float(lv[1])
                    if p > 0 and q >= 0:
                        out.append({"price": p, "qty": q, "notional": round(p * q, 2)})
                except (TypeError, ValueError):
                    continue
            elif isinstance(lv, dict):
                try:
                    p = float(lv.get("price", 0))
                    q = float(lv.get("qty", 0))
                    if p > 0 and q >= 0:
                        out.append({"price": p, "qty": q, "notional": round(p * q, 2)})
                except (TypeError, ValueError):
                    continue
        return out

    bids = _parse(bids_raw)
    asks = _parse(asks_raw)

    best_bid = bids[0]["price"] if bids else 0
    best_ask = asks[0]["price"] if asks else 0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0

    return {
        "ok": True,
        "symbol": symbol.strip().upper(),
        "market": market,
        "timestamp_ms": int(time.time() * 1000),
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bids": bids,
        "asks": asks,
        "source": data.get("source"),
    }


@app.get("/api/density/watcher/status")
def density_watcher_status() -> dict:
    """Статус DensityWatcher."""
    return {
        "ok": True,
        "running": _density_watcher._running,
        "symbols": _density_watcher._symbols,
        "poll_interval_sec": _density_watcher._poll_interval,
    }


@app.post("/api/density/watcher/start")
def density_watcher_start(
    symbols: str = Query("BTCUSDT,ETHUSDT", description="Символы через запятую"),
) -> dict:
    """Запустить DensityWatcher с указанными символами."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    _density_watcher.update_symbols(sym_list)
    _density_watcher.start()
    return {"ok": True, "symbols": sym_list, "message": "DensityWatcher started"}


@app.post("/api/density/watcher/stop")
def density_watcher_stop() -> dict:
    """Остановить DensityWatcher."""
    _density_watcher.stop()
    return {"ok": True, "message": "DensityWatcher stopped"}


# ─── AI Agent ─────────────────────────────────────────────────────────────────

_ai_config_path = _ROOT / "config" / "ai_config.json"


def _load_ai_config() -> dict:
    if _ai_config_path.exists():
        try:
            return json.loads(_ai_config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"provider": "openai", "model": "gpt-4o-mini", "autonomy": "confirm"}


@app.post("/api/ai/chat")
async def ai_chat(request: Request) -> dict:
    """AI Trading Agent chat endpoint."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    message = body.get("message", "").strip()
    if not message:
        return {"ok": False, "error": "Empty message"}

    autonomy = body.get("autonomy", "confirm")

    try:
        from mexc_monitor.ai import TradingAgent, AgentConfig, AutonomyLevel, OpenAIProvider, MARKET_TOOLS

        cfg = _load_ai_config()
        api_key = os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"), "")

        if not api_key:
            return {
                "ok": False,
                "error": f"API key not set. Set {cfg.get('api_key_env', 'OPENAI_API_KEY')} environment variable.",
            }

        provider = OpenAIProvider(
            api_key=api_key,
            model=cfg.get("model", "gpt-4o-mini"),
        )

        agent_config = AgentConfig(
            system_prompt=cfg.get("system_prompt", ""),
            autonomy=AutonomyLevel(autonomy),
            temperature=cfg.get("temperature", 0.3),
            max_tokens=cfg.get("max_tokens", 4096),
        )

        agent = TradingAgent(provider=provider, config=agent_config)

        # Register tools
        for tool_def in MARKET_TOOLS:
            agent.register_tool(
                name=tool_def["name"],
                description=tool_def["description"],
                parameters=tool_def["parameters"],
                handler=tool_def["handler"],
            )

        turn = await agent.chat(message)

        return {
            "ok": True,
            "response": turn.assistant_response,
            "tool_calls": turn.tool_calls,
            "tool_results": turn.tool_results,
        }

    except Exception as e:
        logger.exception("AI chat error")
        return {"ok": False, "error": str(e)}


@app.get("/api/ai/config")
def ai_config() -> dict:
    """Get AI agent configuration."""
    cfg = _load_ai_config()
    # Mask API key
    env_key = cfg.get("api_key_env", "OPENAI_API_KEY")
    has_key = bool(os.environ.get(env_key, "").strip())
    return {
        "ok": True,
        "provider": cfg.get("provider", "openai"),
        "model": cfg.get("model", "gpt-4o-mini"),
        "autonomy": cfg.get("autonomy", "confirm"),
        "has_api_key": has_key,
        "telegram_enabled": cfg.get("telegram_enabled", False),
    }


@app.get("/api/ai/telegram/status")
def telegram_status() -> dict:
    """Get Telegram bot status."""
    cfg = _load_ai_config()
    token_env = cfg.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_env = cfg.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID")
    has_token = bool(os.environ.get(token_env, "").strip())
    has_chat = bool(os.environ.get(chat_env, "").strip())
    enabled = cfg.get("telegram_enabled", False)
    return {
        "ok": True,
        "enabled": enabled,
        "has_token": has_token,
        "has_chat_id": has_chat,
        "token_env": token_env,
        "chat_env": chat_env,
        "running": enabled and has_token,
    }


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


@app.get("/api/snapshot/multi")
def snapshot_multi(
    exchanges: str = Query("mexc,binance,bybit,okx,gateio,bitget", description="Через запятую"),
    market: str = Query("futures", description="spot или futures"),
) -> dict:
    """Параллельная загрузка снимков с нескольких бирж."""
    import concurrent.futures
    ex_list = [e.strip().lower() for e in exchanges.split(",") if e.strip()]
    ex_list = [e for e in ex_list if e in _SUPPORTED_EXCHANGES]
    if not ex_list:
        return {"ok": False, "error": "No valid exchanges", "results": {}}

    def _fetch_one(ex: str) -> tuple[str, dict]:
        if ex == "mexc":
            m = market if market in ("spot", "futures") else "futures"
            return ex, _get_snapshot_payload(
                f"mexc:{m}",
                bypass_cache=False,
                builder=lambda: _build_snapshot_payload(m),
            )
        m = market if market in ("spot", "futures") else None
        return ex, _get_snapshot_payload(
            f"{ex}:{m or 'default'}",
            bypass_cache=False,
            builder=lambda: _build_exchange_snapshot_payload(ex, m),
            ttl=_snapshot_ttl_for(ex),
        )

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ex_list)) as pool:
        futures = {pool.submit(_fetch_one, ex): ex for ex in ex_list}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            try:
                ex, payload = future.result(timeout=25)
                results[ex] = payload
            except Exception as e:
                ex = futures[future]
                results[ex] = {"ok": False, "error": str(e), "rows": [], "count": 0}

    return {"ok": True, "results": results, "exchanges": ex_list}


@app.get("/api/snapshot/stream")
def snapshot_stream(
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

    def event_generator():
        last_sent = ""
        last_keepalive = time.monotonic()
        while True:
            _mark_snapshot_hot(cache_key, builder, ttl)
            try:
                out = _get_snapshot_payload(
                    cache_key, bypass_cache=False, builder=builder, ttl=ttl
                )
            except Exception as e:  # noqa: BLE001
                out = {"ok": False, "error": str(e)}
            marker = f"{out.get('loaded_at')}:{out.get('count')}:{out.get('ok')}"
            if marker != last_sent:
                last_sent = marker
                last_keepalive = time.monotonic()
                yield f"data: {json.dumps(out)}\n\n"
            elif time.monotonic() - last_keepalive > 15.0:
                last_keepalive = time.monotonic()
                yield ": keepalive\n\n"
            time.sleep(interval_sec)

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


_ACCOUNTS_PATH = _ROOT / "config" / "trading_accounts.json"


@app.get("/api/trading/accounts")
def trading_accounts() -> dict:
    """List configured trading accounts."""
    if not _ACCOUNTS_PATH.exists():
        return {"ok": True, "accounts": [], "message": "config/trading_accounts.json not found"}
    try:
        data = json.loads(_ACCOUNTS_PATH.read_text(encoding="utf-8"))
        accounts = data.get("accounts", [])
        # Check which accounts have API keys configured
        for acc in accounts:
            env_key = acc.get("api_key_env", "")
            env_secret = acc.get("api_secret_env", "")
            acc["has_credentials"] = bool(
                os.environ.get(env_key, "").strip() and os.environ.get(env_secret, "").strip()
            )
        return {"ok": True, "accounts": accounts}
    except Exception as e:
        return {"ok": False, "error": str(e), "accounts": []}


@app.get("/api/backtest")
def backtest(
    symbol: str = Query("BTCUSDT", description="Символ"),
    market: str = Query("futures", description="spot или futures"),
    entry_bps: float = Query(30.0, description="Порог входа (bps)"),
    exit_bps: float = Query(5.0, description="Порог выхода (bps)"),
    notional: float = Query(1000.0, description="Размер ордера (USDT)"),
    max_hold_sec: int = Query(300, description="Макс. удержание (сек)"),
) -> dict:
    """Бэктест стратегии захвата спреда на исторических данных."""
    from mexc_monitor.backtest import BacktestSettings, run_backtest
    from mexc_monitor.history_store import resolve_history_db_path

    db_path = resolve_history_db_path(DEFAULT_SETTINGS)
    if not db_path.is_file():
        return {"ok": False, "error": f"History DB not found: {db_path}"}

    settings = BacktestSettings(
        symbol=symbol.strip().upper(),
        market=market if market in ("spot", "futures") else "futures",
        entry_threshold_bps=entry_bps,
        exit_threshold_bps=exit_bps,
        order_notional_usdt=notional,
        max_hold_sec=max_hold_sec,
    )

    try:
        result = run_backtest(db_path, settings)
        return {"ok": True, **result.to_dict()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/system/capabilities")
def system_capabilities() -> dict:
    """Report whether each trading engine is ready to place REAL live orders.

    This endpoint exists so the UI can honestly tell the user, BEFORE they pick
    "live" mode, whether that mode will actually trade or silently fall back to
    a simulator. Today only TradingEngine places real orders; SpreadCapture and
    Arbitrage engines have full live-order code paths but never get an
    OrderExecutor injected in production, so "live" used to fake fills without
    warning. FuturesArb has no live path at all by design.

    Returns per-engine `live_ready: bool` plus a human-readable `reasons` list
    explaining what's missing. This is a read-only status endpoint — it does
    NOT require the admin token, because the user needs to see it before they
    even have a token configured (otherwise they can't tell what's wrong).
    """
    # SpreadCapture / Arbitrage: gated on the engine having an OrderExecutor
    # injected (set_order_executor). In production this is never called, so the
    # attribute is None and "live" silently simulates. We expose that honestly.
    capture_reasons: list[str] = []
    capture_ready = False
    capture_executor = getattr(_spread_capture_engine, "_order_executor", None)
    if capture_executor is None:
        capture_reasons.append("order_executor not injected (live mode will simulate fills)")
    else:
        capture_ready = True

    arb_reasons: list[str] = []
    arb_ready = False
    arb_executor = getattr(_arbitrage_engine, "_order_executor", None)
    if arb_executor is None:
        arb_reasons.append("order_executor not injected (live mode will auto-mark fills)")
    # Arbitrage also has an internal use_real_orders flag that defaults to False
    # even when an executor is present — surface it so users know it's a second gate.
    arb_settings = getattr(_arbitrage_engine, "_settings", None)
    if arb_settings is not None and not getattr(arb_settings, "use_real_orders", False):
        arb_reasons.append("use_real_orders=False (engine config)")
    if arb_executor is not None and not arb_reasons:
        arb_ready = True

    # FuturesArb: the strategy engine has no order-placement path at all.
    fa_reasons = ["engine has no live order executor (paper-only by design)"]
    fa_ready = False

    # TradingEngine (the one engine that actually trades live): check credentials
    # per configured exchange using the same env-prefix pattern as /trading/exchanges.
    trading_per_exchange: dict[str, list[str]] = {}
    trading_any_ready = False
    for ex in Exchange:
        config = EXCHANGE_CONFIGS[ex]
        has_creds = bool(
            os.environ.get(f"{config.env_prefix}_API_KEY")
            and os.environ.get(f"{config.env_prefix}_API_SECRET")
        )
        key = f"{ex.value}"
        if has_creds:
            trading_any_ready = True
        else:
            trading_per_exchange[key] = [
                f"{config.env_prefix}_API_KEY / _API_SECRET not set"
            ]

    return {
        "ok": True,
        "live_ready": {
            "capture": capture_ready,
            "arbitrage": arb_ready,
            "futures_arb": fa_ready,
            "trading": trading_any_ready,
        },
        "reasons": {
            "capture": capture_reasons,
            "arbitrage": arb_reasons,
            "futures_arb": fa_reasons,
            "trading": trading_per_exchange,
        },
    }


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


# ─── Trade stats (spot deals WS → trade_buffer) ──────────────────────────────


@app.get("/api/trades/symbols")
def trades_tracked_symbols() -> dict:
    """Символы с данными сделок в trade_buffer (плотность/имбаланс/VWAP)."""
    from mexc_monitor import trade_buffer as tb

    symbols = tb.get_tracked_symbols()
    return {"ok": True, "symbols": symbols, "count": len(symbols)}


@app.get("/api/trades/stats")
def trades_stats(
    symbol: str = Query(..., min_length=2, max_length=40),
    period_sec: float = Query(60.0, ge=1.0, le=600.0),
) -> dict:
    """Агрегаты сделок за период: count, buy/sell split, объёмы, VWAP, имбаланс."""
    from mexc_monitor import trade_buffer as tb

    st = tb.get_stats(symbol.strip(), period_sec=period_sec)
    if st is None:
        return {"ok": True, "symbol": symbol.strip().upper(), "stats": None}
    return {
        "ok": True,
        "symbol": st.symbol,
        "stats": {
            "period_sec": st.period_sec,
            "count": st.count,
            "buy_count": st.buy_count,
            "sell_count": st.sell_count,
            "volume_base": st.volume_base,
            "volume_quote": st.volume_quote,
            "buy_volume_quote": st.buy_volume_quote,
            "sell_volume_quote": st.sell_volume_quote,
            "vwap": st.vwap,
            "buy_sell_ratio": st.buy_sell_ratio,
            "trades_per_min": st.trades_per_min,
            "latest_ms": st.latest_ms,
        },
    }


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
    Клиент подключается и получает с��бытия при каждом изменении bid/ask.
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


# ─── Spread Screener endpoints ────────────────────────────────────────────────


@app.get("/api/screener/opportunities")
def screener_opportunities(
    limit: int = Query(50, ge=1, le=500, description="Max opportunities to return"),
) -> dict:
    """Current ranked tradeable-spread opportunities (MEXC spot)."""
    opps = _screener_engine.get_opportunities(limit=limit)
    status = _screener_engine.get_status()
    return {
        "ok": True,
        "opportunities": opps,
        "opportunity_count": len(opps),
        "scanned_at": status["scanned_at"],
        "total_universe": status["total_universe"],
        "config": _screener_engine.get_config(),
    }


@app.get("/api/screener/stream")
def screener_stream() -> StreamingResponse:
    """SSE: push the ranked opportunities on every scan."""
    import queue as _queue

    q = _screener_engine.subscribe()

    async def event_generator():
        try:
            # Initial snapshot so the client doesn't wait for the next scan.
            status = _screener_engine.get_status()
            yield (
                "data: "
                + json.dumps(
                    {
                        "scanned_at": status["scanned_at"],
                        "total_universe": status["total_universe"],
                        "opportunity_count": len(status["opportunities"]),
                        "opportunities": status["opportunities"],
                    }
                )
                + "\n\n"
            )

            while True:
                try:
                    payload = q.get(timeout=15.0)
                    yield "data: " + json.dumps(payload) + "\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            _screener_engine.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/screener/history")
def screener_history(
    limit: int = Query(100, ge=1, le=1000),
    symbol: str | None = Query(None),
    only_open: bool = Query(False),
) -> dict:
    """Persistent log of coins 'found' by the screener (enter events)."""
    events = _screener_engine.get_history(
        limit=limit, symbol=symbol, only_open=only_open
    )
    return {"ok": True, "count": len(events), "events": events}


@app.get("/api/screener/config")
def screener_config_get() -> dict:
    """Current screener thresholds and scorer weights."""
    return {"ok": True, "config": _screener_engine.get_config()}


# ─── Network / proxy (exchange egress) ───────────────────────────────────────


def _network_state() -> dict:
    snap = REGISTRY.snapshot()
    configured = DEFAULT_SETTINGS.http_proxy_url
    default_active = effective_http_proxy(DEFAULT_SETTINGS, "generic")
    # Per-exchange effective proxy for every known exchange (what would be used).
    per_exchange_effective = {
        ex: effective_http_proxy(DEFAULT_SETTINGS, ex) for ex in KNOWN_EXCHANGES
    }
    return {
        "default_proxy": snap["default"],
        "per_exchange": snap["per_exchange"],
        "per_exchange_effective": per_exchange_effective,
        "config_proxy": configured,
        "active_proxy": default_active,
        "known_exchanges": list(KNOWN_EXCHANGES),
        "note": (
            "Per-exchange routing: override a venue to a proxy or 'direct'. "
            "Empty inherits the default. MetaScalp (localhost) is always direct."
        ),
    }


@app.get("/api/network/config")
def network_config_get() -> dict:
    """Current exchange-proxy settings (default + per-exchange overrides)."""
    return {"ok": True, **_network_state()}


@app.patch("/api/network/config")
def network_config_update(payload: dict = Body(...)) -> dict:
    """Set/clear proxies at runtime — default and/or per-exchange overrides.

    Body (all fields optional):
    - ``default_proxy``: str — общий прокси ("" сбрасывает);
    - ``per_exchange``: {exchange: url|"direct"|""} — точечные переопределения;
    - ``http_proxy_url``: str — устаревший алиас для ``default_proxy``.

    Схемы: http/https/socks5/socks5h, либо "direct" для обхода прокси.
    """
    try:
        if "default_proxy" in payload or "http_proxy_url" in payload:
            raw = payload.get("default_proxy", payload.get("http_proxy_url", ""))
            REGISTRY.set_default(str(raw or "").strip() or None)
        per = payload.get("per_exchange")
        if isinstance(per, dict):
            for ex, val in per.items():
                if ex.strip().lower() not in KNOWN_EXCHANGES:
                    raise HTTPException(status_code=400, detail=f"Неизвестная биржа: {ex}")
                REGISTRY.set_exchange(ex, str(val or "").strip() or None)
    except ProxyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # New proxies → recreate pooled clients + keep legacy runtime override in sync.
    reset_clients()
    set_runtime_http_proxy(REGISTRY.snapshot()["default"])
    return {"ok": True, **_network_state()}


@app.get("/api/network/test")
def network_test(exchange: str = Query("mexc")) -> dict:
    """Probe one exchange's REST through its resolved proxy; report timing.

    Uses the same lightweight probe endpoints as /api/diagnostics/sources, so
    the result reflects exactly what smart routing does for that venue.
    """
    from mexc_monitor.source_probes import probe_one  # noqa: PLC0415

    ex = exchange.strip().lower()
    res = probe_one(ex, DEFAULT_SETTINGS)
    return {
        "ok": True,
        "exchange": ex,
        "reachable": res.get("status") == "ok",
        "status": res.get("status"),
        "status_code": res.get("status_code"),
        "elapsed_ms": res.get("elapsed_ms"),
        "error": res.get("error"),
        "proxy": effective_http_proxy(DEFAULT_SETTINGS, ex),
    }


@app.patch("/api/screener/config")
def screener_config_update(
    patch: dict = Body(...),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Hot-update screener thresholds/weights (admin only)."""
    try:
        new_cfg = _screener_engine.update_config(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "config": new_cfg}



# ─── Spread Capture Engine endpoints ────────────────────────────���──────────────


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


# ���── AsterDEX Private (Trading) endpoints ─────────────────────────────────────

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


@app.on_event("startup")
def _startup_telegram_bot() -> None:
    """Start Telegram bot if configured."""
    try:
        from mexc_monitor.telegram_bot.bot import load_telegram_bot
        from mexc_monitor.telegram_bot.alerts import AlertManager
        bot = load_telegram_bot()
        if bot:
            bot.start()
            # Start alert manager
            alerts = AlertManager(
                bot_send_fn=bot.send_alert,
                api_base_url="http://127.0.0.1:8006",
            )
            alerts.start()
            logger.info("Telegram bot started")
        else:
            logger.info("Telegram bot not configured (set TELEGRAM_BOT_TOKEN)")
    except Exception as e:
        logger.warning("Failed to start Telegram bot: %s", e)


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


# ─── Cross-Exchange Arbitrage Engine endpoints ─���───────────────────────────────

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
from mexc_monitor.futures_arb.balance_checker import MexcSpotBalanceChecker
# Spot balance checker for reverse cash-and-carry gating. Unconfigured (no MEXC
# spot creds) → no enforcement (paper mode); configured → enforces real balance.
_futures_arb_balance_checker = MexcSpotBalanceChecker()
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
    balance_checker=_futures_arb_balance_checker,
)

# Register all engines with PortfolioRiskManager
_portfolio_risk.register_engine(_CaptureAdapter())
_portfolio_risk.register_engine(_ArbitrageAdapter())
_portfolio_risk.register_engine(_FuturesArbAdapter())
_portfolio_risk.register_engine(_MetaScalpAdapter())
_portfolio_risk.register_engine(_TradingAdapter())

# DensityWatcher — фоновый мониторинг плотности стакана
from mexc_monitor.density_watcher import DensityWatcher
_density_watcher = DensityWatcher(
    symbols=["BTCUSDT", "ETHUSDT"],
    poll_interval_sec=10.0,
    min_notional_usdt=50_000,
    multiplier=5.0,
)


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
    """Mid-цены по всем биржам для указанного символа.

    Returns 200 with an empty `prices` dict when the symbol isn't currently
    monitored (engine stopped, symbol not in config, or no snapshot yet).
    Returning 404 here used to surface as a red console error on the /lead-lag
    page even though "no data yet" is a perfectly normal state — not an error.
    The frontend already treats `prices == {}` as "no data" and renders the
    empty state, so this keeps the UX honest without a scary network error.
    """
    engine = _get_lead_lag_engine()
    prices = engine.get_prices(symbol.strip().upper())
    if prices is None:
        prices = {}
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


# ─── MetaScalp Integration ────────────────────────────────────────────────────

@app.get("/api/metascalp/ping")
def metascalp_ping() -> dict:
    """Проверка доступности MetaScalp."""
    return _metascalp_client.ping()


@app.get("/api/metascalp/status")
def metascalp_status() -> dict:
    """Статус инфраструктуры MetaScalp (кэш, poller, bridge)."""
    return {
        "ok": True,
        "poller": _metascalp_poller.status(),
        "bridge_running": _metascalp_ws_bridge.is_running(),
    }


@app.get("/api/metascalp/connections")
def metascalp_connections() -> list[dict]:
    """Список активных подключений MetaScalp."""
    conns = _metascalp_client.connections()
    return [{"id": c.id, "name": c.name, "exchange": c.exchange, "status": c.status} for c in conns]


@app.get("/api/metascalp/connections/{conn_id}/balance")
def metascalp_balance(conn_id: str) -> dict:
    """Баланс подключения (с кэшем)."""
    cached = _metascalp_cache.get_balance(conn_id)
    if cached is not None:
        return {"ok": True, "connection_id": conn_id, "balances": cached, "cached": True}
    balances = _metascalp_client.balance(conn_id)
    data = [b.__dict__ for b in balances]
    _metascalp_cache.set_balance(conn_id, data)
    return {"ok": True, "connection_id": conn_id, "balances": data, "cached": False}


@app.get("/api/metascalp/connections/{conn_id}/orders")
def metascalp_orders(conn_id: str, ticker: str | None = None) -> dict:
    """Активные ордера (с кэшем)."""
    cached = _metascalp_cache.get_orders(conn_id)
    if cached is not None:
        orders = cached
        if ticker:
            orders = [o for o in orders if o.get("ticker") == ticker]
        return {"ok": True, "connection_id": conn_id, "orders": orders, "cached": True}
    orders = _metascalp_client.orders(conn_id, ticker)
    data = [o.__dict__ for o in orders]
    _metascalp_cache.set_orders(conn_id, data)
    return {"ok": True, "connection_id": conn_id, "orders": data, "cached": False}


@app.get("/api/metascalp/connections/{conn_id}/positions")
def metascalp_positions(conn_id: str) -> dict:
    """Открытые позиции (с кэшем)."""
    cached = _metascalp_cache.get_positions(conn_id)
    if cached is not None:
        return {"ok": True, "connection_id": conn_id, "positions": cached, "cached": True}
    positions = _metascalp_client.positions(conn_id)
    data = [p.__dict__ for p in positions]
    _metascalp_cache.set_positions(conn_id, data)
    return {"ok": True, "connection_id": conn_id, "positions": data, "cached": False}


@app.get("/api/metascalp/connections/{conn_id}/orderbook")
def metascalp_orderbook(conn_id: str, ticker: str) -> dict:
    """Снапшот стакана (короткий TTL кэш)."""
    cached = _metascalp_cache.get_orderbook(conn_id, ticker)
    if cached is not None:
        return {"ok": True, **cached, "cached": True}
    ob = _metascalp_client.orderbook_snapshot(conn_id, ticker)
    if ob is None:
        return {"ok": False, "error": "Failed to fetch orderbook"}
    data = {
        "ticker": ob.ticker,
        "best_ask": ob.best_ask,
        "best_bid": ob.best_bid,
        "asks": [a.__dict__ for a in ob.asks],
        "bids": [b.__dict__ for b in ob.bids],
    }
    _metascalp_cache.set_orderbook(conn_id, ticker, data)
    return {"ok": True, **data, "cached": False}


@app.get("/api/metascalp/connections/{conn_id}/cluster")
def metascalp_cluster(conn_id: str, ticker: str) -> dict:
    """Кластерный снапшот (volume profile)."""
    cached = _metascalp_cache.get_cluster(conn_id, ticker)
    if cached is not None:
        return {"ok": True, **cached, "cached": True}
    cluster = _metascalp_client.cluster_snapshot(conn_id, ticker)
    if cluster is None:
        return {"ok": False, "error": "Failed to fetch cluster snapshot"}
    data = {"ticker": cluster.ticker, "rows": cluster.rows}
    _metascalp_cache.set_cluster(conn_id, ticker, data)
    return {"ok": True, **data, "cached": False}


@app.get("/api/metascalp/connections/{conn_id}/signal-levels")
def metascalp_signal_levels(conn_id: str, ticker: str) -> dict:
    """Уровни сигналов (с кэшем)."""
    cached = _metascalp_cache.get_signal_levels(conn_id, ticker)
    if cached is not None:
        return {"ok": True, "connection_id": conn_id, "ticker": ticker, "levels": cached, "cached": True}
    levels = _metascalp_client.signal_levels(conn_id, ticker)
    data = [l.__dict__ for l in levels]
    _metascalp_cache.set_signal_levels(conn_id, ticker, data)
    return {"ok": True, "connection_id": conn_id, "ticker": ticker, "levels": data, "cached": False}


@app.post("/api/metascalp/connections/{conn_id}/orders")
def metascalp_place_order(
    conn_id: str,
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Размещение ордера через MetaScalp."""
    ticker = str(payload.get("ticker", ""))
    side = str(payload.get("side", ""))
    order_type = str(payload.get("type", ""))
    size = float(payload.get("size", 0))
    price = payload.get("price")
    if not ticker or not side or not order_type or size <= 0:
        return {"ok": False, "error": "ticker, side, type, size are required"}
    result = _metascalp_client.place_order(
        conn_id, ticker, side, order_type, size,
        float(price) if price is not None else None,
    )
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "orders")
    return result


@app.post("/api/metascalp/connections/{conn_id}/orders/cancel")
def metascalp_cancel_order(
    conn_id: str,
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Отмена ордера."""
    order_id = str(payload.get("order_id", ""))
    if not order_id:
        return {"ok": False, "error": "order_id is required"}
    result = _metascalp_client.cancel_order(conn_id, order_id)
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "orders")
    return result


@app.post("/api/metascalp/connections/{conn_id}/orders/cancel-all")
def metascalp_cancel_all(
    conn_id: str,
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Отмена всех ордеров."""
    ticker = payload.get("ticker")
    result = _metascalp_client.cancel_all_orders(conn_id, ticker)
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "orders")
    return result


@app.post("/api/metascalp/connections/{conn_id}/signal-levels")
def metascalp_place_signal_level(
    conn_id: str,
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Установка уровня сигнала."""
    ticker = str(payload.get("ticker", ""))
    price = float(payload.get("price", 0))
    rule = str(payload.get("rule", ""))
    if not ticker or price <= 0:
        return {"ok": False, "error": "ticker and price are required"}
    result = _metascalp_client.place_signal_level(conn_id, ticker, price, rule)
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "signal_levels")
    return result


@app.delete("/api/metascalp/connections/{conn_id}/signal-levels/{level_id}")
def metascalp_remove_signal_level(
    conn_id: str,
    level_id: str,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Удаление уровня сигнала по ID."""
    result = _metascalp_client.remove_signal_level(conn_id, level_id)
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "signal_levels")
    return result


@app.delete("/api/metascalp/connections/{conn_id}/signal-levels")
def metascalp_remove_all_signal_levels(
    conn_id: str,
    ticker: str,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Удаление всех уровней сигналов для тикера."""
    result = _metascalp_client.remove_all_signal_levels(conn_id, ticker)
    if result.get("ok"):
        _metascalp_cache.invalidate(conn_id, "signal_levels")
    return result


@app.delete("/api/metascalp/signal-levels/triggered")
def metascalp_remove_triggered(
    _: None = Depends(_require_admin_token),
) -> dict:
    """Удаление всех сработавших уровней сигналов."""
    result = _metascalp_client.remove_triggered_signal_levels()
    if result.get("ok"):
        _metascalp_cache.invalidate(None, "signal_levels")
    return result


@app.get("/api/metascalp/risk")
def metascalp_risk() -> dict:
    """Риск-метрики MetaScalp из PortfolioRiskManager."""
    adapter = _MetaScalpAdapter()
    return {
        "ok": True,
        "engine": adapter.engine_name,
        "open_notional": adapter.get_open_notional(),
        "open_symbols": adapter.get_open_symbols(),
        "positions_count": adapter.get_status().get("positions_count", 0),
        "orders_count": adapter.get_status().get("orders_count", 0),
    }


@app.get("/api/metascalp/auto-trade/config")
def metascalp_auto_trade_config() -> dict:
    """Конфигурация авто-торговли signal levels."""
    return {"ok": True, "config": _metascalp_auto_trader.get_config()}


@app.post("/api/metascalp/auto-trade/config")
def metascalp_auto_trade_config_set(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Обновить конфигурацию авто-торговли."""
    _metascalp_auto_trader.set_config(payload)
    return {"ok": True, "config": _metascalp_auto_trader.get_config()}


@app.post("/api/metascalp/auto-trade/trigger")
def metascalp_auto_trade_trigger(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Ручной триггер авто-торговли (для тестирования)."""
    conn_id = str(payload.get("conn_id", ""))
    ticker = str(payload.get("ticker", ""))
    price = float(payload.get("price", 0))
    rule = str(payload.get("rule", ""))
    if not conn_id or not ticker or price <= 0:
        return {"ok": False, "error": "conn_id, ticker, price are required"}
    result = _metascalp_auto_trader.on_signal_triggered(conn_id, ticker, price, rule)
    return result



@app.get("/api/basis")
def basis_all() -> dict:
    """Return all current basis snapshots from the basis calculator."""
    snapshots = _futures_arb_basis_calc.get_all_basis()
    return {
        "ok": True,
        "count": len(snapshots),
        "snapshots": [
            {
                "symbol": s.symbol,
                "combo": s.exchange_combo,
                "spot_mid": s.spot_mid,
                "futures_mid": s.futures_mid,
                "basis_bps": s.basis_bps,
                "executable_cc_bps": s.executable_basis_cc_bps,
                "executable_rcc_bps": s.executable_basis_rcc_bps,
                "estimated_apy": s.estimated_apy,
                "funding_rate": s.funding_rate or 0.0,
                "status": s.status,
                "timestamp_ms": s.timestamp_ms,
            }
            for s in snapshots
        ],
    }


# --- MetaScalp Basis Monitor (Spread Sniper integration) ---

@app.get("/api/metascalp/basis")
def metascalp_basis(
    ticker: str = Query("BTCUSDT", min_length=3, max_length=40),
    combo: str = Query("mexc_spot+mexc_futures"),
) -> dict:
    symbol = ticker.strip().upper()
    snapshot = _futures_arb_basis_calc.get_current_basis(symbol, combo)
    if snapshot is None:
        return {"ok": False, "error": f"No basis data for {symbol} {combo}"}

    from mexc_monitor.futures_arb.strategy_engine import _futures_exchange_from_combo
    futures_exchange = _futures_exchange_from_combo(combo)

    funding_info = None
    if futures_exchange:
        funding_info = _futures_arb_funding.get_funding(symbol, futures_exchange)

    return {
        "ok": True,
        "ticker": symbol,
        "combo": combo,
        "spot_mid": snapshot.spot_mid,
        "futures_mid": snapshot.futures_mid,
        "basis_bps": snapshot.basis_bps,
        "executable_cc_bps": snapshot.executable_basis_cc_bps,
        "executable_rcc_bps": snapshot.executable_basis_rcc_bps,
        "funding_rate": funding_info.current_rate if funding_info else (snapshot.funding_rate or 0.0),
        "estimated_apy": snapshot.estimated_apy,
        "status": snapshot.status,
        "timestamp_ms": snapshot.timestamp_ms,
    }


@app.post("/api/metascalp/spread")
def metascalp_open_spread(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    conn_id = str(payload.get("conn_id", ""))
    ticker = str(payload.get("ticker", "")).strip().upper()
    side = str(payload.get("side", "")).strip()
    notional = float(payload.get("notional", 0))
    leverage = int(payload.get("leverage", 3))
    combo = str(payload.get("combo", "mexc_spot+mexc_futures"))

    if not conn_id or not ticker or not side or notional <= 0:
        return {"ok": False, "error": "conn_id, ticker, side, notional are required"}
    if side not in ("Buy", "Sell"):
        return {"ok": False, "error": "side must be Buy or Sell"}

    snapshot = _futures_arb_basis_calc.get_current_basis(ticker, combo)
    if snapshot is None or snapshot.status == "stale" or snapshot.spot_mid <= 0:
        return {"ok": False, "error": f"No fresh basis data for {ticker}"}

    spot_price = snapshot.spot_mid
    futures_price = snapshot.futures_mid

    spot_side = side
    futures_side = "Sell" if side == "Buy" else "Buy"

    spot_size = round(notional / spot_price, 6) if spot_price > 0 else 0
    futures_size = round(notional / futures_price, 6) if futures_price > 0 else 0

    if spot_size <= 0 or futures_size <= 0:
        return {"ok": False, "error": "Invalid price data for sizing"}

    spot_result = _metascalp_client.place_order(
        conn_id, ticker, spot_side, "Market", spot_size, None
    )

    futures_ticker = ticker.replace("USDT", "_USDT") if "mexc_futures" in combo else ticker

    futures_result = _metascalp_client.place_order(
        conn_id, futures_ticker, futures_side, "Market", futures_size, None
    )

    strategy_name = "cash_and_carry" if side == "Buy" else "reverse_cash_and_carry"

    _metascalp_cache.invalidate(conn_id, "orders")
    _metascalp_cache.invalidate(conn_id, "positions")

    return {
        "ok": True,
        "strategy": strategy_name,
        "spot_order": spot_result,
        "futures_order": futures_result,
        "basis_bps": snapshot.basis_bps,
        "executable_cc_bps": snapshot.executable_basis_cc_bps,
        "executable_rcc_bps": snapshot.executable_basis_rcc_bps,
    }


# ─── SPA Fallback (must be AFTER all /api/ routes) ──────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_FRONTEND_DIST = _ROOT / "frontend" / "dist"
_FRONTEND_ASSETS = _FRONTEND_DIST / "assets"

# Mount static assets (JS, CSS, fonts, images from Vite build)
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_ASSETS)), name="static-assets")



@app.post("/api/metascalp/open-ticker")
def metascalp_open_ticker(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Create a signal level in MetaScalp to bring the ticker into focus.

    MetaScalp shows a popup when a signal level is created; clicking it opens the ticker.
    Also returns a metascalp:// URL the frontend can try as a custom protocol fallback.
    """
    conn_id = str(payload.get("conn_id", "")).strip()
    ticker = str(payload.get("ticker", "")).strip().upper()
    price = payload.get("price")

    if not ticker:
        return {"ok": False, "error": "ticker is required"}

    # Auto-resolve connection if not provided
    if not conn_id:
        conns = _metascalp_client.connections()
        if conns:
            conn_id = conns[0].id
        else:
            return {"ok": False, "error": "No MetaScalp connections available"}

    # Resolve price if not provided
    if price is None:
        from mexc_monitor.spread_buffer import get_latest
        tick = get_latest(ticker)
        if tick is None:
            # Try futures format
            fut = ticker.replace("USDT", "_USDT") if ticker.endswith("USDT") else None
            if fut:
                tick = get_latest(fut)
        price = tick.mid if tick else 0.0

    if price <= 0:
        return {"ok": False, "error": "Cannot resolve current price for ticker"}

    result = _metascalp_client.place_signal_level(
        conn_id=conn_id,
        ticker=ticker,
        price=float(price),
        rule="web_open",
    )

    return {
        "ok": result.get("ok", False),
        "connection_id": conn_id,
        "ticker": ticker,
        "price": price,
        "metascalp_url": f"metascalp://open-ticker/{ticker}?connection={conn_id}",
        "signal_level_result": result,
    }


# ─── MetaScalp Density / Participant signals (ProBoyScalp strategy) ────────


@app.get("/api/metascalp/density/scan")
def metascalp_density_scan(
    conn_id: str = Query("", description="Connection ID (пусто = auto-resolve)"),
    tickers: str = Query("", description="CSV тикеров (пусто = watchlist из конфига)"),
    only_candidates: bool = Query(True, description="Только кандидаты (стены + спред)"),
    min_notional: float = Query(0, ge=0, description="Override min_notional_usdt (0 = из конфига)"),
    multiplier: float = Query(0, ge=0, description="Override multiplier (0 = из конфига)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Сканировать стакан MetaScalp на плотности под стратегию ProBoyScalp."""
    cid = conn_id.strip()
    if not cid:
        conns = _metascalp_client.connections()
        if not conns:
            return {"ok": False, "error": "No MetaScalp connections available"}
        cid = conns[0].id

    if tickers.strip():
        tk_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        tk_list = [str(t).strip().upper() for t in _metascalp_signal_worker.get_config().get("watchlist", []) if str(t).strip()]

    if not tk_list:
        return {"ok": False, "error": "No tickers provided and watchlist is empty"}

    scanner = _metascalp_density_scanner
    if min_notional > 0 or multiplier > 0:
        # Создаём ad-hoc сканер с переопределёнными порогами
        from mexc_monitor.metascalp.density_scanner import MetaScalpDensityScanner
        cfg = _metascalp_signal_worker.get_config()
        scanner = MetaScalpDensityScanner(
            _metascalp_client,
            min_notional_usdt=min_notional if min_notional > 0 else float(cfg.get("min_notional_usdt", 1000)),
            multiplier=multiplier if multiplier > 0 else float(cfg.get("multiplier", 5)),
            min_spread_bps=float(cfg.get("min_spread_bps", 5)),
        )

    scans = scanner.scan_watchlist(cid, tk_list, only_candidates=only_candidates, push_history=True)
    return {
        "ok": True,
        "conn_id": cid,
        "scanned_count": len(tk_list),
        "returned_count": len(scans),
        "scans": [s.to_dict() for s in scans],
    }


@app.post("/api/metascalp/density/scan-now")
def metascalp_density_scan_now(_: None = Depends(_require_admin_token)) -> dict:
    """Принудительный проход SignalWorker сейчас (независимо от interval)."""
    signals = _metascalp_signal_worker.scan_now()
    return {
        "ok": True,
        "signals_count": len(signals),
        "signals": [s.to_dict() for s in signals],
    }


@app.get("/api/metascalp/signals")
def metascalp_signals(
    limit: int = Query(20, ge=1, le=200),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Последние комбинированные сигналы (плотности + участник) из ring buffer."""
    signals = _metascalp_signal_worker.get_top_signals(limit=limit)
    return {"ok": True, "count": len(signals), "signals": signals}


@app.get("/api/metascalp/density/last-scan")
def metascalp_density_last_scan(_: None = Depends(_require_admin_token)) -> dict:
    """Последний полный снимок скана watchlist (все тикеры, включая пропущенные)."""
    scans = _metascalp_signal_worker.get_last_scan()
    return {"ok": True, "count": len(scans), "scans": scans}


@app.get("/api/metascalp/density/history")
def metascalp_density_history(
    ticker: str = Query(..., description="Тикер"),
    since_ms: int | None = Query(None, description="С (epoch ms)"),
    max_points: int = Query(200, ge=10, le=2000),
    _: None = Depends(_require_admin_token),
) -> dict:
    """История snapshots плотности по тикеру из density_buffer."""
    from mexc_monitor.density_buffer import get_history, snapshot_to_dict

    ticker_norm = ticker.strip().upper()
    history = get_history(ticker_norm, since_ms=since_ms, max_points=max_points)
    return {
        "ok": True,
        "ticker": ticker_norm,
        "count": len(history),
        "history": [snapshot_to_dict(s) for s in history],
    }


@app.get("/api/metascalp/density/wall-changes")
def metascalp_density_wall_changes(
    ticker: str = Query(..., description="Тикер"),
    since_ms: int | None = Query(None, description="С (epoch ms)"),
    limit: int = Query(100, ge=1, le=1000),
    _: None = Depends(_require_admin_token),
) -> dict:
    """История изменений стен (appeared/disappeared/grew/shrunk) по тикеру."""
    from mexc_monitor.density_buffer import get_wall_changes, wall_change_to_dict

    ticker_norm = ticker.strip().upper()
    changes = get_wall_changes(ticker_norm, since_ms=since_ms, limit=limit)
    return {
        "ok": True,
        "ticker": ticker_norm,
        "count": len(changes),
        "changes": [wall_change_to_dict(c) for c in changes],
    }


@app.get("/api/metascalp/participants")
def metascalp_participants(
    tickers: str = Query("", description="CSV тикеров (пусто = все отслеживаемые)"),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Активные участники (всплески market-ордеров) сейчас."""
    tk_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers.strip() else None
    signals = _metascalp_participant_detector.detect_all(tk_list)
    return {
        "ok": True,
        "count": len(signals),
        "participants": [s.to_dict() for s in signals],
        "detector_stats": _metascalp_participant_detector.stats(),
    }


@app.get("/api/metascalp/participants/volume")
def metascalp_participants_volume(
    ticker: str = Query(..., description="Тикер"),
    _: None = Depends(_require_admin_token),
) -> dict:
    """Сводка объёмов buy/sell по тикеру без формирования сигнала (для UI)."""
    ticker_norm = ticker.strip().upper()
    return {
        "ok": True,
        "summary": _metascalp_participant_detector.get_volume_summary(ticker_norm),
    }


@app.post("/api/metascalp/trades/subscribe")
def metascalp_trades_subscribe(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Подписаться на WS trades тикеров (для ParticipantDetector).

    Body: {"conn_id": "...", "tickers": ["LYN_USDT", ...]}
    """
    conn_id = str(payload.get("conn_id", "")).strip()
    tickers = payload.get("tickers", [])
    if not isinstance(tickers, list):
        return {"ok": False, "error": "tickers must be a list"}
    if not conn_id:
        conns = _metascalp_client.connections()
        if not conns:
            return {"ok": False, "error": "No MetaScalp connections available"}
        conn_id = conns[0].id
    added: list[str] = []
    for tk in tickers:
        tk = str(tk).strip().upper()
        if tk:
            _metascalp_ws_bridge.subscribe_trades(conn_id, tk)
            added.append(tk)
    return {
        "ok": True,
        "conn_id": conn_id,
        "subscribed": added,
        "all_subscribed": [f"{c}:{t}" for c, t in _metascalp_ws_bridge.subscribed_trades()],
    }


@app.get("/api/metascalp/signals/config")
def metascalp_signals_config_get(_: None = Depends(_require_admin_token)) -> dict:
    """Текущий конфиг SignalWorker (watchlist, пороги, interval)."""
    return {"ok": True, "config": _metascalp_signal_worker.get_config(), "running": _metascalp_signal_worker.is_running()}


@app.post("/api/metascalp/signals/config")
def metascalp_signals_config_update(
    payload: dict,
    _: None = Depends(_require_admin_token),
) -> dict:
    """Обновить конфиг SignalWorker (поля: enabled, watchlist, interval_sec, пороги)."""
    cfg = _metascalp_signal_worker.update_config(payload)
    return {"ok": True, "config": cfg, "running": _metascalp_signal_worker.is_running()}


@app.post("/api/metascalp/signals/reload")
def metascalp_signals_reload(_: None = Depends(_require_admin_token)) -> dict:
    """Перезагрузить конфиг SignalWorker с диска (с рестартом если был включён)."""
    cfg = _metascalp_signal_worker.reload_config()
    return {"ok": True, "config": cfg, "running": _metascalp_signal_worker.is_running()}


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
