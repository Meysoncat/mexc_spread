from __future__ import annotations

import random
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from mexc_monitor.clock_skew_middleware import ClockSkewClient
from mexc_monitor.config import Settings
from mexc_monitor.proxy_registry import REGISTRY


def set_runtime_http_proxy(url: str | None) -> None:
    """Set/clear the runtime *default* proxy override for exchange traffic.

    Backwards-compatible shim: routes to the shared ProxyRegistry default.
    Per-exchange overrides are managed via the registry directly.
    """
    REGISTRY.set_default(url)


def effective_http_proxy(settings: Settings, exchange: str = "generic") -> str | None:
    """Resolve the proxy URL for ``exchange``: per-exchange > default > config.

    Resolution order:
    1. ProxyRegistry per-exchange override (incl. explicit direct → None);
    2. ProxyRegistry default proxy;
    3. Settings.http_proxy_url (static config);
    4. None → httpx trust_env (HTTP_PROXY/HTTPS_PROXY) or direct.
    """
    resolved = REGISTRY.resolve(exchange)
    if resolved is not None:
        return resolved
    # Registry has no opinion for this exchange → fall back to static config.
    cfg_proxy = (getattr(settings, "http_proxy_url", "") or "").strip()
    return cfg_proxy or None


@contextmanager
def mexc_httpx_client(settings: Settings, exchange: str = "generic") -> Iterator[httpx.Client]:
    """Контекстный httpx.Client с интеграцией clock skew detection.

    Parameters
    ----------
    settings
        Настройки для httpx-клиента.
    exchange
        Имя биржи для clock skew detection (mexc, binance, etc.).

    Yields
    ------
    httpx.Client
        Клиент с clock skew detection.
    """
    kwargs: dict[str, Any] = {"timeout": settings.timeout_sec}
    if settings.http_extra_headers:
        kwargs["headers"] = dict(settings.http_extra_headers)
    proxy = effective_http_proxy(settings, exchange)
    if proxy:
        kwargs["proxy"] = proxy

    with ClockSkewClient(**kwargs, exchange=exchange) as client:
        yield client


class RequestPacer:
    """Минимальный интервал между исходящими HTTP-запросами (один клиент / одна цепочка вызовов)."""

    def __init__(self, min_interval_sec: float):
        self._min = max(0.0, float(min_interval_sec))
        self._last = 0.0
        self._lock = threading.Lock()

    def wait_slot(self) -> None:
        if self._min <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self._min:
                time.sleep(self._min - elapsed)
            self._last = time.monotonic()


def _retryable_status(code: int) -> bool:
    return code == 429 or code == 502 or code == 503 or code == 504


def get_with_retry(
    client: httpx.Client,
    settings: Settings,
    url: str,
    *,
    pacer: RequestPacer | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """
    GET с pacing и повторными попытками при сетевых сбоях и 429/502/503/504.
    Backoff: http_retry_backoff_sec * 2^attempt + небольшой jitter, ограничен http_max_retry_wait_sec.
    """
    pacer = pacer or RequestPacer(0.0)
    max_attempts = max(1, settings.http_max_retries + 1)
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        pacer.wait_slot()
        try:
            r = client.get(url, params=params)
            if _retryable_status(r.status_code) and attempt < max_attempts - 1:
                base = max(0.05, settings.http_retry_backoff_sec)
                delay = min(
                    settings.http_max_retry_wait_sec,
                    base * (2**attempt) + random.uniform(0, base * 0.25),
                )
                time.sleep(delay)
                continue
            return r
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ) as e:
            last_error = e
            if attempt >= max_attempts - 1:
                raise
            base = max(0.05, settings.http_retry_backoff_sec)
            delay = min(
                settings.http_max_retry_wait_sec,
                base * (2**attempt) + random.uniform(0, base * 0.25),
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error
