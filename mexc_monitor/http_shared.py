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
            _shared_client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=64, max_keepalive_connections=32
                ),
            )
        client = _shared_client
    return client.get(url, params=params, timeout=timeout)
