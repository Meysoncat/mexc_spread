"""
dYdX v4 Public API Client — market data (без аутентификации).

Base URL: https://indexer.dydx.trade
Endpoints:
  GET /v4/perpetualMarkets — список перпетуальных рынков (volume24H, oraclePrice)
  GET /v4/orderbooks/perpetualMarket/{market} — стакан (bids/asks)
  GET /v4/candles/perpetualMarkets/{market} — свечи (OHLCV)

dYdX v4 indexer API возвращает JSON без обёртки retCode — проверяем HTTP-статус.
Символы в формате "BTC-USD" → нормализуем в "BTCUSD" (убираем дефис).
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

DEFAULT_BASE_URL = "https://indexer.dydx.trade"
DEFAULT_TIMEOUT_SEC = 15.0

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"

# Маппинг стандартных интервалов в формат dYdX v4
_INTERVAL_MAP: dict[str, str] = {
    "5m": "5MINS",
    "15m": "15MINS",
    "1h": "1HOUR",
    "4h": "4HOURS",
    "1d": "1DAY",
}


def _load_dydx_config() -> dict[str, Any]:
    """Загрузить секцию 'dydx' из config/external_apis.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("dydx", {})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Cannot read dydx config from %s: %s", _CONFIG_PATH, e)
        return {}


def _normalize_symbol(raw_symbol: str) -> str:
    """Нормализация символа dYdX: 'BTC-USD' → 'BTCUSD'."""
    return raw_symbol.replace("-", "")


class DydxApiError(RuntimeError):
    """Ошибка при обращении к dYdX API."""
    pass


@dataclass(frozen=True)
class DydxBookTicker:
    """Лучшие bid/ask с dYdX perpetual market."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float
    volume_24h_quote: float


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из строки или числа."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class DydxPublicClient:
    """Публичный клиент dYdX v4 indexer API (без ключей)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_dydx_config()
        self._base_url = (base_url or cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
        self._endpoints = cfg.get("endpoints", {})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок dYdX API."""
        url = f"{self._base_url}{path}"
        try:
            r = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise DydxApiError(
                f"dYdX API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise DydxApiError(
                f"dYdX HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            raise DydxApiError(
                f"Rate limited by dYdX (429): {r.text[:300]}"
            )
        if r.status_code >= 400:
            raise DydxApiError(
                f"dYdX HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise DydxApiError(f"dYdX invalid JSON response: {e}") from e

        return data

    def perpetual_markets(self) -> dict[str, Any]:
        """
        Получить список перпетуальных рынков.

        Endpoint: GET /v4/perpetualMarkets
        Returns: {"markets": {"BTC-USD": {...}, "ETH-USD": {...}, ...}}
        """
        path = self._endpoints.get("perpetual_markets", "/v4/perpetualMarkets")
        data = self._get(path)
        if not isinstance(data, dict):
            return {}
        return data.get("markets", {})

    def orderbook(self, market: str) -> dict[str, Any]:
        """
        Получить стакан для конкретного рынка.

        Endpoint: GET /v4/orderbooks/perpetualMarket/{market}
        Returns: {"bids": [{"price": "...", "size": "..."}, ...], "asks": [...]}
        """
        path_template = self._endpoints.get(
            "orderbook", "/v4/orderbooks/perpetualMarket/{market}"
        )
        path = path_template.format(market=market)
        data = self._get(path)
        if not isinstance(data, dict):
            return {}
        return data

    def book_tickers(self) -> list[DydxBookTicker]:
        """
        Получить тикеры всех перпетуальных рынков.

        Сначала загружает список рынков (volume, oraclePrice),
        затем для каждого рынка запрашивает стакан для bid/ask.
        """
        markets = self.perpetual_markets()
        if not markets:
            return []

        tickers: list[DydxBookTicker] = []
        for market_name, market_info in markets.items():
            if not isinstance(market_info, dict):
                continue

            # Получаем volume из perpetualMarkets
            volume_24h_quote = _parse_float(market_info.get("volume24H"))
            oracle_price = _parse_float(market_info.get("oraclePrice"))
            volume_24h_base = (
                volume_24h_quote / oracle_price if oracle_price > 0 else 0.0
            )

            # Получаем стакан для bid/ask
            try:
                ob = self.orderbook(market_name)
            except DydxApiError as e:
                logger.warning("Failed to fetch orderbook for %s: %s", market_name, e)
                continue

            bids = ob.get("bids", [])
            asks = ob.get("asks", [])

            if not bids or not asks:
                continue

            # Лучший bid — первый элемент bids, лучший ask — первый элемент asks
            best_bid = bids[0] if isinstance(bids[0], dict) else {}
            best_ask = asks[0] if isinstance(asks[0], dict) else {}

            bid_price = _parse_float(best_bid.get("price"))
            bid_qty = _parse_float(best_bid.get("size"))
            ask_price = _parse_float(best_ask.get("price"))
            ask_qty = _parse_float(best_ask.get("size"))

            if bid_price <= 0 or ask_price <= 0:
                continue

            tickers.append(DydxBookTicker(
                symbol=market_name,
                bid_price=bid_price,
                bid_qty=bid_qty,
                ask_price=ask_price,
                ask_qty=ask_qty,
                volume_24h_base=volume_24h_base,
                volume_24h_quote=volume_24h_quote,
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

        Endpoint: GET /v4/candles/perpetualMarkets/{market}
        dYdX использует формат символа "BTC-USD" в URL.
        Возвращает list[dict] с ключами: time, open, high, low, close, volume.
        """
        path_template = self._endpoints.get(
            "candles", "/v4/candles/perpetualMarkets/{market}"
        )
        path = path_template.format(market=symbol)

        dydx_interval = _INTERVAL_MAP.get(interval, interval)

        data = self._get(path, params={
            "resolution": dydx_interval,
            "limit": min(limit, 1000),
        })

        # dYdX returns {"candles": [{...}, ...]}
        candles_raw = data.get("candles", []) if isinstance(data, dict) else []
        if not isinstance(candles_raw, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in candles_raw:
            if not isinstance(item, dict):
                continue
            # dYdX candle fields: startedAt (ISO8601), open, high, low, close,
            # baseTokenVolume, usdVolume
            started_at = item.get("startedAt", "")
            try:
                dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except (ValueError, AttributeError):
                continue

            candles.append({
                "time": ts,
                "open": _parse_float(item.get("open")),
                "high": _parse_float(item.get("high")),
                "low": _parse_float(item.get("low")),
                "close": _parse_float(item.get("close")),
                "volume": _parse_float(item.get("baseTokenVolume")),
            })

        # Сортируем по time (старые первые)
        candles.sort(key=lambda c: c["time"])
        return candles


def dydx_snapshot_rows(client: DydxPublicClient | None = None) -> list[BookTickerRow]:
    """
    Получить тикеры dYdX perpetual markets и нормализовать в list[BookTickerRow].

    Символы нормализуются: "BTC-USD" → "BTCUSD".
    """
    if client is None:
        client = DydxPublicClient()

    tickers = client.book_tickers()
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
