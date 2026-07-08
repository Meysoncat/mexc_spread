"""
Binance Public API Client — market data (без аутентификации).

Spot Base URL: https://api.binance.com
Futures Base URL: https://fapi.binance.com

Endpoints:
  GET /api/v3/ticker/bookTicker — лучшие bid/ask (spot)
  GET /fapi/v1/ticker/bookTicker — лучшие bid/ask (futures)
  GET /api/v3/ticker/24hr — 24h статистика (spot)
  GET /fapi/v1/ticker/24hr — 24h статистика (futures)
  GET /api/v3/klines — свечи (spot)
  GET /fapi/v1/klines — свечи (futures)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mexc_monitor.http_shared import shared_get

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

# Fallback defaults (used when config/external_apis.json is missing or incomplete)
_DEFAULT_SPOT_BASE_URL = "https://api.binance.com"
_DEFAULT_FUTURES_BASE_URL = "https://fapi.binance.com"
_DEFAULT_TIMEOUT_SEC = 15.0
_DEFAULT_ENDPOINTS = {
    "spot_book_ticker": "/api/v3/ticker/bookTicker",
    "futures_book_ticker": "/fapi/v1/ticker/bookTicker",
    "spot_ticker_24hr": "/api/v3/ticker/24hr",
    "futures_ticker_24hr": "/fapi/v1/ticker/24hr",
    "spot_klines": "/api/v3/klines",
    "futures_klines": "/fapi/v1/klines",
}


def _load_binance_config() -> dict[str, Any]:
    """Load Binance config from external_apis.json with fallback defaults."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = json.load(f)
        return full_config.get("binance", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


class BinanceApiError(RuntimeError):
    """Ошибка при обращении к Binance API."""
    pass


@dataclass(frozen=True)
class BinanceBookTicker:
    """Лучшие bid/ask с Binance."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float = 0.0
    volume_24h_quote: float = 0.0


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из API-ответа."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class BinancePublicClient:
    """Публичный клиент Binance (без ключей)."""

    def __init__(
        self,
        spot_base_url: str | None = None,
        futures_base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_binance_config()
        self._spot_base_url = (
            spot_base_url or cfg.get("spot_base_url", _DEFAULT_SPOT_BASE_URL)
        ).rstrip("/")
        self._futures_base_url = (
            futures_base_url or cfg.get("futures_base_url", _DEFAULT_FUTURES_BASE_URL)
        ).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)
        self._endpoints = cfg.get("endpoints", _DEFAULT_ENDPOINTS)

    def _get_base_url(self, market: str) -> str:
        """Return base URL for the given market."""
        if market == "spot":
            return self._spot_base_url
        return self._futures_base_url

    def _get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок."""
        url = f"{base_url}{path}"
        try:
            r = shared_get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise BinanceApiError(
                f"Binance API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise BinanceApiError(
                f"Binance HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            raise BinanceApiError(
                f"Rate limited by Binance (HTTP 429): {r.text[:300]}"
            )
        if r.status_code >= 400:
            raise BinanceApiError(
                f"Binance HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise BinanceApiError(f"Binance invalid JSON response: {e}") from e

        # Binance API-level error (e.g. {"code": -1121, "msg": "Invalid symbol."})
        if isinstance(data, dict) and "code" in data:
            code = data.get("code")
            if code not in (None, 0, 200):
                msg = data.get("msg", "unknown")
                raise BinanceApiError(f"Binance API error code={code} msg={msg}")

        return data

    def book_tickers(self, market: str = "spot") -> list[BinanceBookTicker]:
        """
        Лучшие bid/ask.
        market: "spot" или "futures"
        """
        base_url = self._get_base_url(market)
        endpoint_key = f"{market}_book_ticker"
        path = self._endpoints.get(endpoint_key, _DEFAULT_ENDPOINTS[endpoint_key])

        data = self._get(base_url, path)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        result: list[BinanceBookTicker] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol", "")
            if not sym:
                continue
            bp = _parse_float(item.get("bidPrice"))
            bq = _parse_float(item.get("bidQty"))
            ap = _parse_float(item.get("askPrice"))
            aq = _parse_float(item.get("askQty"))
            if bp <= 0 or ap <= 0:
                continue
            result.append(BinanceBookTicker(
                symbol=sym,
                bid_price=bp,
                bid_qty=bq,
                ask_price=ap,
                ask_qty=aq,
            ))
        return result

    def ticker_24h(self, market: str = "spot") -> list[dict[str, Any]]:
        """24h статистика для получения volume данных."""
        base_url = self._get_base_url(market)
        endpoint_key = f"{market}_ticker_24hr"
        path = self._endpoints.get(endpoint_key, _DEFAULT_ENDPOINTS[endpoint_key])

        data = self._get(base_url, path)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        return data

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
        market: str = "spot",
    ) -> list[list]:
        """
        Свечи (klines).
        market: "spot" или "futures"
        """
        base_url = self._get_base_url(market)
        endpoint_key = f"{market}_klines"
        path = self._endpoints.get(endpoint_key, _DEFAULT_ENDPOINTS[endpoint_key])

        data = self._get(base_url, path, params={
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1500),
        })
        return data if isinstance(data, list) else []


def binance_snapshot_rows(
    client: BinancePublicClient | None = None,
    market: str = "futures",
) -> list[BookTickerRow]:
    """
    Получить тикеры Binance и нормализовать в list[BookTickerRow].

    Вызывает book_tickers() и ticker_24h(), мержит по symbol,
    вычисляет mid/spread_abs/spread_bps.
    """
    if client is None:
        client = BinancePublicClient()

    book_tickers = client.book_tickers(market=market)
    tickers_24h = client.ticker_24h(market=market)

    # Индексируем 24h данные по символу для быстрого мержа
    volume_map: dict[str, dict[str, Any]] = {}
    for t in tickers_24h:
        if isinstance(t, dict) and t.get("symbol"):
            volume_map[t["symbol"]] = t

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[BookTickerRow] = []

    for bt in book_tickers:
        bid = bt.bid_price
        ask = bt.ask_price
        mid = (bid + ask) / 2
        spread_abs = ask - bid
        spread_bps: float | None = (
            10_000 * spread_abs / mid if mid > 0 else None
        )

        # Получаем 24h volume из ticker_24h (если есть)
        vol_info = volume_map.get(bt.symbol)
        if vol_info:
            volume_24h_base = _parse_float(vol_info.get("volume"))
            volume_24h_quote = _parse_float(vol_info.get("quoteVolume"))
        else:
            volume_24h_base = bt.volume_24h_base
            volume_24h_quote = bt.volume_24h_quote

        rows.append(BookTickerRow(
            symbol=bt.symbol,
            bid=bid,
            ask=ask,
            bid_qty=bt.bid_qty,
            ask_qty=bt.ask_qty,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            volume_24h_base=volume_24h_base,
            volume_24h_quote=volume_24h_quote,
            observed_at=now_iso,
        ))

    return rows
