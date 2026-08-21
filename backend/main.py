from __future__ import annotations

import asyncio  # noqa: F401  (re-exported to backend.routers via `bm.`)
import concurrent.futures as _cf  # noqa: F401  (re-exported to routers)
import hashlib  # noqa: F401  (re-exported to backend.routers via `bm.`)
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from mexc_monitor.client import MexcApiError  # noqa: F401  (re-exported to backend.routers via bm)
from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.history_store import init_db, query_recent, resolve_history_db_path  # noqa: F401  (re-exported to backend.routers via bm)
from mexc_monitor.history_worker import start_history_worker, stop_history_worker
from mexc_monitor.klines import fetch_klines_for_market
from mexc_monitor.orderbook import fetch_orderbook_depth
from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.trading.engine import TradingEngine
from mexc_monitor.trading.engine_registry import EngineRegistry
from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS
from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.ws_futures import ensure_started_from_settings
from mexc_monitor.ws_futures_orderbook import ensure_futures_orderbook_ws_started
from mexc_monitor.ws_futures_depth_book import (
    ensure_futures_depth_book_ws_started,
    get_fresh_depth_book as get_fresh_futures_depth_book,
    reconcile_futures_depth_book_ws,  # noqa: F401  (re-exported to backend.routers via bm)
    stop_futures_depth_book_ws,
    touch_watchlist as touch_futures_depth_book_watchlist,  # noqa: F401  (re-exported to routers)
)
from mexc_monitor.ws_l2_depth import (
    get_fresh_depth_book as get_fresh_l2_depth_book,  # noqa: F401  (re-exported to routers)
    l2_depth_health,  # noqa: F401  (re-exported to routers)
    reconcile as reconcile_l2_depth,  # noqa: F401  (re-exported to routers)
    stop_all as stop_l2_depth_ws,
    touch_watchlist as touch_l2_depth_watchlist,  # noqa: F401  (re-exported to routers)
)
from mexc_monitor.ws_spot_orderbook import ensure_spot_orderbook_ws_started, stop_spot_orderbook_ws
from mexc_monitor.ws_spot_deals import ensure_spot_deals_ws_started, stop_spot_deals_ws
from mexc_monitor.http_utils import effective_http_proxy, set_runtime_http_proxy  # noqa: F401  (re-exported to backend.routers via bm)
from mexc_monitor.rest_trades_poller import RestTradesPoller
from mexc_monitor.http_shared import reset_clients, set_proxy_resolver
from mexc_monitor.proxy_registry import REGISTRY, KNOWN_EXCHANGES, ProxyValidationError

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent

# ─── Environment & Auth ───────────────────────────────────────────────────────

_ENV_FILE = _ROOT / ".env"
if not _ENV_FILE.exists():
    _generated_token = secrets.token_urlsafe(32)
    _ENV_FILE.write_text(f"ADMIN_TOKEN={_generated_token}\n", encoding="utf-8")
    print("[auth] Generated new ADMIN_TOKEN in .env")

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
# REST trades poller → trade_buffer (working trade-feed; spot deals WS is blocked).
_rest_trades_poller = RestTradesPoller(DEFAULT_SETTINGS)
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
    ),
    rest_trades_poller=_rest_trades_poller,
)

from mexc_monitor.cross_screener import CrossScreenerEngine
_cross_screener_engine = CrossScreenerEngine(
    history_db_path=(
        resolve_history_db_path(DEFAULT_SETTINGS)
        if DEFAULT_SETTINGS.history_enabled
        else None
    ),
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
    _cross_screener_engine.start()
    if DEFAULT_SETTINGS.rest_trades_poller_enabled:
        _rest_trades_poller.start()
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
    _cross_screener_engine.stop()
    _registry.shutdown_all()
    stop_history_worker()
    stop_futures_depth_book_ws()
    stop_l2_depth_ws()
    stop_spot_orderbook_ws()
    stop_spot_deals_ws()
    _rest_trades_poller.stop()
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


# Заголовки, которые ставят reverse-proxy (nginx/traefik/cloudflare). Если хоть
# один присутствует — запрос пришёл НЕ напрямую с этой машины, даже если
# request.client.host выглядит как 127.0.0.1 (nginx → 127.0.0.1:8006).
_FORWARDED_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})


