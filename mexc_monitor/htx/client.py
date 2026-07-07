"""
HTX (Huobi) Public API Client — market data (без аутентификации).

Spot Base URL: https://api.huobi.pro
Futures Base URL: https://api.hbdm.com

Endpoints:
  GET /market/tickers — тикеры (spot)
  GET /linear-swap-ex/market/detail/batch_merged — тикеры (futures linear swap)
  GET /market/history/kline — свечи (spot)
  GET /linear-swap-ex/market/history/kline — свечи (futures)

HTX API возвращает JSON с "status": "ok"/"error".
Spot tickers: {"status": "ok", "data": [...]}
Futures tickers: {"status": "ok", "ticks": [...]}
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
_DEFAULT_SPOT_BASE_URL = "https://api.huobi.pro"
_DEFAULT_FUTURES_BASE_URL = "https://api.hbdm.com"
_DEFAULT_TIMEOUT_SEC = 15.0
_DEFAULT_ENDPOINTS = {
    "spot_tickers": "/market/tickers",
    "futures_tickers": "/linear-swap-ex/market/detail/batch_merged",
    "spot_klines": "/market/history/kline",
    "futures_klines": "/linear-swap-ex/market/history/kline",
}

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"

# Маппинг стандартных интервалов в формат HTX
_INTERVAL_MAP: dict[str, str] = {
    "5m": "5min",
    "15m": "15min",
    "1h": "60min",
    "4h": "4hour",
    "1d": "1day",
}


def _load_htx_config() -> dict[str, Any]:
    """Загрузить секцию 'htx' из config/external_apis.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            full_config = json.load(f)
        return full_config.get("htx", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning("Cannot read htx config from %s: %s", _CONFIG_PATH, e)
        return {}


class HtxApiError(RuntimeError):
    """Ошибка при обращении к HTX API."""
    pass


@dataclass(frozen=True)
class HtxBookTicker:
    """Лучшие bid/ask с HTX."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float = 0.0
    volume_24h_quote: float = 0.0


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float из API-ответа."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class HtxPublicClient:
    """Публичный клиент HTX (без ключей). Поддерживает spot и futures."""

    def __init__(
        self,
        spot_base_url: str | None = None,
        futures_base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_htx_config()
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
        """HTTP GET с обработкой ошибок HTX API."""
        url = f"{base_url}{path}"
        try:
            r = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise HtxApiError(
                f"HTX API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise HtxApiError(
                f"HTX HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            raise HtxApiError(
                f"Rate limited by HTX (HTTP 429): {r.text[:300]}"
            )
        if r.status_code >= 400:
            raise HtxApiError(
                f"HTX HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise HtxApiError(f"HTX invalid JSON response: {e}") from e

        # HTX API-level error: {"status": "error", "err-code": "...", "err-msg": "..."}
        if isinstance(data, dict) and data.get("status") == "error":
            err_code = data.get("err-code", "unknown")
            err_msg = data.get("err-msg", "unknown")
            raise HtxApiError(
                f"HTX API error code={err_code} msg={err_msg}"
            )

        return data

    def book_tickers(self, market: str = "spot") -> list[HtxBookTicker]:
        """
        Получить тикеры.

        market: "spot" или "futures"
        Spot endpoint: GET /market/tickers → {"status": "ok", "data": [...]}
        Futures endpoint: GET /linear-swap-ex/market/detail/batch_merged → {"status": "ok", "ticks": [...]}
        """
        base_url = self._get_base_url(market)
        endpoint_key = f"{market}_tickers"
        path = self._endpoints.get(endpoint_key, _DEFAULT_ENDPOINTS.get(endpoint_key, ""))

        data = self._get(base_url, path)

        if market == "spot":
            items = data.get("data", [])
        else:
            items = data.get("ticks", [])

        if not isinstance(items, list):
            return []

        tickers: list[HtxBookTicker] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            if market == "spot":
                sym = item.get("symbol", "")
                bid_price = _parse_float(item.get("bid"))
                bid_qty = _parse_float(item.get("bidSize"))
                ask_price = _parse_float(item.get("ask"))
                ask_qty = _parse_float(item.get("askSize"))
                volume_base = _parse_float(item.get("vol"))
                volume_quote = _parse_float(item.get("amount"))
            else:
                # Futures: contract_code, bid/ask arrays [price, qty], vol, amount
                sym = item.get("contract_code", "")
                bid_arr = item.get("bid")
                ask_arr = item.get("ask")
                bid_price = _parse_float(bid_arr[0]) if isinstance(bid_arr, list) and len(bid_arr) > 0 else 0.0
                bid_qty = _parse_float(bid_arr[1]) if isinstance(bid_arr, list) and len(bid_arr) > 1 else 0.0
                ask_price = _parse_float(ask_arr[0]) if isinstance(ask_arr, list) and len(ask_arr) > 0 else 0.0
                ask_qty = _parse_float(ask_arr[1]) if isinstance(ask_arr, list) and len(ask_arr) > 1 else 0.0
                volume_base = _parse_float(item.get("vol"))
                volume_quote = _parse_float(item.get("amount"))

            if not sym:
                continue
            if bid_price <= 0 or ask_price <= 0:
                continue

            # Symbol normalization: HTX uses lowercase → uppercase
            normalized_symbol = sym.upper().replace("-", "")

            tickers.append(HtxBookTicker(
                symbol=normalized_symbol,
                bid_price=bid_price,
                bid_qty=bid_qty,
                ask_price=ask_price,
                ask_qty=ask_qty,
                volume_24h_base=volume_base,
                volume_24h_quote=volume_quote,
            ))

        return tickers

    def klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 96,
        market: str = "spot",
    ) -> list[dict[str, Any]]:
        """
        Получить свечи в унифицированном формате.

        HTX kline response: {"status": "ok", "data": [{"id": ts, "open": ..., "close": ..., ...}]}
        Возвращает list[dict] с ключами: time, open, high, low, close, volume.
        """
        base_url = self._get_base_url(market)
        endpoint_key = f"{market}_klines"
        path = self._endpoints.get(endpoint_key, _DEFAULT_ENDPOINTS.get(endpoint_key, ""))

        htx_interval = _INTERVAL_MAP.get(interval, interval)

        # HTX uses lowercase symbols for spot, contract_code for futures
        if market == "spot":
            htx_symbol = symbol.lower()
        else:
            # Futures: contract_code format, e.g. "BTC-USDT"
            htx_symbol = symbol.upper()
            # If symbol is like "BTCUSDT", convert to "BTC-USDT" for futures
            if "-" not in htx_symbol and htx_symbol.endswith("USDT"):
                base = htx_symbol[:-4]
                htx_symbol = f"{base}-USDT"

        params: dict[str, Any] = {
            "symbol" if market == "spot" else "contract_code": htx_symbol,
            "period": htx_interval,
            "size": min(limit, 2000),
        }

        data = self._get(base_url, path, params=params)

        items = data.get("data", [])
        if not isinstance(items, list):
            return []

        candles: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ts = item.get("id", 0)
            candles.append({
                "time": int(ts),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("vol", item.get("amount", 0))),
            })

        # Sort by time ascending
        candles.sort(key=lambda c: c["time"])
        return candles


def htx_snapshot_rows(
    client: HtxPublicClient | None = None,
    market: str = "futures",
) -> list[BookTickerRow]:
    """
    Получить тикеры HTX и нормализовать в list[BookTickerRow].

    market: "spot" или "futures"
    Вычисляет mid/spread_abs/spread_bps.
    """
    if client is None:
        client = HtxPublicClient()

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
            observed_at=now_iso,
        ))

    return rows
