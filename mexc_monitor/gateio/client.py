"""
Gate.io Public API Client — market data (без аутентификации).

Spot Base URL: https://api.gateio.ws
Futures Base URL: https://api.gateio.ws
Endpoints:
  GET /api/v4/spot/tickers — спот тикеры
  GET /api/v4/futures/usdt/tickers — фьючерсные тикеры (USDT-settled)
  GET /api/v4/spot/candlesticks — спот свечи
  GET /api/v4/futures/usdt/candlesticks — фьючерсные свечи
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

DEFAULT_SPOT_BASE_URL = "https://api.gateio.ws"
DEFAULT_FUTURES_BASE_URL = "https://api.gateio.ws"
DEFAULT_TIMEOUT_SEC = 15.0

# Default endpoints
DEFAULT_ENDPOINTS = {
    "spot_tickers": "/api/v4/spot/tickers",
    "futures_tickers": "/api/v4/futures/usdt/tickers",
    "spot_klines": "/api/v4/spot/candlesticks",
    "futures_klines": "/api/v4/futures/usdt/candlesticks",
}


class GateioApiError(RuntimeError):
    """Ошибка при обращении к Gate.io API."""
    pass


@dataclass(frozen=True)
class GateioBookTicker:
    """Лучшие bid/ask с Gate.io."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float
    volume_24h_quote: float


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из любого значения."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _normalize_symbol(raw_symbol: str) -> str:
    """Нормализация символа Gate.io: 'BTC_USDT' → 'BTCUSDT'."""
    return raw_symbol.replace("_", "").upper()


def _load_config() -> dict[str, Any]:
    """Загрузка конфигурации из config/external_apis.json секции 'gateio'."""
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "external_apis.json"
    if not config_path.is_file():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw.get("gateio", {}) if isinstance(raw.get("gateio"), dict) else {}


class GateioPublicClient:
    """Публичный клиент Gate.io (без ключей)."""

    def __init__(
        self,
        spot_base_url: str | None = None,
        futures_base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_config()
        self._spot_base_url = (
            spot_base_url or cfg.get("spot_base_url", DEFAULT_SPOT_BASE_URL)
        ).rstrip("/")
        self._futures_base_url = (
            futures_base_url or cfg.get("futures_base_url", DEFAULT_FUTURES_BASE_URL)
        ).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
        endpoints = cfg.get("endpoints", {})
        self._endpoints = {
            k: endpoints.get(k, DEFAULT_ENDPOINTS[k])
            for k in DEFAULT_ENDPOINTS
        }

    def _get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок."""
        url = f"{base_url}{path}"
        try:
            r = shared_get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise GateioApiError(
                f"Gate.io API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise GateioApiError(f"Gate.io HTTP error: {type(e).__name__}: {e}") from e
        if r.status_code == 429:
            raise GateioApiError(f"Rate limited by Gate.io (HTTP 429): {url}")
        if r.status_code >= 400:
            raise GateioApiError(f"Gate.io HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except Exception as e:
            raise GateioApiError(f"Gate.io invalid JSON response: {e}") from e
        return data

    def book_tickers(self, market: str = "futures") -> list[GateioBookTicker]:
        """
        Получение тикеров.
        market="spot" → GET /api/v4/spot/tickers
        market="futures" → GET /api/v4/futures/usdt/tickers
        """
        if market == "spot":
            base_url = self._spot_base_url
            path = self._endpoints["spot_tickers"]
        else:
            base_url = self._futures_base_url
            path = self._endpoints["futures_tickers"]

        data = self._get(base_url, path)
        if not isinstance(data, list):
            return []

        result: list[GateioBookTicker] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            if market == "spot":
                sym = item.get("currency_pair", "")
                bid_price = _parse_float(item.get("highest_bid"))
                ask_price = _parse_float(item.get("lowest_ask"))
                bid_qty = 0.0  # Gate.io spot tickers don't provide bid/ask qty
                ask_qty = 0.0
                volume_base = _parse_float(item.get("base_volume"))
                volume_quote = _parse_float(item.get("quote_volume"))
            else:
                sym = item.get("contract", "")
                bid_price = _parse_float(item.get("highest_bid"))
                ask_price = _parse_float(item.get("lowest_ask"))
                bid_qty = 0.0  # Gate.io futures tickers don't provide bid/ask qty
                ask_qty = 0.0
                volume_base = _parse_float(item.get("volume_24h_base"))
                volume_quote = _parse_float(item.get("volume_24h_quote"))

            if not sym or bid_price <= 0 or ask_price <= 0:
                continue

            result.append(GateioBookTicker(
                symbol=sym,
                bid_price=bid_price,
                bid_qty=bid_qty,
                ask_price=ask_price,
                ask_qty=ask_qty,
                volume_24h_base=volume_base,
                volume_24h_quote=volume_quote,
            ))

        return result

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
        market: str = "futures",
    ) -> list[dict]:
        """
        Получение свечей в унифицированном формате.
        Возвращает list[dict] с ключами: time, open, high, low, close, volume.
        """
        if market == "spot":
            base_url = self._spot_base_url
            path = self._endpoints["spot_klines"]
            params: dict[str, Any] = {
                "currency_pair": symbol,
                "interval": interval,
                "limit": min(limit, 1000),
            }
        else:
            base_url = self._futures_base_url
            path = self._endpoints["futures_klines"]
            params = {
                "contract": symbol,
                "interval": interval,
                "limit": min(limit, 2000),
            }

        data = self._get(base_url, path, params=params)
        if not isinstance(data, list):
            return []

        result: list[dict] = []
        for candle in data:
            if market == "spot":
                # Spot: [timestamp, volume, close, high, low, open, ...]
                if not isinstance(candle, list) or len(candle) < 6:
                    continue
                result.append({
                    "time": int(candle[0]),
                    "volume": _parse_float(candle[1]),
                    "close": _parse_float(candle[2]),
                    "high": _parse_float(candle[3]),
                    "low": _parse_float(candle[4]),
                    "open": _parse_float(candle[5]),
                })
            else:
                # Futures: dict with t, o, h, l, c, v fields
                if not isinstance(candle, dict):
                    continue
                result.append({
                    "time": int(candle.get("t", 0)),
                    "open": _parse_float(candle.get("o")),
                    "high": _parse_float(candle.get("h")),
                    "low": _parse_float(candle.get("l")),
                    "close": _parse_float(candle.get("c")),
                    "volume": _parse_float(candle.get("v")),
                })

        return result


def gateio_snapshot_rows(
    client: GateioPublicClient | None = None,
    market: str = "futures",
) -> list[BookTickerRow]:
    """
    Получить тикеры Gate.io и нормализовать в list[BookTickerRow].

    Вызывает book_tickers(market), нормализует символы,
    вычисляет mid/spread_abs/spread_bps.
    """
    if client is None:
        client = GateioPublicClient()

    tickers = client.book_tickers(market=market)
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
            symbol=_normalize_symbol(t.symbol),
            bid=bid,
            ask=ask,
            bid_qty=t.bid_qty,
            ask_qty=t.ask_qty,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            volume_24h_base=t.volume_24h_base,
            volume_24h_quote=t.volume_24h_quote,
            observed_at=now_iso,
        ))

    return rows
