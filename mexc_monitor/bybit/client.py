"""
Bybit Public API Client — market data (без аутентификации).

Base URL: https://api.bybit.com
Endpoints:
  GET /v5/market/tickers?category=linear — тикеры линейных перпов (best bid/ask, volume, funding)
  GET /v5/market/kline — свечи (OHLCV)

Bybit API v5 возвращает JSON с retCode/retMsg — проверяем retCode == 0.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.bybit.com"
DEFAULT_TIMEOUT_SEC = 15.0

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"

# Маппинг стандартных интервалов в формат Bybit
_INTERVAL_MAP: dict[str, str] = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}


def _load_bybit_config() -> dict[str, Any]:
    """Загрузить секцию 'bybit' из config/external_apis.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("bybit", {})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Cannot read bybit config from %s: %s", _CONFIG_PATH, e)
        return {}


class BybitApiError(RuntimeError):
    """Ошибка при обращении к Bybit API."""
    pass


@dataclass(frozen=True)
class BybitBookTicker:
    """Лучшие bid/ask с Bybit linear perpetual."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float
    volume_24h_quote: float
    funding_rate: float | None


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из строки или числа."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_float_optional(v: Any) -> float | None:
    """Парсинг float, возвращает None если значение отсутствует."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class BybitPublicClient:
    """Публичный клиент Bybit v5 API (без ключей)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_bybit_config()
        self._base_url = (base_url or cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
        self._endpoints = cfg.get("endpoints", {})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок Bybit API."""
        url = f"{self._base_url}{path}"
        try:
            r = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise BybitApiError(
                f"Bybit API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise BybitApiError(
                f"Bybit HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            raise BybitApiError(
                f"Rate limited by Bybit (429): {r.text[:300]}"
            )
        if r.status_code >= 400:
            raise BybitApiError(
                f"Bybit HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise BybitApiError(f"Bybit invalid JSON response: {e}") from e

        # Bybit v5 API: retCode == 0 означает успех
        if isinstance(data, dict):
            ret_code = data.get("retCode")
            if ret_code is not None and int(ret_code) != 0:
                ret_msg = data.get("retMsg", "unknown")
                raise BybitApiError(
                    f"Bybit API error retCode={ret_code} retMsg={ret_msg}"
                )

        return data

    def book_tickers(self, category: str = "linear") -> list[BybitBookTicker]:
        """
        Получить тикеры.

        category: "linear" (USDT perps), "inverse", "spot"
        Endpoint: GET /v5/market/tickers?category=linear
        """
        path = self._endpoints.get("tickers", "/v5/market/tickers")
        data = self._get(path, params={"category": category})

        result_list = data.get("result", {}).get("list", [])
        if not isinstance(result_list, list):
            return []

        tickers: list[BybitBookTicker] = []
        for item in result_list:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol", "")
            if not sym:
                continue

            bid_price = _parse_float(item.get("bid1Price"))
            ask_price = _parse_float(item.get("ask1Price"))
            if bid_price <= 0 or ask_price <= 0:
                continue

            tickers.append(BybitBookTicker(
                symbol=sym,
                bid_price=bid_price,
                bid_qty=_parse_float(item.get("bid1Size")),
                ask_price=ask_price,
                ask_qty=_parse_float(item.get("ask1Size")),
                volume_24h_base=_parse_float(item.get("volume24h")),
                volume_24h_quote=_parse_float(item.get("turnover24h")),
                funding_rate=_parse_float_optional(item.get("fundingRate")),
            ))

        return tickers

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
    ) -> list[dict[str, Any]]:
        """
        Получить свечи в унифицированном формате.

        Bybit v5 kline response: {"result": {"list": [[ts, o, h, l, c, vol, turnover], ...]}}
        Возвращает list[dict] с ключами: time, open, high, low, close, volume.
        """
        path = self._endpoints.get("klines", "/v5/market/kline")
        bybit_interval = _INTERVAL_MAP.get(interval, interval)

        data = self._get(path, params={
            "category": "linear",
            "symbol": symbol.upper(),
            "interval": bybit_interval,
            "limit": min(limit, 1000),
        })

        result_list = data.get("result", {}).get("list", [])
        if not isinstance(result_list, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in result_list:
            if not isinstance(item, (list, tuple)) or len(item) < 6:
                continue
            candles.append({
                "time": int(item[0]) // 1000,  # ms → sec
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })

        # Bybit возвращает свечи в обратном порядке (новые первые) — сортируем по time
        candles.sort(key=lambda c: c["time"])
        return candles


def bybit_snapshot_rows(client: BybitPublicClient | None = None) -> list[BookTickerRow]:
    """
    Получить тикеры Bybit linear perps и нормализовать в list[BookTickerRow].

    Включает funding_rate из тикера.
    """
    if client is None:
        client = BybitPublicClient()

    tickers = client.book_tickers(category="linear")
    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[BookTickerRow] = []

    for t in tickers:
        bid = t.bid_price
        ask = t.ask_price
        mid = (bid + ask) / 2
        spread_abs = ask - bid
        spread_bps: float | None = (
            10_000 * spread_abs / mid if mid > 0 else None
        )

        rows.append(BookTickerRow(
            symbol=t.symbol,
            bid=bid,
            ask=ask,
            bid_qty=t.bid_qty,
            ask_qty=t.ask_qty,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            volume_24h_base=t.volume_24h_base,
            volume_24h_quote=t.volume_24h_quote,
            funding_rate=t.funding_rate,
            observed_at=now_iso,
        ))

    return rows
