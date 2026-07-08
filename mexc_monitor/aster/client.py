"""
AsterDEX Public API Client — market data (без аутентификации).

Base URL: https://fapi.asterdex.com
Endpoints:
  GET /fapi/v1/ticker/bookTicker — лучшие bid/ask (все или один символ)
  GET /fapi/v1/ticker/24hr — 24h статистика
  GET /fapi/v1/depth — стакан
  GET /fapi/v1/klines — свечи
  GET /fapi/v1/premiumIndex — mark price + funding rate
  GET /fapi/v1/exchangeInfo — информация о символах
  GET /fapi/v1/ping — проверка связи
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from mexc_monitor.http_shared import shared_get

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fapi.asterdex.com"
DEFAULT_TIMEOUT_SEC = 15.0


class AsterApiError(RuntimeError):
    """Ошибка при обращении к AsterDEX API."""
    pass


@dataclass(frozen=True)
class AsterBookTicker:
    """Лучшие bid/ask с AsterDEX."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    time_ms: int


@dataclass(frozen=True)
class AsterTicker24h:
    """24h статистика с AsterDEX."""
    symbol: str
    last_price: float
    price_change_percent: float
    high_price: float
    low_price: float
    volume: float
    quote_volume: float
    open_time: int
    close_time: int


@dataclass(frozen=True)
class AsterFundingInfo:
    """Mark price и funding rate."""
    symbol: str
    mark_price: float
    index_price: float
    last_funding_rate: float
    next_funding_time: int


def _parse_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class AsterPublicClient:
    """Публичный клиент AsterDEX (без ключей)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        try:
            r = shared_get(url, params=params, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise AsterApiError(f"HTTP error: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise AsterApiError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except Exception as e:
            raise AsterApiError(f"Invalid JSON: {e}") from e
        if isinstance(data, dict) and "code" in data:
            code = data.get("code")
            if code not in (None, 0, "0", 200, "200"):
                msg = data.get("msg", "unknown")
                raise AsterApiError(f"Aster error code={code} msg={msg}")
        return data

    def ping(self) -> bool:
        """Проверка связи."""
        try:
            self._get("/fapi/v1/ping")
            return True
        except AsterApiError:
            return False

    def server_time(self) -> int:
        """Серверное время (ms)."""
        data = self._get("/fapi/v1/time")
        return int(data.get("serverTime", 0))

    def exchange_info(self) -> dict[str, Any]:
        """Информация о бирже и символах."""
        return self._get("/fapi/v1/exchangeInfo")

    def get_symbols(self) -> list[str]:
        """Список торгуемых символов."""
        info = self.exchange_info()
        symbols = info.get("symbols", [])
        return [
            s["symbol"]
            for s in symbols
            if isinstance(s, dict) and s.get("status") == "TRADING"
        ]

    def book_ticker(self, symbol: str | None = None) -> list[AsterBookTicker]:
        """
        Лучшие bid/ask.
        Без symbol — все символы.
        """
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = self._get("/fapi/v1/ticker/bookTicker", params=params or None)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        result = []
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
            t = int(item.get("time", 0))
            if bp <= 0 or ap <= 0:
                continue
            result.append(AsterBookTicker(
                symbol=sym,
                bid_price=bp,
                bid_qty=bq,
                ask_price=ap,
                ask_qty=aq,
                time_ms=t,
            ))
        return result

    def ticker_24h(self, symbol: str | None = None) -> list[AsterTicker24h]:
        """24h статистика."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = self._get("/fapi/v1/ticker/24hr", params=params or None)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            result.append(AsterTicker24h(
                symbol=item.get("symbol", ""),
                last_price=_parse_float(item.get("lastPrice")),
                price_change_percent=_parse_float(item.get("priceChangePercent")),
                high_price=_parse_float(item.get("highPrice")),
                low_price=_parse_float(item.get("lowPrice")),
                volume=_parse_float(item.get("volume")),
                quote_volume=_parse_float(item.get("quoteVolume")),
                open_time=int(item.get("openTime", 0)),
                close_time=int(item.get("closeTime", 0)),
            ))
        return result

    def depth(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        """Стакан (order book)."""
        data = self._get("/fapi/v1/depth", params={
            "symbol": symbol.upper(),
            "limit": min(limit, 1000),
        })
        return data if isinstance(data, dict) else {}

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
    ) -> list[list]:
        """Свечи."""
        data = self._get("/fapi/v1/klines", params={
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1500),
        })
        return data if isinstance(data, list) else []

    def premium_index(self, symbol: str | None = None) -> list[AsterFundingInfo]:
        """Mark price и funding rate."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = self._get("/fapi/v1/premiumIndex", params=params or None)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            result.append(AsterFundingInfo(
                symbol=item.get("symbol", ""),
                mark_price=_parse_float(item.get("markPrice")),
                index_price=_parse_float(item.get("indexPrice")),
                last_funding_rate=_parse_float(item.get("lastFundingRate")),
                next_funding_time=int(item.get("nextFundingTime", 0)),
            ))
        return result

    def funding_rate(
        self,
        symbol: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """История funding rate."""
        data = self._get("/fapi/v1/fundingRate", params={
            "symbol": symbol.upper(),
            "limit": min(limit, 1000),
        })
        return data if isinstance(data, list) else []


def aster_snapshot_rows(client: AsterPublicClient | None = None) -> list[BookTickerRow]:
    """
    Получить тикеры AsterDEX и нормализовать в list[BookTickerRow].

    Вызывает book_ticker() и ticker_24h(), мержит по symbol,
    вычисляет mid/spread_abs/spread_bps.
    """
    if client is None:
        client = AsterPublicClient()

    book_tickers = client.book_ticker()
    tickers_24h = client.ticker_24h()

    # Индексируем 24h данные по символу для быстрого мержа
    volume_map: dict[str, AsterTicker24h] = {t.symbol: t for t in tickers_24h}

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
        volume_24h_base = vol_info.volume if vol_info else 0.0
        volume_24h_quote = vol_info.quote_volume if vol_info else 0.0

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
