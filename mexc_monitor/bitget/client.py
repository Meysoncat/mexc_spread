"""
Bitget Public API Client — market data (без аутентификации).

Base URL: https://api.bitget.com
Endpoints:
  GET /api/v2/mix/market/tickers?productType=USDT-FUTURES — тикеры фьючерсов
  GET /api/v2/mix/market/candles — свечи
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

# Fallback defaults (used when config/external_apis.json is missing or incomplete)
_DEFAULT_BASE_URL = "https://api.bitget.com"
_DEFAULT_TIMEOUT_SEC = 15.0
_DEFAULT_ENDPOINTS = {
    "tickers": "/api/v2/mix/market/tickers",
    "klines": "/api/v2/mix/market/candles",
}

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"


def _load_bitget_config() -> dict[str, Any]:
    """Load Bitget config from external_apis.json with fallback defaults."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            full_config = json.load(f)
        return full_config.get("bitget", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


class BitgetApiError(RuntimeError):
    """Ошибка при обращении к Bitget API."""
    pass


@dataclass(frozen=True)
class BitgetBookTicker:
    """Лучшие bid/ask с Bitget USDT-FUTURES."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float = 0.0
    volume_24h_quote: float = 0.0
    funding_rate: float | None = None


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из API-ответа."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_float_optional(v: Any) -> float | None:
    """Парсинг float, возвращает None если значение отсутствует или пустое."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(raw_symbol: str) -> str:
    """
    Нормализация символа Bitget.

    Bitget symbols like "BTCUSDT_UMCBL" → strip suffix after "_" → "BTCUSDT"
    Symbols without underscore are kept as-is (already "BTCUSDT").
    """
    if "_" in raw_symbol:
        return raw_symbol.split("_")[0]
    return raw_symbol


class BitgetPublicClient:
    """Публичный клиент Bitget (без ключей)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_bitget_config()
        self._base_url = (
            base_url or cfg.get("base_url", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)
        self._endpoints = cfg.get("endpoints", _DEFAULT_ENDPOINTS)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок и проверкой Bitget response code."""
        url = f"{self._base_url}{path}"
        try:
            r = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise BitgetApiError(
                f"Bitget API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise BitgetApiError(
                f"Bitget HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            raise BitgetApiError(
                f"Rate limited by Bitget (HTTP 429): {r.text[:300]}"
            )
        if r.status_code >= 400:
            raise BitgetApiError(
                f"Bitget HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise BitgetApiError(f"Bitget invalid JSON response: {e}") from e

        # Bitget API returns {"code": "00000", "msg": "success", "data": [...]}
        # Check code == "00000" for success
        if isinstance(data, dict) and "code" in data:
            code = str(data.get("code", ""))
            if code != "00000":
                msg = data.get("msg", "unknown")
                raise BitgetApiError(f"Bitget API error code={code} msg={msg}")

        return data

    def book_tickers(self) -> list[BitgetBookTicker]:
        """
        Тикеры USDT-FUTURES.

        GET /api/v2/mix/market/tickers?productType=USDT-FUTURES
        Response: {"code": "00000", "data": [...]}
        Each item: symbol, bidPr, bidSz, askPr, askSz, baseVolume, quoteVolume, fundingRate
        """
        path = self._endpoints.get("tickers", _DEFAULT_ENDPOINTS["tickers"])
        response = self._get(path, params={"productType": "USDT-FUTURES"})

        items = response.get("data", []) if isinstance(response, dict) else []
        if not isinstance(items, list):
            return []

        result: list[BitgetBookTicker] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_symbol = item.get("symbol", "")
            if not raw_symbol:
                continue

            bp = _parse_float(item.get("bidPr"))
            bq = _parse_float(item.get("bidSz"))
            ap = _parse_float(item.get("askPr"))
            aq = _parse_float(item.get("askSz"))

            if bp <= 0 or ap <= 0:
                continue

            base_volume = _parse_float(item.get("baseVolume"))
            quote_volume = _parse_float(item.get("quoteVolume"))
            funding_rate = _parse_float_optional(item.get("fundingRate"))

            symbol = _normalize_symbol(raw_symbol)

            result.append(BitgetBookTicker(
                symbol=symbol,
                bid_price=bp,
                bid_qty=bq,
                ask_price=ap,
                ask_qty=aq,
                volume_24h_base=base_volume,
                volume_24h_quote=quote_volume,
                funding_rate=funding_rate,
            ))
        return result

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
    ) -> list[dict[str, Any]]:
        """
        Свечи (candles).

        GET /api/v2/mix/market/candles
        Params: symbol, productType, granularity, limit
        """
        path = self._endpoints.get("klines", _DEFAULT_ENDPOINTS["klines"])
        response = self._get(path, params={
            "symbol": symbol.upper(),
            "productType": "USDT-FUTURES",
            "granularity": interval,
            "limit": str(min(limit, 1000)),
        })

        items = response.get("data", []) if isinstance(response, dict) else []
        if not isinstance(items, list):
            return []

        # Bitget candles: each item is a list [timestamp, open, high, low, close, volume, quoteVolume]
        result: list[dict[str, Any]] = []
        for candle in items:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            result.append({
                "time": int(candle[0]),
                "open": _parse_float(candle[1]),
                "high": _parse_float(candle[2]),
                "low": _parse_float(candle[3]),
                "close": _parse_float(candle[4]),
                "volume": _parse_float(candle[5]),
            })
        return result


def bitget_snapshot_rows(
    client: BitgetPublicClient | None = None,
) -> list[BookTickerRow]:
    """
    Получить тикеры Bitget и нормализовать в list[BookTickerRow].

    Вызывает book_tickers(), вычисляет mid/spread_abs/spread_bps.
    Включает funding_rate из ответа API.
    """
    if client is None:
        client = BitgetPublicClient()

    book_tickers = client.book_tickers()

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

        rows.append(BookTickerRow(
            symbol=bt.symbol,
            bid=bid,
            ask=ask,
            bid_qty=bt.bid_qty,
            ask_qty=bt.ask_qty,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            volume_24h_base=bt.volume_24h_base,
            volume_24h_quote=bt.volume_24h_quote,
            funding_rate=bt.funding_rate,
            observed_at=now_iso,
        ))

    return rows
