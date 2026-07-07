"""
Hyperliquid Public API Client — market data (без аутентификации).

Base URL: https://api.hyperliquid.xyz
Endpoints:
  POST /info {"type": "allMids"} — mid prices для всех активов
  POST /info {"type": "metaAndAssetCtxs"} — мета-данные + контексты активов (funding, volume, impactPxs)
  POST /info {"type": "candleSnapshot", "req": {...}} — свечи
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.hyperliquid.xyz"
DEFAULT_TIMEOUT_SEC = 15.0
DEFAULT_INFO_ENDPOINT = "/info"


def _load_config() -> dict[str, Any]:
    """Load hyperliquid config from external_apis.json with fallback defaults."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("hyperliquid", {})
    except (OSError, json.JSONDecodeError):
        return {}


class HyperliquidApiError(RuntimeError):
    """Ошибка при обращении к Hyperliquid API."""
    pass


@dataclass(frozen=True)
class HyperliquidBookTicker:
    """Тикер Hyperliquid с bid/ask, volume и funding."""
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    volume_24h_base: float = 0.0
    volume_24h_quote: float = 0.0
    funding_rate: float | None = None


def _parse_float(v: Any) -> float:
    """Safely parse a value to float, returning 0.0 on failure."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _normalize_symbol(raw_symbol: str) -> str:
    """
    Normalize Hyperliquid symbol to standard format.
    Hyperliquid uses bare asset names: "BTC" → "BTCUSDT"
    """
    s = raw_symbol.strip().upper()
    if not s:
        return s
    # If already has USDT suffix, keep as-is
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


class HyperliquidPublicClient:
    """Публичный клиент Hyperliquid (без ключей). Использует POST /info."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_config()
        self._base_url = (base_url or cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout_sec or cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
        self._info_endpoint = cfg.get("endpoints", {}).get("info", DEFAULT_INFO_ENDPOINT)

    def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        """HTTP POST with JSON body and error handling."""
        url = f"{self._base_url}{path}"
        try:
            r = httpx.post(
                url,
                json=body,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        except httpx.TimeoutException as e:
            raise HyperliquidApiError(
                f"Hyperliquid API timeout after {self._timeout}s: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise HyperliquidApiError(
                f"Hyperliquid HTTP error: {type(e).__name__}: {e}"
            ) from e

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "unknown")
            raise HyperliquidApiError(
                f"Rate limited by Hyperliquid (429). Retry-After: {retry_after}"
            )

        if r.status_code >= 400:
            raise HyperliquidApiError(
                f"Hyperliquid HTTP {r.status_code}: {r.text[:300]}"
            )

        try:
            return r.json()
        except Exception as e:
            raise HyperliquidApiError(
                f"Hyperliquid invalid JSON response: {e}"
            ) from e

    def all_mids(self) -> dict[str, str]:
        """
        Get all mid prices.
        Returns dict: asset → mid price string, e.g. {"BTC": "50000.5", "ETH": "3000.2"}
        """
        data = self._post(self._info_endpoint, {"type": "allMids"})
        if not isinstance(data, dict):
            return {}
        return data

    def meta_and_asset_ctxs(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """
        Get meta info and asset contexts.
        Returns (meta_dict, list_of_asset_ctxs).
        Each asset ctx has: funding, openInterest, dayNtlVlm, markPx, midPx, impactPxs, etc.
        """
        data = self._post(self._info_endpoint, {"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return {}, []
        meta = data[0] if isinstance(data[0], dict) else {}
        asset_ctxs = data[1] if isinstance(data[1], list) else []
        return meta, asset_ctxs

    def book_tickers(self) -> list[HyperliquidBookTicker]:
        """
        Get book tickers for all Hyperliquid perps.
        Combines allMids and metaAndAssetCtxs to build bid/ask/volume/funding.
        """
        meta, asset_ctxs = self.meta_and_asset_ctxs()

        # Get universe (list of asset names) from meta
        universe: list[dict[str, Any]] = meta.get("universe", [])
        if not universe or not asset_ctxs:
            return []

        result: list[HyperliquidBookTicker] = []

        for i, ctx in enumerate(asset_ctxs):
            if i >= len(universe):
                break
            if not isinstance(ctx, dict):
                continue

            asset_info = universe[i]
            raw_symbol = asset_info.get("name", "") if isinstance(asset_info, dict) else ""
            if not raw_symbol:
                continue

            symbol = _normalize_symbol(raw_symbol)

            # Mid price
            mid_px = _parse_float(ctx.get("midPx"))
            if mid_px <= 0:
                mark_px = _parse_float(ctx.get("markPx"))
                if mark_px <= 0:
                    continue
                mid_px = mark_px

            # Bid/Ask from impactPxs (list of [bid_impact, ask_impact])
            impact_pxs = ctx.get("impactPxs")
            if isinstance(impact_pxs, list) and len(impact_pxs) >= 2:
                bid_price = _parse_float(impact_pxs[0])
                ask_price = _parse_float(impact_pxs[1])
            else:
                # Derive from mid with a small spread (0.01%)
                spread_factor = 0.0001
                bid_price = mid_px * (1 - spread_factor / 2)
                ask_price = mid_px * (1 + spread_factor / 2)

            if bid_price <= 0 or ask_price <= 0:
                continue

            # Volume: dayNtlVlm is 24h notional volume in USD
            day_ntl_vlm = _parse_float(ctx.get("dayNtlVlm"))
            volume_24h_quote = day_ntl_vlm
            volume_24h_base = day_ntl_vlm / mid_px if mid_px > 0 else 0.0

            # Funding rate
            funding_raw = ctx.get("funding")
            funding_rate: float | None = None
            if isinstance(funding_raw, str):
                funding_rate = _parse_float(funding_raw) or None
            elif isinstance(funding_raw, (int, float)):
                funding_rate = float(funding_raw) if funding_raw != 0 else None

            # Open interest as proxy for qty (Hyperliquid doesn't have L1 qty in this endpoint)
            open_interest = _parse_float(ctx.get("openInterest"))

            result.append(HyperliquidBookTicker(
                symbol=symbol,
                bid_price=bid_price,
                bid_qty=open_interest,
                ask_price=ask_price,
                ask_qty=open_interest,
                volume_24h_base=volume_24h_base,
                volume_24h_quote=volume_24h_quote,
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
        Get candlestick data for a symbol.
        Symbol should be the raw Hyperliquid name (e.g. "BTC", not "BTCUSDT").
        Returns list of dicts with: time, open, high, low, close, volume.
        """
        # Strip USDT suffix if provided for convenience
        coin = symbol.upper()
        if coin.endswith("USDT"):
            coin = coin[:-4]
        elif coin.endswith("USD"):
            coin = coin[:-3]

        # Calculate time range
        now_ms = int(time.time() * 1000)
        interval_ms = _interval_to_ms(interval)
        start_time = now_ms - (limit * interval_ms)

        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": now_ms,
            },
        }

        data = self._post(self._info_endpoint, body)
        if not isinstance(data, list):
            return []

        result: list[dict[str, Any]] = []
        for candle in data:
            if not isinstance(candle, dict):
                continue
            result.append({
                "time": candle.get("t", 0),
                "open": _parse_float(candle.get("o")),
                "high": _parse_float(candle.get("h")),
                "low": _parse_float(candle.get("l")),
                "close": _parse_float(candle.get("c")),
                "volume": _parse_float(candle.get("v")),
            })

        return result


def _interval_to_ms(interval: str) -> int:
    """Convert interval string to milliseconds."""
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
        "1w": 604_800_000,
    }
    return mapping.get(interval, 3_600_000)


def hyperliquid_snapshot_rows(
    client: HyperliquidPublicClient | None = None,
) -> list[BookTickerRow]:
    """
    Получить тикеры Hyperliquid и нормализовать в list[BookTickerRow].

    Вызывает book_tickers(), вычисляет mid/spread_abs/spread_bps,
    включает funding_rate.
    """
    if client is None:
        client = HyperliquidPublicClient()

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