def _local_bootstrap_allowed(request: Request) -> bool:
    """True, если токен можно отдать этому запросу.

    Три условия одновременно:
      1. Bootstrap не выключен через ``ADMIN_TOKEN_LOCAL_BOOTSTRAP=0`` (прод).
      2. Клиент — петля (uvicorn видит 127.0.0.1/::1).
      3. В запросе нет forwarded-заголовков, т.е. это не прокинутый снаружи
         запрос. Без этой проверки схема «nginx на том же хосте → 127.0.0.1»
         отдаёт админ-токен любому внешнему клиенту.
    """
    flag = str(os.environ.get("ADMIN_TOKEN_LOCAL_BOOTSTRAP", "1")).strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK_HOSTS:
        return False
    return not any(h in request.headers for h in _FORWARDED_HEADERS)


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








_WITHDRAWAL_FEES_PATH = _ROOT / "config" / "withdrawal_fees.json"




















# ─── Batch Klines with in-memory cache ─────────────────────────────────────────


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
        from mexc_monitor.lighter.client import LighterPublicClient
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


























# ─── AI Agent ─────────────────────────────────────────────────────────────────

_ai_config_path = _ROOT / "config" / "ai_config.json"


def _load_ai_config() -> dict:
    if _ai_config_path.exists():
        try:
            return json.loads(_ai_config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"provider": "openai", "model": "gpt-4o-mini", "autonomy": "confirm"}








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














_ACCOUNTS_PATH = _ROOT / "config" / "trading_accounts.json"


























# ─── Spread Buffer & Streaming endpoints ───────────────────────────────────────

from mexc_monitor.spread_buffer import (
    SpreadTick,
    get_history as sb_get_history,  # noqa: F401  (re-exported to routers)
    get_latest as sb_get_latest,  # noqa: F401  (re-exported to routers)
    get_stats as sb_get_stats,  # noqa: F401  (re-exported to routers)
    get_tracked_symbols as sb_get_tracked_symbols,  # noqa: F401  (re-exported to routers)
    subscribe as sb_subscribe,  # noqa: F401  (re-exported to routers)
    unsubscribe as sb_unsubscribe,  # noqa: F401  (re-exported to routers)
)


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




# ─── Trade stats (spot deals WS → trade_buffer) ──────────────────────────────
















# ─── Spread Screener endpoints ────────────────────────────────────────────────










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











# ─── Spread Capture Engine endpoints ────────────────────────────���──────────────






















# ─── AsterDEX Integration endpoints ───────────────────────────────────────────

from mexc_monitor.aster import AsterPublicClient, AsterApiError as AsterError  # noqa: F401  (AsterError re-exported to routers)

_aster_public = AsterPublicClient()


















# ���── AsterDEX Private (Trading) endpoints ─────────────────────────────────────

import os as _os
_ASTER_API_KEY = _os.environ.get("ASTER_API_KEY", "").strip()
_ASTER_API_SECRET = _os.environ.get("ASTER_API_SECRET", "").strip()














# ─── Telegram Alerts endpoints ─────────────────────────────────────────────────

from mexc_monitor.alerts import AlertService, load_alert_config

_alert_service = AlertService(load_alert_config())








# ─── AsterDEX WebSocket Management endpoints ──────────────────────────────────

from mexc_monitor.aster.ws_client import (
    ensure_aster_ws_started,
    get_aster_ws_client,  # noqa: F401  (re-exported to backend.routers via bm)
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








# ─── Cross-Exchange Arbitrage Engine endpoints ─���───────────────────────────────

from mexc_monitor.arbitrage.engine import ArbitrageEngine

_arbitrage_engine = ArbitrageEngine()


















# ─── Cross-Spread History endpoints ───────────────────────────────────────────

from mexc_monitor.cross_spread_store import (
    query_cross_spread_history,  # noqa: F401  (re-exported to backend.routers via bm)
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
















# ─── MetaScalp Integration ────────────────────────────────────────────────────












































# --- MetaScalp Basis Monitor (Spread Sniper integration) ---





# ─── Domain routers (extracted endpoint handlers) ───
from backend.routers import ALL_ROUTERS

for _router in ALL_ROUTERS:
    app.include_router(_router)


# ─── SPA Fallback (must be AFTER all /api/ routes) ──────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_FRONTEND_DIST = _ROOT / "frontend" / "dist"
_FRONTEND_ASSETS = _FRONTEND_DIST / "assets"

# Mount static assets (JS, CSS, fonts, images from Vite build)
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_ASSETS)), name="static-assets")





# ─── MetaScalp Density / Participant signals (ProBoyScalp strategy) ────────


























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
