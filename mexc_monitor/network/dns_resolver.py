"""DNS Resolver with DOH and failover support."""

from __future__ import annotations

import logging
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from mexc_monitor.config import CustomDNSServer

logger = logging.getLogger(__name__)

DNS_TYPE = Literal["system", "custom", "doh"]


@dataclass
class DNSResolution:
    """Результат разрешения домена."""

    ip_address: str
    dns_type: DNS_TYPE = "custom"
    method: str = "direct"
    response_time_ms: float = 0.0
    from_cache: bool = False

    def __str__(self) -> str:
        return f"{self.ip_address} ({self.dns_type} via {self.method}, {self.response_time_ms:.0f}ms)"


class DNSCache:
    """Cache для разрешенных доменов."""

    def __init__(self, ttl_sec: int = 60):
        self._cache: dict[str, list[DNSResolution]] = {}
        self._lock = threading.Lock()
        self._ttl_sec = ttl_sec

    def get(self, hostname: str) -> DNSResolution | None:
        """Get resolved IPs from cache."""
        with self._lock:
            if hostname in self._cache:
                entries = self._cache[hostname]
                # Check TTL
                if entries and entries[0].response_time_ms <= (time.monotonic() - self._last_update(hostname)) * 1000 <= self._ttl_sec * 1000:
                    logger.debug(f"DNS cache hit: {hostname}")
                    return entries[0]

        return None

    def set(self, hostname: str, resolution: DNSResolution) -> None:
        """Add resolution to cache."""
        with self._lock:
            if hostname not in self._cache:
                self._cache[hostname] = []

            # Add new resolution to the front
            self._cache[hostname].insert(0, resolution)

    def _last_update(self, hostname: str) -> float:
        """Get last update time for hostname."""
        return self._cache.get(hostname, [0])[0].response_time_ms / 1000.0

    def clear(self) -> None:
        """Clear all cache."""
        with self._lock:
            self._cache.clear()


