from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


FuturesTickerSource = Literal["rest", "websocket"]


@dataclass(frozen=True)
class Settings:
    """Параметры внешних HTTP API (по умолчанию MEXC). Значения подгружаются из config/external_apis.json."""

    base_url: str = "https://api.mexc.com"
    book_ticker_path: str = "/api/v3/ticker/bookTicker"
    ticker_24hr_path: str = "/api/v3/ticker/24hr"
    spot_klines_path: str = "/api/v3/klines"
    spot_depth_path: str = "/api/v3/depth"
    # MEXC futures REST uses api.mexc.com (update-log 2026-01-19).
    futures_base_url: str = "https://api.mexc.com"
    contract_ticker_path: str = "/api/v1/contract/ticker"
    contract_depth_path_prefix: str = "/api/v1/contract/depth"
    futures_kline_path_prefix: str = "/api/v1/contract/kline"
    timeout_sec: float = 30.0

    http_max_retries: int = 3
    http_retry_backoff_sec: float = 0.5
    http_max_retry_wait_sec: float = 8.0
    http_min_request_interval_sec: float = 0.0
    # Доп. заголовки HTTP (перекрывают встроенные WAF-заголовки при совпадении имён).
    http_extra_headers: tuple[tuple[str, str], ...] = ()

    # Spot WebSocket (bookTicker L1 для выбранных символов).
    spot_ws_url: str = "wss://wbs.mexc.com/ws"
    spot_orderbook_ws_enabled: bool = False
    spot_orderbook_ws_symbols: tuple[str, ...] = ()
    spot_orderbook_ws_stale_after_sec: float = 3.0

    futures_ws_url: str = "wss://contract.mexc.com/edge"
    futures_ticker_source: FuturesTickerSource = "rest"
    futures_ws_stale_after_sec: float = 12.0
    # Сколько секунд ждать первый push.tickers (холодный старт WS или обход 403 REST).
    futures_ws_bootstrap_wait_sec: float = 55.0
    # WebSocket стакан (sub.depth / push.depth): L1 поверх REST или WS-тикеров.
    futures_orderbook_ws_enabled: bool = False
    futures_orderbook_ws_symbols: tuple[str, ...] = ()
    futures_orderbook_ws_stale_after_sec: float = 3.0
    # REST contract/ticker не отдаёт объёмы L1 — добираем из contract/depth (см. futures_l1_qty).
    futures_rest_l1_qty_enrich: bool = True
    futures_rest_l1_qty_max_symbols: int = 500
    futures_rest_l1_qty_max_workers: int = 24
    futures_rest_l1_qty_depth_limit: int = 5

    spot_symbols_whitelist: tuple[str, ...] = ()
    spot_symbols_blacklist: tuple[str, ...] = ()
    futures_symbols_whitelist: tuple[str, ...] = ()
    futures_symbols_blacklist: tuple[str, ...] = ()

    history_enabled: bool = False
    history_db_path: str = "data/spread_history.sqlite"
    history_interval_sec: float = 60.0
    history_markets: tuple[str, ...] = ("spot", "futures")

    exec_spot_taker_fee_bps: float = 0.0
    exec_futures_taker_fee_bps: float = 0.0
    exec_reference_quote_notional: float = 0.0

    @property
    def book_ticker_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.book_ticker_path}"

    @property
    def ticker_24hr_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.ticker_24hr_path}"

    @property
    def spot_klines_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.spot_klines_path}"

    @property
    def spot_depth_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.spot_depth_path}"

    @property
    def spot_ws_url_resolved(self) -> str:
        return self.spot_ws_url

    @property
    def contract_ticker_url(self) -> str:
        return f"{self.futures_base_url.rstrip('/')}{self.contract_ticker_path}"

    def contract_depth_url(self, symbol_path_segment: str) -> str:
        """symbol_path_segment — URL-encoded символ (например BTC_USDT)."""
        base = self.futures_base_url.rstrip("/")
        prefix = self.contract_depth_path_prefix.strip("/")
        sym = symbol_path_segment.lstrip("/")
        return f"{base}/{prefix}/{sym}"

    def futures_kline_url(self, symbol_path_segment: str) -> str:
        """symbol_path_segment — уже закодированный для path сегмент (например BTC_USDT)."""
        base = self.futures_base_url.rstrip("/")
        prefix = self.futures_kline_path_prefix.strip("/")
        sym = symbol_path_segment.lstrip("/")
        return f"{base}/{prefix}/{sym}"


# ─── Network & DNS Configuration ────────────────────────────────────────────


