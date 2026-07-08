"""
OKX Public API Client — market data (без аутентификации).

Base URL: https://www.okx.com
Endpoints:
  GET /api/v5/market/tickers?instType=SPOT — тикеры спот
  GET /api/v5/market/tickers?instType=SWAP — тикеры свопов (перпетуалы)
  GET /api/v5/market/candles — свечи
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

DEFAULT_BASE_URL = "https://www.okx.com"
DEFAULT_TIMEOUT_SEC = 15.0

# Маппинг стандартных интервалов в формат OKX
_INTERVAL_MAP: dict[str, str] = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}


def _load_config() -> dict[str, Any]:
    """Загрузка конфигурации OKX из external_apis.json."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = json.load(f)
        return full_config.get("okx", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из строки или числа."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _normalize_symbol(inst_id: str) -> str:
    """
    Нормализация символа OKX в унифицированный формат.

    "BTC-USDT" → "BTCUSDT"
    "BTC-USDT-SWAP" → "BTCUSDT"
    """
    # Убираем суффикс -SWAP
    s = inst_id.replace("-SWAP", "")
    # Убираем дефисы
    s = s.replace("-", "")
    return s.upper()


class OkxApiError(RuntimeError):
    """Ошибка при обращении к OKX API."""
    pass


@dataclass(frozen=True)
class OkxBookTicker:
    """Лучшие bid/ask с OKX."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float
    volume_24h_quote: float


class OkxPublicClient:
    """Публичный клиент OKX (без ключей)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        config = _load_config()
        self._base_url = (base_url or config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout_sec or config.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
        self._endpoints = config.get("endpoints", {
            "tickers": "/api/v5/market/tickers",
            "candles": "/api/v5/market/candles",
        })

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок OKX API."""
        url = f"{self._base_url}{path}"
        try:
            r = shared_get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise OkxApiError(
                f"OKX API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise OkxApiError(f"OKX HTTP error: {type(e).__name__}: {e}") from e

        if r.status_code == 429:
            raise OkxApiError(f"Rate limited by OKX: HTTP 429")

        if r.status_code >= 400:
            raise OkxApiError(f"OKX HTTP {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
        except Exception as e:
            raise OkxApiError(f"OKX invalid JSON response: {e}") from e

        # OKX API возвращает {"code": "0", "msg": "", "data": [...]}
        if isinstance(data, dict) and "code" in data:
            code = data.get("code", "0")
            if str(code) != "0":
                msg = data.get("msg", "unknown error")
                raise OkxApiError(f"OKX API error code={code} msg={msg}")

        return data

    def book_tickers(self, inst_type: str = "SWAP") -> list[OkxBookTicker]:
        """
        Получение тикеров OKX.

        Args:
            inst_type: "SPOT" или "SWAP" (perpetual futures)

        Returns:
            Список OkxBookTicker
        """
        path = self._endpoints.get("tickers", "/api/v5/market/tickers")
        data = self._get(path, params={"instType": inst_type.upper()})

        items = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []

        result: list[OkxBookTicker] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            inst_id = item.get("instId", "")
            if not inst_id:
                continue

            bid_price = _parse_float(item.get("bidPx"))
            bid_qty = _parse_float(item.get("bidSz"))
            ask_price = _parse_float(item.get("askPx"))
            ask_qty = _parse_float(item.get("askSz"))
            volume_24h_base = _parse_float(item.get("vol24h"))
            volume_24h_quote = _parse_float(item.get("volCcy24h"))

            if bid_price <= 0 or ask_price <= 0:
                continue

            result.append(OkxBookTicker(
                symbol=inst_id,
                bid_price=bid_price,
                bid_qty=bid_qty,
                ask_price=ask_price,
                ask_qty=ask_qty,
                volume_24h_base=volume_24h_base,
                volume_24h_quote=volume_24h_quote,
            ))

        return result

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
    ) -> list[dict]:
        """
        Получение свечей OKX.

        Args:
            symbol: Символ в формате OKX (e.g. "BTC-USDT-SWAP")
            interval: Стандартный интервал (5m, 15m, 1h, 4h, 1d)
            limit: Количество свечей (макс 300)

        Returns:
            Список словарей {time, open, high, low, close, volume}
        """
        path = self._endpoints.get("candles", "/api/v5/market/candles")
        okx_interval = _INTERVAL_MAP.get(interval, interval)

        data = self._get(path, params={
            "instId": symbol,
            "bar": okx_interval,
            "limit": str(min(limit, 300)),
        })

        items = data.get("data", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []

        # OKX candles: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        result: list[dict] = []
        for candle in items:
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            result.append({
                "time": int(candle[0]) // 1000,  # ms → seconds
                "open": _parse_float(candle[1]),
                "high": _parse_float(candle[2]),
                "low": _parse_float(candle[3]),
                "close": _parse_float(candle[4]),
                "volume": _parse_float(candle[5]),
            })

        return result


def okx_snapshot_rows(
    client: OkxPublicClient | None = None,
    market: str = "futures",
) -> list[BookTickerRow]:
    """
    Получить тикеры OKX и нормализовать в list[BookTickerRow].

    Args:
        client: Экземпляр OkxPublicClient (создаётся по умолчанию)
        market: "spot" или "futures" (SWAP)

    Returns:
        Список BookTickerRow с нормализованными символами
    """
    if client is None:
        client = OkxPublicClient()

    # Маппинг market → instType
    inst_type = "SPOT" if market == "spot" else "SWAP"
    tickers = client.book_tickers(inst_type=inst_type)

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

        normalized_symbol = _normalize_symbol(t.symbol)

        rows.append(BookTickerRow(
            symbol=normalized_symbol,
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