class DNSMetrics:
    """Monitor DNS resolution metrics."""

    def __init__(self, exchange: str | None = None):
        self.exchange = exchange.lower() if exchange else "global"
        self._lock = threading.Lock()

        # Metrics
        self.total_queries = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.successful_resolutions = 0
        self.failed_resolutions = 0
        self.total_response_time_ms = 0.0
        self.max_response_time_ms = 0.0
        self.min_response_time_ms = float('inf')
        self.failures_per_method: dict[str, int] = {}

        # Health check metrics
        self.last_success_time: float | None = None
        self.last_failure_time: float | None = None
        self.consecutive_failures: int = 0
        self.total_consecutive_failures: int = 0
        self.health_score: float = 1.0

    def record_success(self, response_time_ms: float, method: str = "unknown") -> None:
        """Record successful resolution."""
        with self._lock:
            self.total_queries += 1
            self.successful_resolutions += 1
            self.total_response_time_ms += response_time_ms
            self.max_response_time_ms = max(self.max_response_ms, response_time_ms)
            self.min_response_time_ms = min(self.min_response_time_ms, response_time_ms)

            # Health score calculation
            self.health_score = min(1.0, self.health_score + 0.1)
            self.last_success_time = time.time()
            self.consecutive_failures = 0
            if self.total_consecutive_failures < self.consecutive_failures:
                self.total_consecutive_failures = 0

            # Method tracking
            self.failures_per_method[method] = self.failures_per_method.get(method, 0) + 1

    def record_failure(self, method: str = "unknown") -> None:
        """Record failed resolution."""
        with self._lock:
            self.total_queries += 1
            self.failed_resolutions += 1
            self.consecutive_failures += 1
            self.total_consecutive_failures = max(self.total_consecutive_failures, self.consecutive_failures)

            # Health score calculation
            self.health_score = max(0.0, self.health_score - 0.1)
            self.last_failure_time = time.time()

            # Method tracking
            self.failures_per_method[method] = self.failures_per_method.get(method, 0) + 1

    def record_cache_hit(self) -> None:
        """Record cache hit."""
        with self._lock:
            self.total_queries += 1
            self.cache_hits += 1

    def record_cache_miss(self) -> None:
        """Record cache miss."""
        with self._lock:
            self.total_queries += 1
            self.cache_misses += 1

    def get_stats(self) -> dict[str, Any]:
        """Get metrics as dictionary."""
        with self._lock:
            cache_hit_rate = (self.cache_hits / self.total_queries * 100) if self.total_queries > 0 else 0.0
            success_rate = (self.successful_resolutions / self.total_queries * 100) if self.total_queries > 0 else 0.0
            avg_response_time = (self.total_response_time_ms / self.successful_resolutions) if self.successful_resolutions > 0 else 0.0

            return {
                "exchange": self.exchange.name.lower() if self.exchange else "global",
                "total_queries": self.total_queries,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_hit_rate": cache_hit_rate,
                "successful_resolutions": self.successful_resolutions,
                "failed_resolutions": self.failed_resolutions,
                "success_rate": success_rate,
                "total_response_time_ms": self.total_response_time_ms,
                "avg_response_time_ms": avg_response_time,
                "max_response_time_ms": self.max_response_time_ms,
                "min_response_time_ms": self.min_response_time_ms,
                "consecutive_failures": self.consecutive_failures,
                "total_consecutive_failures": self.total_consecutive_failures,
                "health_score": round(self.health_score, 2),
                "last_success_time": self.last_success_time,
                "last_failure_time": self.last_failure_time,
                "failures_per_method": dict(self.failures_per_method),
            }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API."""
        return self.get_stats()


class CustomDNSResolver:
    """Resolver с поддержкой custom DNS, DOH и failover."""

    def __init__(
        self,
        config: "NetworkSettings",
        use_global_config: bool = True,
    ):
        self._config = config
        self._use_global_config = use_global_config
        self._cache = DNSCache(
            ttl_sec=config.global_config.dns_cache_ttl_sec
        )
        self._lock = threading.Lock()
        self._http_client = httpx.Client(
            timeout=10.0,
            follow_redirects=True,
        )
        self._metrics: dict[Exchange, DNSMetrics] = {}

    def get_metrics(self, exchange: Exchange | None = None) -> DNSMetrics:
        """Get or create metrics for exchange."""
        if exchange is None:
            # Global metrics
            metrics = DNSMetrics(exchange=None)
        else:
            if exchange not in self._metrics:
                self._metrics[exchange] = DNSMetrics(exchange=exchange)
            metrics = self._metrics[exchange]

        return metrics

    def get_resolved_url(self, url: str) -> str:
        """Get URL with resolved IP address."""
        with self._lock:
            parsed = urlparse(url)
            hostname = parsed.netloc.split(":")[0]

            # Try cache first
            cached = self._cache.get(hostname)
            if cached:
                self._cache.hit(hostname)
                logger.debug(f"Using cached DNS for {hostname}: {cached}")
                return f"https://{cached.ip_address}{parsed.path}"

            # Get DNS config for this URL
            if parsed.netloc.endswith("mexc.com"):
                dns_config = self._config.get_exchange_config("mexc")
            elif parsed.netloc.endswith("asterdex.com"):
                dns_config = self._config.get_exchange_config("asterdex")
            elif parsed.netloc.endswith("binance.com"):
                dns_config = self._config.get_exchange_config("binance")
            elif parsed.netloc.endswith("bybit.com"):
                dns_config = self._config.get_exchange_config("bybit")
            elif parsed.netloc.endswith("okx.com"):
                dns_config = self._config.get_exchange_config("okx")
            elif parsed.netloc.endswith("gateio.com"):
                dns_config = self._config.get_exchange_config("gateio")
            elif parsed.netloc.endswith("huobi.com") or parsed.netloc.endswith("hbdm.com"):
                dns_config = self._config.get_exchange_config("htx")
            elif parsed.netloc.endswith("bitget.com"):
                dns_config = self._config.get_exchange_config("bitget")
            elif parsed.netloc.endswith("dydx.trade"):
                dns_config = self._config.get_exchange_config("dydx")
            elif parsed.netloc.endswith("hyperliquid.xyz"):
                dns_config = self._config.get_exchange_config("hyperliquid")
            else:
                dns_config = self._config.global_config

            # Resolve using configured method
            resolution = self._resolve_hostname(
                hostname=hostname,
                config=dns_config,
                exchange=None,
            )

            if resolution is None:
                logger.warning(f"Failed to resolve {hostname}")
                return url  # Return original URL

            # Cache the result
            self._cache.set(hostname, resolution)

            if self._config.global_config.dns_log_enabled:
                self._log_dns_query(hostname, resolution)

            logger.info(f"Resolved {hostname} → {resolution}")
            return f"https://{resolution.ip_address}{parsed.path}"

    def _resolve_hostname(
        self,
        hostname: str,
        config: ExchangeDNSConfig | GlobalNetworkConfig,
        exchange: Exchange | None = None,
    ) -> DNSResolution | None:
        """Resolve hostname using configured DNS method."""

        # Determine DNS type
        if self._use_global_config:
            dns_type = config.dns_type
        else:
            dns_type = "custom"

        attempts = 0
        last_error: Exception | None = None

        def try_resolve() -> DNSResolution | None:
            nonlocal attempts, last_error

            timeout = config.dns_timeout_sec
            retry_count = config.dns_retry_count

            attempts = 0
            last_error = None

            while attempts <= retry_count:
                try:
                    attempts += 1
                    if attempts > 1:
                        logger.debug(f"DNS retry {attempts}/{retry_count} for {hostname}")
                        time.sleep(0.1 * (2 ** (attempts - 1)))

                    if config.use_custom_dns and dns_type == "custom":
                        return self._resolve_custom_dns(
                            hostname=hostname,
                            servers=config.custom_dns_servers,
                            config=config,
                        )

                    if dns_type == "doh" and config.dns_over_https_enabled:
                        return self._resolve_doh(
                            hostname=hostname,
                            doh_endpoint=config.doh_endpoint,
                            config=config,
                        )

                    if dns_type == "system":
                        return self._resolve_system_dns(
                            hostname=hostname,
                            config=config,
                        )

                    return None

                except Exception as e:
                    last_error = e
                    logger.debug(f"DNS resolution failed (attempt {attempts}): {e}")

                    if attempts >= retry_count:
                        continue

                    time.sleep(0.1)

            return None

        resolution = try_resolve()

        if resolution is None and config.dns_fallback_enabled:
            logger.warning(f"Primary DNS failed, trying fallback for {hostname}")

            if isinstance(config, ExchangeDNSConfig) and config.custom_dns_fallback:
                resolution = self._resolve_custom_dns(
                    hostname=hostname,
                    servers=config.custom_dns_fallback,
                    config=config,
                )

            if resolution is None:
                resolution = self._resolve_system_dns(
                    hostname=hostname,
                    config=config,
                )

        return resolution

    def _resolve_custom_dns(
        self,
        hostname: str,
        servers: tuple[CustomDNSServer, ...],
        config: ExchangeDNSConfig | GlobalNetworkConfig,
    ) -> DNSResolution:
        import socket

        start_time = time.time()

        for server in servers:
            try:
                logger.debug(f"Querying custom DNS server {server}")

                resolver = socket.getaddrinfo(
                    hostname,
                    server.port,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                )

                ip_address = resolver[0][4][0]
                response_time_ms = (time.time() - start_time) * 1000

                return DNSResolution(
                    ip_address=ip_address,
                    dns_type="custom",
                    method=f"DNS {server.address}",
                    response_time_ms=response_time_ms,
                )

            except Exception as e:
                logger.debug(f"Custom DNS {server.address} failed: {e}")
                continue

        raise Exception(f"Failed to resolve {hostname} with custom DNS")

    def _resolve_doh(
        self,
        hostname: str,
        doh_endpoint: DNSEndpointDOH | None,
        config: ExchangeDNSConfig | GlobalNetworkConfig,
    ) -> DNSResolution:
        if not doh_endpoint:
            raise Exception("DOH endpoint not configured")

        start_time = time.time()

        import base64

        dns_packet = b"\x00\x01\x00\x01\x00\x00\x00\x00"
        dns_packet += hostname.encode("ascii")
        dns_packet += b"\x00\x01\x00\x01"

        dns_query = base64.urlsafe_b64encode(dns_packet)

        headers = {
            "accept": "application/dns-json",
        }
        if doh_endpoint.headers:
            headers.update(dict(doh_endpoint.headers))

        try:
            response = self._http_client.post(
                doh_endpoint.url,
                content=dns_query,
                headers=headers,
                timeout=doh_endpoint.timeout_sec,
            )

            if response.status_code == 200:
                data = response.json()

                answers = data.get("Answer", [])
                if answers:
                    ip_address = answers[0].get("data")
                    response_time_ms = (time.time() - start_time) * 1000

                    return DNSResolution(
                        ip_address=ip_address,
                        dns_type="doh",
                        method=f"DOH {doh_endpoint.url}",
                        response_time_ms=response_time_ms,
                    )

            raise Exception(f"DOH request failed: {response.status_code}")

        except Exception as e:
            logger.error(f"DOH resolution failed: {e}")
            raise

    def _resolve_system_dns(
        self,
        hostname: str,
        config: ExchangeDNSConfig | GlobalNetworkConfig,
    ) -> DNSResolution:
        import socket

        start_time = time.time()

        try:
            resolver = socket.getaddrinfo(hostname, 80)
            ip_address = resolver[0][4][0]
            response_time_ms = (time.time() - start_time) * 1000

            return DNSResolution(
                ip_address=ip_address,
                dns_type="system",
                method="system",
                response_time_ms=response_time_ms,
            )

        except Exception as e:
            logger.error(f"System DNS failed: {e}")
            raise

    def _log_dns_query(self, hostname: str, resolution: DNSResolution) -> None:
        log_data = {
            "timestamp": time.time(),
            "hostname": hostname,
            "ip_address": resolution.ip_address,
            "dns_type": resolution.dns_type,
            "method": resolution.method,
            "response_time_ms": resolution.response_time_ms,
        }

        logger.debug(f"DNS Query: {log_data}")

        if self._config.global_config.dns_log_enabled:
            log_path = Path(self._config.global_config.dns_log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    import json
                    f.write(json.dumps(log_data) + "\n")
            except Exception as e:
                logger.warning(f"Failed to write DNS log: {e}")

    def clear_cache(self) -> None:
        """Clear DNS cache."""
        self._cache.clear()

    def get_all_metrics(self) -> dict[str, Any]:
        """Get metrics for all exchanges."""
        metrics = {}
        for exchange, metric in self._metrics.items():
            metrics[exchange.name.lower()] = metric.to_dict()
        return metrics

    def __enter__(self) -> "CustomDNSResolver":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._http_client.close()