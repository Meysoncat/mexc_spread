"""Общий httpx.Client с keep-alive пулом соединений.

Отдельный модуль без внутренних импортов mexc_monitor, чтобы клиенты бирж
могли использовать его без циклических импортов (http_utils тянет config).
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

_shared_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None
# Proxy для биржевого трафика через shared-клиент. Устанавливается снаружи
# (main.py из настроек) во избежание циклического импорта с config.
_shared_proxy: str | None = None


def set_shared_http_proxy(url: str | None) -> None:
    """Set the proxy for the shared exchange client; resets the cached client
    so the next request recreates it with the new proxy."""
    global _shared_proxy, _shared_client
    with _shared_client_lock:
        _shared_proxy = (url or "").strip() or None
        if _shared_client is not None and not _shared_client.is_closed:
            try:
                _shared_client.close()
            except Exception:
                pass
        _shared_client = None


def shared_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """GET через общий клиент: переиспользует TCP+TLS соединения к биржам.

    Разовые httpx.get() открывают новое соединение на каждый запрос и
    тратят время на handshake; общий пул убирает эти накладные расходы.
    """
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None or _shared_client.is_closed:
            client_kwargs: dict[str, Any] = {
                "limits": httpx.Limits(
                    max_connections=64, max_keepalive_connections=32
                ),
            }
            if _shared_proxy:
                client_kwargs["proxy"] = _shared_proxy
            _shared_client = httpx.Client(**client_kwargs)
        client = _shared_client
    return client.get(url, params=params, timeout=timeout)
