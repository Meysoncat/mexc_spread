"""Общие httpx.Client с keep-alive пулом, сгруппированные по прокси.

Раньше был один общий клиент на весь исходящий трафик. С per-exchange smart
routing разным биржам нужны разные прокси, поэтому держим пул клиентов с
ключом по резолвнутому прокси-URL (или "" для прямого доступа). Клиенты
переиспользуются между биржами с одинаковым прокси — keep-alive сохраняется.

Отдельный модуль без внутренних импортов mexc_monitor во избежание циклов;
резолвинг прокси инжектируется снаружи (main.py) через set_proxy_resolver.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

import httpx

_lock = threading.Lock()
# Ключ: прокси-URL ("" = direct). Значение: httpx.Client с этим прокси.
_clients: dict[str, httpx.Client] = {}

# Функция резолвинга прокси по бирже. Инжектируется из main.py, чтобы не тянуть
# config/registry сюда (циклы). None → всё напрямую (обратная совместимость).
_proxy_resolver: Callable[[str], str | None] | None = None


def set_proxy_resolver(resolver: Callable[[str], str | None] | None) -> None:
    """Задать функцию (exchange) -> proxy_url|None для shared-клиентов."""
    global _proxy_resolver
    with _lock:
        _proxy_resolver = resolver


def _resolve(exchange: str) -> str:
    """Вернуть прокси-URL для биржи ("" = direct)."""
    if _proxy_resolver is None:
        return ""
    try:
        return _proxy_resolver(exchange) or ""
    except Exception:
        return ""


def reset_clients() -> None:
    """Закрыть и сбросить все кэшированные клиенты (после смены прокси)."""
    global _clients
    with _lock:
        for c in _clients.values():
            try:
                if not c.is_closed:
                    c.close()
            except Exception:
                pass
        _clients = {}


def set_shared_http_proxy(url: str | None) -> None:  # noqa: ARG001
    """Обратная совместимость: смена прокси инвалидирует пул клиентов.

    Сам URL больше не хранится здесь — источник истины ProxyRegistry через
    инжектированный resolver. Достаточно сбросить клиентов, чтобы следующий
    запрос пересоздал их с актуальным прокси.
    """
    reset_clients()


def _client_for(proxy_key: str) -> httpx.Client:
    """Получить/создать переиспользуемый клиент для данного прокси."""
    with _lock:
        client = _clients.get(proxy_key)
        if client is not None and not client.is_closed:
            return client
        client_kwargs: dict[str, Any] = {
            "limits": httpx.Limits(max_connections=64, max_keepalive_connections=32),
        }
        if proxy_key:
            client_kwargs["proxy"] = proxy_key
        client = httpx.Client(**client_kwargs)
        _clients[proxy_key] = client
        return client


def shared_get(
    url: str,
    *,
    exchange: str = "generic",
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """GET через общий клиент, соответствующий прокси биржи ``exchange``.

    Переиспользует TCP+TLS соединения per-proxy; разные прокси не мешают
    друг другу. Без injected resolver ходит напрямую (как раньше).
    """
    proxy_key = _resolve(exchange)
    client = _client_for(proxy_key)
    return client.get(url, params=params, timeout=timeout)