@dataclass(frozen=True)
class CustomDNSServer:
    """Одна DNS сервера."""

    address: str  # IP address (например, "185.212.113.7")
    port: int = 53
    tls: bool = False

    def __str__(self) -> str:
        return f"{self.address}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "port": self.port,
            "tls": self.tls,
        }


@dataclass(frozen=True)
class DNSEndpointDOH:
    """DNS-over-HTTPS endpoint."""

    url: str  # например, "https://1.1.1.1/dns-query"
    timeout_sec: float = 5.0
    headers: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "timeout_sec": self.timeout_sec,
            "headers": self.headers,
        }


@dataclass(frozen=True)
class ExchangeDNSConfig:
    """DNS конфигурация для конкретной биржи."""

    use_custom_dns: bool = False
    dns_type: Literal["system", "custom", "doh"] = "system"
    custom_dns_servers: tuple[CustomDNSServer, ...] = ()
    custom_dns_fallback: tuple[CustomDNSServer, ...] = ()
    dns_over_https_enabled: bool = False
    doh_endpoint: DNSEndpointDOH | None = None
    custom_dns_fallback_enabled: bool = True
    dns_timeout_sec: float = 5.0
    dns_retry_count: int = 3
    monitoring_enabled: bool = True
    alert_on_failure: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExchangeDNSConfig":
        """Load from dict (parsed JSON)."""
        servers = [
            CustomDNSServer(
                address=str(s.get("address", "")),
                port=int(s.get("port", 53)),
                tls=bool(s.get("tls", False)),
            )
            for s in data.get("custom_dns_servers", [])
        ]

        fallback = [
            CustomDNSServer(
                address=str(s.get("address", "")),
                port=int(s.get("port", 53)),
                tls=bool(s.get("tls", False)),
            )
            for s in data.get("custom_dns_fallback", [])
        ]

        doh_data = data.get("doh_endpoint")
        doh = None
        if doh_data:
            doh = DNSEndpointDOH(
                url=str(doh_data.get("url", "")),
                timeout_sec=float(doh_data.get("timeout_sec", 5.0)),
                headers=tuple(s for s in doh_data.get("headers", [])),
            )

        return cls(
            use_custom_dns=bool(data.get("use_custom_dns", False)),
            dns_type=str(data.get("dns_type", "system")),
            custom_dns_servers=tuple(servers),
            custom_dns_fallback=tuple(fallback),
            dns_over_https_enabled=bool(data.get("dns_over_https_enabled", False)),
            doh_endpoint=doh,
            custom_dns_fallback_enabled=bool(data.get("custom_dns_fallback_enabled", True)),
            dns_timeout_sec=float(data.get("dns_timeout_sec", 5.0)),
            dns_retry_count=int(data.get("dns_retry_count", 3)),
            monitoring_enabled=bool(data.get("monitoring_enabled", True)),
            alert_on_failure=bool(data.get("alert_on_failure", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (for JSON serialization)."""
        return {
            "use_custom_dns": self.use_custom_dns,
            "dns_type": self.dns_type,
            "custom_dns_servers": [s.to_dict() for s in self.custom_dns_servers],
            "custom_dns_fallback": [s.to_dict() for s in self.custom_dns_fallback],
            "dns_over_https_enabled": self.dns_over_https_enabled,
            "doh_endpoint": self.doh_endpoint.to_dict() if self.doh_endpoint else None,
            "custom_dns_fallback_enabled": self.custom_dns_fallback_enabled,
            "dns_timeout_sec": self.dns_timeout_sec,
            "dns_retry_count": self.dns_retry_count,
            "monitoring_enabled": self.monitoring_enabled,
            "alert_on_failure": self.alert_on_failure,
        }


@dataclass(frozen=True)
class GlobalNetworkConfig:
    """Глобальные сетевые настройки."""

    use_custom_dns: bool = False
    dns_type: Literal["system", "custom", "doh"] = "system"
    dns_fallback_enabled: bool = True
    dns_timeout_sec: float = 5.0
    dns_retry_count: int = 3
    monitoring_enabled: bool = True
    dns_cache_enabled: bool = True
    dns_cache_ttl_sec: int = 60
    dns_log_enabled: bool = True
    dns_log_file: str = "logs/dns_queries.log"
    alert_on_failure: bool = True
    alert_email: str = ""
    alert_webhook: str = ""
    health_check_interval_sec: int = 60
    failure_threshold: int = 3
    auto_failover_to_alternative_dns: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GlobalNetworkConfig":
        """Load from dict (parsed JSON)."""
        return cls(
            use_custom_dns=bool(data.get("use_custom_dns", False)),
            dns_type=str(data.get("dns_type", "system")),
            dns_fallback_enabled=bool(data.get("dns_fallback_enabled", True)),
            dns_timeout_sec=float(data.get("dns_timeout_sec", 5.0)),
            dns_retry_count=int(data.get("dns_retry_count", 3)),
            monitoring_enabled=bool(data.get("monitoring_enabled", True)),
            dns_cache_enabled=bool(data.get("dns_cache_enabled", True)),
            dns_cache_ttl_sec=int(data.get("dns_cache_ttl_sec", 60)),
            dns_log_enabled=bool(data.get("dns_log_enabled", True)),
            dns_log_file=str(data.get("dns_log_file", "logs/dns_queries.log")),
            alert_on_failure=bool(data.get("alert_on_failure", True)),
            alert_email=str(data.get("alert_email", "")),
            alert_webhook=str(data.get("alert_webhook", "")),
            health_check_interval_sec=int(data.get("health_check_interval_sec", 60)),
            failure_threshold=int(data.get("failure_threshold", 3)),
            auto_failover_to_alternative_dns=bool(data.get("auto_failover_to_alternative_dns", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (for JSON serialization)."""
        return {
            "use_custom_dns": self.use_custom_dns,
            "dns_type": self.dns_type,
            "dns_fallback_enabled": self.dns_fallback_enabled,
            "dns_timeout_sec": self.dns_timeout_sec,
            "dns_retry_count": self.dns_retry_count,
            "monitoring_enabled": self.monitoring_enabled,
            "dns_cache_enabled": self.dns_cache_enabled,
            "dns_cache_ttl_sec": self.dns_cache_ttl_sec,
            "dns_log_enabled": self.dns_log_enabled,
            "dns_log_file": self.dns_log_file,
            "alert_on_failure": self.alert_on_failure,
            "alert_email": self.alert_email,
            "alert_webhook": self.alert_webhook,
            "health_check_interval_sec": self.health_check_interval_sec,
            "failure_threshold": self.failure_threshold,
            "auto_failover_to_alternative_dns": self.auto_failover_to_alternative_dns,
        }


@dataclass(frozen=True)
class NetworkSettings:
    """Полная конфигурация сети (DNS + глобальные настройки)."""

    global_config: GlobalNetworkConfig = field(default_factory=GlobalNetworkConfig)
    exchange_configs: dict[str, "ExchangeDNSConfig"] = field(default_factory=dict)

    def get_exchange_config(self, exchange: str | Any) -> "ExchangeDNSConfig":
        """Get DNS config for specific exchange."""
        if isinstance(exchange, str):
            exchange_str = exchange.lower()
        else:
            try:
                exchange_str = str(exchange).lower()
            except Exception:
                exchange_str = ""
        return self.exchange_configs.get(
            exchange_str,
            ExchangeDNSConfig(
                use_custom_dns=False,
                dns_type="system",
                monitoring_enabled=True,
            )
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NetworkSettings":
        """Load from dict (parsed JSON from network_dns_config.json)."""
        global_config = GlobalNetworkConfig.from_dict(data.get("global", {}))
        exchange_configs = {}

        for exchange_name, exchange_data in data.get("exchanges", {}).items():
            exchange_configs[exchange_name] = ExchangeDNSConfig.from_dict(exchange_data)

        return cls(global_config=global_config, exchange_configs=exchange_configs)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (for JSON serialization)."""
        return {
            "global": self.global_config.to_dict(),
            "exchanges": self.exchange_configs
        }


def default_network_dns_config_path() -> Path:
    """Get default path for network DNS config."""
    custom = os.environ.get("MEXC_MONITOR_NETWORK_DNS_CONFIG")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "config" / "network_dns_config.json"


def load_network_dns_config(path: Path | None = None) -> NetworkSettings:
    """Load network DNS configuration from JSON file."""
    if path is None:
        path = default_network_dns_config_path()

    if not path.exists():
        logger.warning(f"Network DNS config not found: {path}. Using default settings.")
        return NetworkSettings()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        settings = NetworkSettings.from_dict(data)
        logger.info(f"Loaded network DNS config from {path}")
        logger.info(f"Global DNS type: {settings.global_config.dns_type}")
        logger.info(f"Enabled exchanges with custom DNS: {sum(1 for c in settings.exchange_configs.values() if c.use_custom_dns)}")

        return settings
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to load network DNS config: {e}")
        logger.warning(f"Using default network settings")
        return NetworkSettings()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Lazy import для избежания циклического импорта
        from mexc_monitor.trading.exchanges import Exchange

        settings = NetworkSettings.from_dict(data)
        logger.info(f"Loaded network DNS config from {path}")
        logger.info(f"Global DNS type: {settings.global_config.dns_type}")
        logger.info(f"Enabled exchanges with custom DNS: {sum(1 for c in settings.exchange_configs.values() if c.use_custom_dns)}")

        return settings
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to load network DNS config: {e}")
        logger.warning(f"Using default network settings")
        return NetworkSettings()


def default_external_apis_config_path() -> Path:
    custom = os.environ.get("MEXC_MONITOR_EXTERNAL_APIS_CONFIG")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "config" / "external_apis.json"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_str_tuple(val: Any) -> tuple[str, ...]:
    if val is None:
        return ()
    if isinstance(val, list):
        return tuple(str(x).strip() for x in val if str(x).strip())
    return ()


def _parse_http_headers(val: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(val, dict):
        return ()
    out: list[tuple[str, str]] = []
    for k, v in val.items():
        ks = str(k).strip()
        if not ks:
            continue
        vs = "" if v is None else str(v)
        out.append((ks, vs))
    return tuple(out)


def _norm_spot_symbol(s: str) -> str:
    return s.strip().upper()


def _norm_futures_symbol(s: str) -> str:
    x = s.strip().upper()
    if "_" not in x:
        return x
    return "_".join(p for p in x.split("_") if p)


def _comma_tuple_from_env(name: str) -> tuple[str, ...] | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    parts = tuple(x.strip() for x in raw.split(",") if x.strip())
    return parts


def _merge_history_from_raw(blob: Any, s: Settings) -> Settings:
    if not isinstance(blob, dict):
        return s
    enabled = bool(blob.get("enabled", s.history_enabled))
    db_path = str(blob.get("db_path", s.history_db_path)).strip() or s.history_db_path
    try:
        interval = float(blob.get("interval_sec", s.history_interval_sec))
    except (TypeError, ValueError):
        interval = s.history_interval_sec
    interval = max(5.0, interval)
    mk_raw = blob.get("markets")
    if isinstance(mk_raw, list):
        mk = tuple(str(x).strip().lower() for x in mk_raw if str(x).strip())
        mk = tuple(m for m in mk if m in ("spot", "futures"))
        if not mk:
            mk = s.history_markets
    else:
        mk = s.history_markets
    return replace(
        s,
        history_enabled=enabled,
        history_db_path=db_path,
        history_interval_sec=interval,
        history_markets=mk,
    )


def _merge_execution_from_raw(blob: Any, s: Settings) -> Settings:
    if not isinstance(blob, dict):
        return s
    try:
        spot_fee = float(blob.get("spot_taker_fee_bps", s.exec_spot_taker_fee_bps))
    except (TypeError, ValueError):
        spot_fee = s.exec_spot_taker_fee_bps
    try:
        fut_fee = float(blob.get("futures_taker_fee_bps", s.exec_futures_taker_fee_bps))
    except (TypeError, ValueError):
        fut_fee = s.exec_futures_taker_fee_bps
    try:
        ref = float(blob.get("reference_quote_notional", s.exec_reference_quote_notional))
    except (TypeError, ValueError):
        ref = s.exec_reference_quote_notional
    return replace(
        s,
        exec_spot_taker_fee_bps=max(0.0, spot_fee),
        exec_futures_taker_fee_bps=max(0.0, fut_fee),
        exec_reference_quote_notional=max(0.0, ref),
    )


def _settings_from_json_dict(raw: dict[str, Any]) -> Settings | None:
    mexc = raw.get("mexc")
    if not isinstance(mexc, dict):
        return None
    spot = mexc.get("spot")
    fut = mexc.get("futures")
    if not isinstance(spot, dict) or not isinstance(fut, dict):
        return None
    d = Settings()
    try:
        timeout = float(mexc.get("http_timeout_sec", d.timeout_sec))
    except (TypeError, ValueError):
        timeout = d.timeout_sec
    try:
        http_max_retries = int(mexc.get("http_max_retries", d.http_max_retries))
    except (TypeError, ValueError):
        http_max_retries = d.http_max_retries
    try:
        http_retry_backoff_sec = float(
            mexc.get("http_retry_backoff_sec", d.http_retry_backoff_sec),
        )
    except (TypeError, ValueError):
        http_retry_backoff_sec = d.http_retry_backoff_sec
    try:
        http_max_retry_wait_sec = float(
            mexc.get("http_max_retry_wait_sec", d.http_max_retry_wait_sec),
        )
    except (TypeError, ValueError):
        http_max_retry_wait_sec = d.http_max_retry_wait_sec
    try:
        http_min_request_interval_sec = float(
            mexc.get("http_min_request_interval_sec", d.http_min_request_interval_sec),
        )
    except (TypeError, ValueError):
        http_min_request_interval_sec = d.http_min_request_interval_sec
    try:
        futures_ws_stale_after_sec = float(
            mexc.get("futures_ws_stale_after_sec", d.futures_ws_stale_after_sec),
        )
    except (TypeError, ValueError):
        futures_ws_stale_after_sec = d.futures_ws_stale_after_sec
    try:
        futures_ws_bootstrap_wait_sec = float(
            mexc.get("futures_ws_bootstrap_wait_sec", d.futures_ws_bootstrap_wait_sec),
        )
    except (TypeError, ValueError):
        futures_ws_bootstrap_wait_sec = d.futures_ws_bootstrap_wait_sec
    futures_ws_bootstrap_wait_sec = max(10.0, min(120.0, futures_ws_bootstrap_wait_sec))

    spot_orderbook_ws_enabled = bool(
        mexc.get("spot_orderbook_ws_enabled", d.spot_orderbook_ws_enabled),
    )
    so_syms = tuple(
        _norm_spot_symbol(x)
        for x in _parse_str_tuple(mexc.get("spot_orderbook_ws_symbols"))
    )
    try:
        spot_orderbook_ws_stale_after_sec = float(
            mexc.get(
                "spot_orderbook_ws_stale_after_sec",
                d.spot_orderbook_ws_stale_after_sec,
            ),
        )
    except (TypeError, ValueError):
        spot_orderbook_ws_stale_after_sec = d.spot_orderbook_ws_stale_after_sec

    futures_orderbook_ws_enabled = bool(
        mexc.get("futures_orderbook_ws_enabled", d.futures_orderbook_ws_enabled),
    )
    fo_syms = tuple(
        _norm_futures_symbol(x)
        for x in _parse_str_tuple(mexc.get("futures_orderbook_ws_symbols"))
    )
    try:
        futures_orderbook_ws_stale_after_sec = float(
            mexc.get(
                "futures_orderbook_ws_stale_after_sec",
                d.futures_orderbook_ws_stale_after_sec,
            ),
        )
    except (TypeError, ValueError):
        futures_orderbook_ws_stale_after_sec = d.futures_orderbook_ws_stale_after_sec

    futures_rest_l1_qty_enrich = bool(
        mexc.get("futures_rest_l1_qty_enrich", d.futures_rest_l1_qty_enrich),
    )
    try:
        futures_rest_l1_qty_max_symbols = int(
            mexc.get(
                "futures_rest_l1_qty_max_symbols",
                d.futures_rest_l1_qty_max_symbols,
            ),
        )
    except (TypeError, ValueError):
        futures_rest_l1_qty_max_symbols = d.futures_rest_l1_qty_max_symbols
    futures_rest_l1_qty_max_symbols = max(0, futures_rest_l1_qty_max_symbols)
    try:
        futures_rest_l1_qty_max_workers = int(
            mexc.get(
                "futures_rest_l1_qty_max_workers",
                d.futures_rest_l1_qty_max_workers,
            ),
        )
    except (TypeError, ValueError):
        futures_rest_l1_qty_max_workers = d.futures_rest_l1_qty_max_workers
    futures_rest_l1_qty_max_workers = max(1, min(128, futures_rest_l1_qty_max_workers))
    try:
        futures_rest_l1_qty_depth_limit = int(
            mexc.get(
                "futures_rest_l1_qty_depth_limit",
                d.futures_rest_l1_qty_depth_limit,
            ),
        )
    except (TypeError, ValueError):
        futures_rest_l1_qty_depth_limit = d.futures_rest_l1_qty_depth_limit
    futures_rest_l1_qty_depth_limit = max(5, futures_rest_l1_qty_depth_limit)

    fts_raw = str(mexc.get("futures_ticker_source", d.futures_ticker_source)).lower()
    if fts_raw in ("rest", "websocket"):
        futures_ticker_source: FuturesTickerSource = fts_raw  # type: ignore[assignment]
    else:
        futures_ticker_source = d.futures_ticker_source

    spot_wl = tuple(
        _norm_spot_symbol(x) for x in _parse_str_tuple(spot.get("symbols_whitelist"))
    )
    spot_bl = tuple(
        _norm_spot_symbol(x) for x in _parse_str_tuple(spot.get("symbols_blacklist"))
    )
    fut_wl = tuple(
        _norm_futures_symbol(x) for x in _parse_str_tuple(fut.get("symbols_whitelist"))
    )
    fut_bl = tuple(
        _norm_futures_symbol(x) for x in _parse_str_tuple(fut.get("symbols_blacklist"))
    )

    return Settings(
        base_url=str(spot.get("base_url", d.base_url)),
        book_ticker_path=str(spot.get("book_ticker_path", d.book_ticker_path)),
        ticker_24hr_path=str(spot.get("ticker_24hr_path", d.ticker_24hr_path)),
        spot_klines_path=str(spot.get("klines_path", d.spot_klines_path)),
        spot_depth_path=str(spot.get("depth_path", d.spot_depth_path)),
        futures_base_url=str(fut.get("base_url", d.futures_base_url)),
        contract_ticker_path=str(
            fut.get("contract_ticker_path", d.contract_ticker_path),
        ),
        contract_depth_path_prefix=str(
            fut.get("contract_depth_path_prefix", d.contract_depth_path_prefix),
        ),
        futures_kline_path_prefix=str(
            fut.get("contract_kline_path_prefix", d.futures_kline_path_prefix),
        ),
        timeout_sec=timeout,
        http_max_retries=max(0, http_max_retries),
        http_retry_backoff_sec=max(0.0, http_retry_backoff_sec),
        http_max_retry_wait_sec=max(0.05, http_max_retry_wait_sec),
        http_min_request_interval_sec=max(0.0, http_min_request_interval_sec),
        http_extra_headers=_parse_http_headers(mexc.get("http_headers")),
        futures_ws_url=str(mexc.get("futures_ws_url", d.futures_ws_url)),
        futures_ticker_source=futures_ticker_source,
        spot_ws_url=str(mexc.get("spot_ws_url", d.spot_ws_url)),
        spot_orderbook_ws_enabled=spot_orderbook_ws_enabled,
        spot_orderbook_ws_symbols=so_syms,
        spot_orderbook_ws_stale_after_sec=max(0.5, spot_orderbook_ws_stale_after_sec),
        futures_ws_stale_after_sec=max(2.0, futures_ws_stale_after_sec),
        futures_ws_bootstrap_wait_sec=futures_ws_bootstrap_wait_sec,
        futures_orderbook_ws_enabled=futures_orderbook_ws_enabled,
        futures_orderbook_ws_symbols=fo_syms,
        futures_orderbook_ws_stale_after_sec=max(
            0.5,
            futures_orderbook_ws_stale_after_sec,
        ),
        futures_rest_l1_qty_enrich=futures_rest_l1_qty_enrich,
        futures_rest_l1_qty_max_symbols=futures_rest_l1_qty_max_symbols,
        futures_rest_l1_qty_max_workers=futures_rest_l1_qty_max_workers,
        futures_rest_l1_qty_depth_limit=futures_rest_l1_qty_depth_limit,
        spot_symbols_whitelist=spot_wl,
        spot_symbols_blacklist=spot_bl,
        futures_symbols_whitelist=fut_wl,
        futures_symbols_blacklist=fut_bl,
    )


def _apply_env_overrides(s: Settings) -> Settings:
    """Переопределение из окружения (удобно для Docker/k8s без правки JSON)."""
    kw: dict[str, Any] = {}
    if os.environ.get("MEXC_HTTP_MAX_RETRIES") is not None:
        kw["http_max_retries"] = max(0, _int_env("MEXC_HTTP_MAX_RETRIES", s.http_max_retries))
    if os.environ.get("MEXC_HTTP_RETRY_BACKOFF_SEC") is not None:
        kw["http_retry_backoff_sec"] = max(
            0.0,
            _float_env("MEXC_HTTP_RETRY_BACKOFF_SEC", s.http_retry_backoff_sec),
        )
    if os.environ.get("MEXC_HTTP_MAX_RETRY_WAIT_SEC") is not None:
        kw["http_max_retry_wait_sec"] = max(
            0.05,
            _float_env("MEXC_HTTP_MAX_RETRY_WAIT_SEC", s.http_max_retry_wait_sec),
        )
    if os.environ.get("MEXC_HTTP_MIN_REQUEST_INTERVAL_SEC") is not None:
        kw["http_min_request_interval_sec"] = max(
            0.0,
            _float_env(
                "MEXC_HTTP_MIN_REQUEST_INTERVAL_SEC",
                s.http_min_request_interval_sec,
            ),
        )
    if os.environ.get("MEXC_HTTP_USER_AGENT"):
        ua = os.environ["MEXC_HTTP_USER_AGENT"].strip()
        if ua:
            extra = [x for x in s.http_extra_headers if x[0].lower() != "user-agent"]
            extra.append(("User-Agent", ua))
            kw["http_extra_headers"] = tuple(extra)
    if os.environ.get("MEXC_FUTURES_TICKER_SOURCE") is not None:
        v = os.environ["MEXC_FUTURES_TICKER_SOURCE"].strip().lower()
        if v in ("rest", "websocket"):
            kw["futures_ticker_source"] = v
    if os.environ.get("MEXC_FUTURES_WS_URL") is not None:
        url = os.environ["MEXC_FUTURES_WS_URL"].strip()
        if url:
            kw["futures_ws_url"] = url
    if os.environ.get("MEXC_FUTURES_WS_STALE_AFTER_SEC") is not None:
        kw["futures_ws_stale_after_sec"] = max(
            2.0,
            _float_env("MEXC_FUTURES_WS_STALE_AFTER_SEC", s.futures_ws_stale_after_sec),
        )
    if os.environ.get("MEXC_FUTURES_WS_BOOTSTRAP_WAIT_SEC") is not None:
        v = _float_env(
            "MEXC_FUTURES_WS_BOOTSTRAP_WAIT_SEC",
            s.futures_ws_bootstrap_wait_sec,
        )
        kw["futures_ws_bootstrap_wait_sec"] = max(10.0, min(120.0, v))
    if os.environ.get("MEXC_SPOT_ORDERBOOK_WS_ENABLED") is not None:
        kw["spot_orderbook_ws_enabled"] = _bool_env(
            "MEXC_SPOT_ORDERBOOK_WS_ENABLED",
            s.spot_orderbook_ws_enabled,
        )
    ct = _comma_tuple_from_env("MEXC_SPOT_ORDERBOOK_WS_SYMBOLS")
    if ct is not None:
        kw["spot_orderbook_ws_symbols"] = tuple(_norm_spot_symbol(x) for x in ct)
    if os.environ.get("MEXC_SPOT_ORDERBOOK_WS_STALE_AFTER_SEC") is not None:
        kw["spot_orderbook_ws_stale_after_sec"] = max(
            0.5,
            _float_env(
                "MEXC_SPOT_ORDERBOOK_WS_STALE_AFTER_SEC",
                s.spot_orderbook_ws_stale_after_sec,
            ),
        )
    if os.environ.get("MEXC_SPOT_WS_URL") is not None:
        url = os.environ["MEXC_SPOT_WS_URL"].strip()
        if url:
            kw["spot_ws_url"] = url
    if os.environ.get("MEXC_FUTURES_ORDERBOOK_WS_ENABLED") is not None:
        kw["futures_orderbook_ws_enabled"] = _bool_env(
            "MEXC_FUTURES_ORDERBOOK_WS_ENABLED",
            s.futures_orderbook_ws_enabled,
        )
    ct = _comma_tuple_from_env("MEXC_FUTURES_ORDERBOOK_WS_SYMBOLS")
    if ct is not None:
        kw["futures_orderbook_ws_symbols"] = tuple(
            _norm_futures_symbol(x) for x in ct
        )
    if os.environ.get("MEXC_FUTURES_ORDERBOOK_WS_STALE_AFTER_SEC") is not None:
        kw["futures_orderbook_ws_stale_after_sec"] = max(
            0.5,
            _float_env(
                "MEXC_FUTURES_ORDERBOOK_WS_STALE_AFTER_SEC",
                s.futures_orderbook_ws_stale_after_sec,
            ),
        )
    if os.environ.get("MEXC_FUTURES_REST_L1_QTY_ENRICH") is not None:
        kw["futures_rest_l1_qty_enrich"] = _bool_env(
            "MEXC_FUTURES_REST_L1_QTY_ENRICH",
            s.futures_rest_l1_qty_enrich,
        )
    if os.environ.get("MEXC_FUTURES_REST_L1_QTY_MAX_SYMBOLS") is not None:
        kw["futures_rest_l1_qty_max_symbols"] = max(
            0,
            _int_env(
                "MEXC_FUTURES_REST_L1_QTY_MAX_SYMBOLS",
                s.futures_rest_l1_qty_max_symbols,
            ),
        )
    if os.environ.get("MEXC_FUTURES_REST_L1_QTY_MAX_WORKERS") is not None:
        kw["futures_rest_l1_qty_max_workers"] = max(
            1,
            min(
                128,
                _int_env(
                    "MEXC_FUTURES_REST_L1_QTY_MAX_WORKERS",
                    s.futures_rest_l1_qty_max_workers,
                ),
            ),
        )
    if os.environ.get("MEXC_FUTURES_REST_L1_QTY_DEPTH_LIMIT") is not None:
        kw["futures_rest_l1_qty_depth_limit"] = max(
            5,
            _int_env(
                "MEXC_FUTURES_REST_L1_QTY_DEPTH_LIMIT",
                s.futures_rest_l1_qty_depth_limit,
            ),
        )
    if os.environ.get("MEXC_HISTORY_ENABLED") is not None:
        kw["history_enabled"] = _bool_env("MEXC_HISTORY_ENABLED", s.history_enabled)
    if os.environ.get("MEXC_HISTORY_DB_PATH"):
        p = os.environ["MEXC_HISTORY_DB_PATH"].strip()
        if p:
            kw["history_db_path"] = p
    if os.environ.get("MEXC_HISTORY_INTERVAL_SEC") is not None:
        kw["history_interval_sec"] = max(
            5.0,
            _float_env("MEXC_HISTORY_INTERVAL_SEC", s.history_interval_sec),
        )
    ct = _comma_tuple_from_env("MEXC_SPOT_SYMBOLS_WHITELIST")
    if ct is not None:
        kw["spot_symbols_whitelist"] = tuple(_norm_spot_symbol(x) for x in ct)
    ct = _comma_tuple_from_env("MEXC_SPOT_SYMBOLS_BLACKLIST")
    if ct is not None:
        kw["spot_symbols_blacklist"] = tuple(_norm_spot_symbol(x) for x in ct)
    ct = _comma_tuple_from_env("MEXC_FUTURES_SYMBOLS_WHITELIST")
    if ct is not None:
        kw["futures_symbols_whitelist"] = tuple(_norm_futures_symbol(x) for x in ct)
    ct = _comma_tuple_from_env("MEXC_FUTURES_SYMBOLS_BLACKLIST")
    if ct is not None:
        kw["futures_symbols_blacklist"] = tuple(_norm_futures_symbol(x) for x in ct)
    if os.environ.get("MEXC_SPOT_TAKER_FEE_BPS") is not None:
        kw["exec_spot_taker_fee_bps"] = max(
            0.0,
            _float_env("MEXC_SPOT_TAKER_FEE_BPS", s.exec_spot_taker_fee_bps),
        )
    if os.environ.get("MEXC_FUTURES_TAKER_FEE_BPS") is not None:
        kw["exec_futures_taker_fee_bps"] = max(
            0.0,
            _float_env("MEXC_FUTURES_TAKER_FEE_BPS", s.exec_futures_taker_fee_bps),
        )
    if os.environ.get("MEXC_EXEC_REFERENCE_QUOTE_NOTIONAL") is not None:
        kw["exec_reference_quote_notional"] = max(
            0.0,
            _float_env(
                "MEXC_EXEC_REFERENCE_QUOTE_NOTIONAL",
                s.exec_reference_quote_notional,
            ),
        )
    return replace(s, **kw) if kw else s


def load_settings_from_file(path: Path | None = None) -> Settings:
    """Читает JSON; при отсутствии файла или ошибке — встроенные значения по умолчанию."""
    p = path or default_external_apis_config_path()
    if not p.is_file():
        return _apply_env_overrides(Settings())
    try:
        text = p.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return _apply_env_overrides(Settings())
    if not isinstance(raw, dict):
        return _apply_env_overrides(Settings())
    merged = _settings_from_json_dict(raw)
    base = merged if merged is not None else Settings()
    base = _merge_history_from_raw(raw.get("history"), base)
    base = _merge_execution_from_raw(raw.get("execution"), base)
    return _apply_env_overrides(base)


# Import Exchange after all other definitions to avoid circular imports
try:
    from mexc_monitor.trading.exchanges import Exchange  # noqa: F401
except Exception as e:
    logger.warning(f"Could not import Exchange enum: {e}")
    Exchange = None  # type: ignore[misc,assignment]
    logger.warning("Will use string identifiers for exchanges instead")


# Lazy load DEFAULT_SETTINGS to avoid circular imports
_DEFAULT_SETTINGS: Settings | None = None

def get_default_settings() -> Settings:
    """Get or create default settings (lazy loading)."""
    global _DEFAULT_SETTINGS
    if _DEFAULT_SETTINGS is None:
        _DEFAULT_SETTINGS = load_settings_from_file()
    return _DEFAULT_SETTINGS


# Import Exchange after DEFAULT_SETTINGS to avoid circular imports
DEFAULT_SETTINGS = get_default_settings()

try:
    from mexc_monitor.trading.exchanges import Exchange  # noqa: F401
except Exception as e:
    logger.warning(f"Could not import Exchange enum: {e}")
    Exchange = None  # type: ignore[misc,assignment]
    logger.warning("Will use string identifiers for exchanges instead")
