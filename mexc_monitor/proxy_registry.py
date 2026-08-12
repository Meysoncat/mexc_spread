"""Единый реестр прокси с per-exchange smart routing.

Задача: проксировать только те биржи, что геоблокированы (Binance/Bybit/MEXC/
OKX), а остальные (Gate.io/Bitget/HTX) пускать напрямую — по тому же принципу,
что и Smart DNS, но на уровне HTTP/SOCKS-прокси.

Модель резолвинга по бирже (в порядке приоритета):
1. per-exchange override, если задан (включая явный DIRECT — «ходить напрямую»);
2. общий default-прокси, если задан;
3. None — httpx падает на trust_env (HTTP_PROXY/HTTPS_PROXY) или прямой доступ.

Чистый модуль: только stdlib, без внутренних импортов mexc_monitor — чтобы его
могли использовать и http_utils, и http_shared без циклических импортов.
Потокобезопасен: конфиг читается из нескольких потоков (WS-фиды, REST-пулы).
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse

# Разрешённые схемы прокси. socks5h резолвит DNS на стороне прокси (важно, если
# локальный DNS сам заблокирован); http/https — обычные CONNECT-прокси.
ALLOWED_SCHEMES = ("http", "https", "socks5", "socks5h")

# Сентинел: биржа ходит напрямую, игнорируя общий default-прокси.
DIRECT = "direct"

# Известные биржи (для валидации ключей и UI). Держим в одном месте.
KNOWN_EXCHANGES = (
    "mexc",
    "binance",
    "bybit",
    "okx",
    "gateio",
    "bitget",
    "htx",
    "asterdex",
    "hyperliquid",
    "dydx",
    "lighter",
)


class ProxyValidationError(ValueError):
    """Некорректный URL прокси (схема/хост)."""


def normalize_proxy(value: str | None) -> str | None:
    """Нормализовать и провалидировать URL прокси.

    Возвращает:
    - ``None`` — пусто (наследовать default / trust_env);
    - ``"direct"`` — явный обход прокси для этой биржи;
    - валидный URL прокси (строка).

    Бросает ``ProxyValidationError`` при недопустимой схеме или отсутствии хоста.
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v.lower() == DIRECT:
        return DIRECT
    parsed = urlparse(v)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ProxyValidationError(
            f"Недопустимая схема прокси '{parsed.scheme}'. "
            f"Разрешены: {', '.join(ALLOWED_SCHEMES)} или '{DIRECT}'."
        )
    if not parsed.hostname:
        raise ProxyValidationError("В URL прокси отсутствует хост.")
    return v


class ProxyRegistry:
    """Потокобезопасное хранилище общего и per-exchange прокси."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._default: str | None = None
        self._per_exchange: dict[str, str] = {}

    # --- запись ---------------------------------------------------------

    def set_default(self, value: str | None) -> str | None:
        """Установить/сбросить общий прокси. ``DIRECT`` для default не имеет
        смысла (эквивалент пустого) — трактуем как сброс."""
        norm = normalize_proxy(value)
        with self._lock:
            self._default = None if norm == DIRECT else norm
            return self._default

    def set_exchange(self, exchange: str, value: str | None) -> None:
        """Задать/сбросить прокси для конкретной биржи. Пусто = наследовать."""
        ex = exchange.strip().lower()
        norm = normalize_proxy(value)
        with self._lock:
            if norm is None:
                self._per_exchange.pop(ex, None)
            else:
                self._per_exchange[ex] = norm

    def replace_all(
        self, default: str | None, per_exchange: dict[str, str | None]
    ) -> None:
        """Атомарно заменить всю конфигурацию (валидирует до применения)."""
        norm_default = normalize_proxy(default)
        norm_map: dict[str, str] = {}
        for ex, val in per_exchange.items():
            n = normalize_proxy(val)
            if n is not None:
                norm_map[ex.strip().lower()] = n
        with self._lock:
            self._default = None if norm_default == DIRECT else norm_default
            self._per_exchange = norm_map

    # --- чтение ---------------------------------------------------------

    def resolve(self, exchange: str) -> str | None:
        """Вернуть прокси для биржи: override > default > None.

        ``DIRECT`` override возвращает ``None`` (прямой доступ), сознательно
        перекрывая общий default.
        """
        ex = (exchange or "").strip().lower()
        with self._lock:
            if ex in self._per_exchange:
                val = self._per_exchange[ex]
                return None if val == DIRECT else val
            return self._default

    def snapshot(self) -> dict[str, object]:
        """Текущее состояние для API/диагностики."""
        with self._lock:
            return {
                "default": self._default,
                "per_exchange": dict(self._per_exchange),
            }


# Единый глобальный реестр процесса.
REGISTRY = ProxyRegistry()
