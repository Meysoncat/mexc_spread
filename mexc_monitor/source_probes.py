"""Диагностика источников данных: REST-пробы бирж (латентность + геоблок).

Лёгкие «ping/time»-эндпоинты на тех же хостах, что и боевые запросы, поэтому
геоблок (451/403) проявляется идентично, но проба быстрая и не нагружает API.
Запросы идут через прокси-aware ``mexc_httpx_client`` — значит настроенный на
странице «Сеть / Прокси» шлюз влияет и на диагностику.

Данные о свежести WebSocket-фидов берутся отдельно из
``ws_bookticker.feeds_health()`` и объединяются в эндпоинте ``/api/diagnostics``.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from mexc_monitor.config import Settings
from mexc_monitor.http_utils import mexc_httpx_client

# Статусы REST-пробы (порядок = приоритет для агрегированной оценки).
STATUS_OK = "ok"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_GEO_BLOCKED = "geo_blocked"
STATUS_ERROR = "error"


class Probe:
    """Описание лёгкой REST-пробы одной биржи."""

    __slots__ = ("method", "url", "json_body")

    def __init__(
        self,
        method: str,
        url: str,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.json_body = json_body


# Реестр проб: лёгкие публичные эндпоинты (ping / server-time / status).
# Для Lighter и Hyperliquid используются реально вызываемые клиентом пути.
PROBES: dict[str, Probe] = {
    "mexc": Probe("GET", "https://api.mexc.com/api/v3/ping"),
    "binance": Probe("GET", "https://fapi.binance.com/fapi/v1/ping"),
    "bybit": Probe("GET", "https://api.bybit.com/v5/market/time"),
    "okx": Probe("GET", "https://www.okx.com/api/v5/public/time"),
    "gateio": Probe("GET", "https://api.gateio.ws/api/v4/spot/time"),
    "bitget": Probe("GET", "https://api.bitget.com/api/v2/public/time"),
    "htx": Probe("GET", "https://api.huobi.pro/v1/common/timestamp"),
    "asterdex": Probe("GET", "https://fapi.asterdex.com/fapi/v1/ping"),
    "dydx": Probe("GET", "https://indexer.dydx.trade/v4/height"),
    "lighter": Probe("GET", "https://mainnet.zklighter.elliot.ai/api/v1/orderBooks"),
    "hyperliquid": Probe("POST", "https://api.hyperliquid.xyz/info", {"type": "meta"}),
}


def classify(status_code: int | None) -> str:
    """Классификация HTTP-статуса пробы в статус доступности источника."""
    if status_code is None:
        return STATUS_ERROR
    if status_code == 429:
        return STATUS_RATE_LIMITED
    # 451 (Unavailable For Legal Reasons) и 403 — типичный признак геоблока/WAF.
    if status_code in (451, 403):
        return STATUS_GEO_BLOCKED
    if 200 <= status_code < 300:
        return STATUS_OK
    return STATUS_ERROR


def probe_one(name: str, settings: Settings, *, timeout_sec: float = 8.0) -> dict[str, Any]:
    """Одна REST-проба: латентность, статус-код, классификация."""
    probe = PROBES.get(name)
    if probe is None:
        return {
            "exchange": name,
            "ok": False,
            "status": STATUS_ERROR,
            "status_code": None,
            "elapsed_ms": 0,
            "error": "no probe defined",
            "url": None,
        }

    started = time.monotonic()
    try:
        with mexc_httpx_client(settings, exchange=name) as client:
            if probe.method == "POST":
                r = client.post(probe.url, json=probe.json_body, timeout=timeout_sec)
            else:
                r = client.get(probe.url, timeout=timeout_sec)
        status = classify(r.status_code)
        return {
            "exchange": name,
            "ok": status == STATUS_OK,
            "status": status,
            "status_code": r.status_code,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "url": probe.url,
        }
    except Exception as e:  # noqa: BLE001 — сетевые ошибки не должны падать наружу
        return {
            "exchange": name,
            "ok": False,
            "status": STATUS_ERROR,
            "status_code": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(e).__name__}: {e}",
            "url": probe.url,
        }


def probe_all(
    settings: Settings,
    exchanges: list[str] | None = None,
    *,
    timeout_sec: float = 8.0,
) -> dict[str, dict[str, Any]]:
    """Параллельные REST-пробы всех (или выбранных) бирж."""
    names = [n for n in (exchanges or list(PROBES)) if n in PROBES]
    out: dict[str, dict[str, Any]] = {}
    if not names:
        return out
    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        futures = {
            pool.submit(probe_one, name, settings, timeout_sec=timeout_sec): name
            for name in names
        }
        for fut in as_completed(futures):
            res = fut.result()
            out[res["exchange"]] = res
    return out
