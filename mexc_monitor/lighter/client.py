"""
Lighter DEX Public API Client — market data (без аутентификации).

Lighter — DEX перпетуальных фьючерсов на zkSync.
Base URL: https://mainnet.zklighter.elliot.ai
Endpoints:
  GET /api/v1/orderBooks — сводка по рынкам (best bid/ask, volume)
  GET /api/v1/orderBookDetails — метаданные рынков (decimals, min amounts)
  GET /api/v1/orderBookOrders — ордера в стакане (bid/ask)
  GET /api/v1/funding-rates — текущие funding rates
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://mainnet.zklighter.elliot.ai"
DEFAULT_TIMEOUT_SEC = 15.0

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "external_apis.json"


def _load_lighter_config() -> dict[str, Any]:
    """Загрузить секцию 'lighter' из config/external_apis.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("lighter", {})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Cannot read lighter config from %s: %s", _CONFIG_PATH, e)
        return {}


class LighterApiError(RuntimeError):
    """Ошибка при обращении к Lighter API."""
    pass


@dataclass(frozen=True)
class LighterMarketInfo:
    """Метаданные рынка из orderBookDetails."""
    market_id: int
    symbol: str           # e.g. "ETH-PERP"
    base_asset: str       # e.g. "ETH"
    quote_asset: str      # e.g. "USD"
    price_decimals: int
    size_decimals: int
    min_base_amount: float
    min_quote_amount: float
    taker_fee_pct: float
    maker_fee_pct: float
    last_trade_price: float = 0.0
    volume_24h: float = 0.0  # daily_quote_token_volume (USD)


@dataclass(frozen=True)
class LighterOrderbookSummary:
    """Сводка по рынку из orderBooks."""
    market_id: int
    best_bid: float
    best_ask: float
    best_bid_qty: float
    best_ask_qty: float
    volume_24h: float
    last_price: float


@dataclass(frozen=True)
class LighterFundingRate:
    """Текущий funding rate."""
    market_id: int
    funding_rate: float
    next_funding_time: int


def _parse_float(v: Any) -> float:
    """Безопасный парсинг float."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_int(v: Any) -> int:
    """Безопасный парсинг int."""
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


class LighterPublicClient:
    """Публичный клиент Lighter DEX (без аутентификации)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = _load_lighter_config()
        self._base_url = (base_url or cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout_sec if timeout_sec is not None else cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """HTTP GET с обработкой ошибок."""
        url = f"{self._base_url}{path}"
        try:
            r = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise LighterApiError(f"HTTP error: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise LighterApiError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except Exception as e:
            raise LighterApiError(f"Invalid JSON: {e}") from e
        return data

    def orderbooks(self, filter: str = "perp") -> list[LighterOrderbookSummary]:
        """
        GET /api/v1/orderBooks — сводка по рынкам (best bid/ask, volume).

        Args:
            filter: тип рынка ("perp" для перпетуальных фьючерсов)

        Returns:
            Список LighterOrderbookSummary
        """
        params: dict[str, Any] = {}
        if filter:
            params["filter"] = filter
        data = self._get("/api/v1/orderBooks", params=params or None)

        if not isinstance(data, list):
            # API может вернуть объект с ключом "orderBooks"
            if isinstance(data, dict):
                data = data.get("orderBooks", data.get("order_books", []))
            if not isinstance(data, list):
                return []

        result: list[LighterOrderbookSummary] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            market_id = _parse_int(item.get("market_id", item.get("marketId")))
            best_bid = _parse_float(item.get("best_bid", item.get("bestBid", item.get("best_bid_price"))))
            best_ask = _parse_float(item.get("best_ask", item.get("bestAsk", item.get("best_ask_price"))))
            best_bid_qty = _parse_float(item.get("best_bid_qty", item.get("bestBidQty", item.get("best_bid_size"))))
            best_ask_qty = _parse_float(item.get("best_ask_qty", item.get("bestAskQty", item.get("best_ask_size"))))
            volume_24h = _parse_float(item.get("volume_24h", item.get("volume24h", item.get("volume"))))
            last_price = _parse_float(item.get("last_price", item.get("lastPrice", item.get("last_trade_price"))))

            result.append(LighterOrderbookSummary(
                market_id=market_id,
                best_bid=best_bid,
                best_ask=best_ask,
                best_bid_qty=best_bid_qty,
                best_ask_qty=best_ask_qty,
                volume_24h=volume_24h,
                last_price=last_price,
            ))
        return result

    def orderbook_details(self, filter: str = "perp") -> list[LighterMarketInfo]:
        """
        GET /api/v1/orderBookDetails — метаданные рынков.

        Args:
            filter: тип рынка ("perp" для перпетуальных фьючерсов)

        Returns:
            Список LighterMarketInfo
        """
        params: dict[str, Any] = {}
        if filter:
            params["filter"] = filter
        data = self._get("/api/v1/orderBookDetails", params=params or None)

        if not isinstance(data, list):
            if isinstance(data, dict):
                data = data.get("orderBookDetails", data.get("order_book_details", []))
            if not isinstance(data, list):
                return []

        result: list[LighterMarketInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            market_id = _parse_int(item.get("market_id", item.get("marketId")))
            symbol = item.get("symbol", item.get("name", ""))
            base_asset = item.get("base_asset", item.get("baseAsset", ""))
            quote_asset = item.get("quote_asset", item.get("quoteAsset", "USD"))
            price_decimals = _parse_int(
                item.get("supported_price_decimals",
                         item.get("price_decimals",
                                  item.get("priceDecimals", 0)))
            )
            size_decimals = _parse_int(
                item.get("supported_size_decimals",
                         item.get("size_decimals",
                                  item.get("sizeDecimals", 0)))
            )
            min_base_amount = _parse_float(
                item.get("min_base_amount", item.get("minBaseAmount", 0))
            )
            min_quote_amount = _parse_float(
                item.get("min_quote_amount", item.get("minQuoteAmount", 0))
            )
            taker_fee_pct = _parse_float(
                item.get("taker_fee_pct", item.get("takerFeePct", item.get("taker_fee", 0)))
            )
            maker_fee_pct = _parse_float(
                item.get("maker_fee_pct", item.get("makerFeePct", item.get("maker_fee", 0)))
            )

            result.append(LighterMarketInfo(
                market_id=market_id,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                price_decimals=price_decimals,
                size_decimals=size_decimals,
                min_base_amount=min_base_amount,
                min_quote_amount=min_quote_amount,
                taker_fee_pct=taker_fee_pct,
                maker_fee_pct=maker_fee_pct,
                last_trade_price=_parse_float(
                    item.get("last_trade_price", item.get("lastTradePrice", 0))
                ),
                volume_24h=_parse_float(
                    item.get("daily_quote_token_volume",
                             item.get("dailyQuoteTokenVolume",
                                      item.get("volume_24h", 0)))
                ),
            ))
        return result

    def orderbook_orders(
        self,
        market_id: int,
        limit: int = 5,
    ) -> dict[str, Any]:
        """
        GET /api/v1/orderBookOrders — ордера в стакане (bid/ask).

        Args:
            market_id: ID рынка
            limit: количество уровней (по умолчанию 5)

        Returns:
            Словарь с ключами "bids" и "asks" (списки ордеров)
        """
        params: dict[str, Any] = {
            "market_id": market_id,
            "limit": limit,
        }
        data = self._get("/api/v1/orderBookOrders", params=params)
        if not isinstance(data, dict):
            return {"bids": [], "asks": []}
        return data

    def funding_rates(self) -> list[LighterFundingRate]:
        """
        GET /api/v1/funding-rates — текущие funding rates.

        Returns:
            Список LighterFundingRate
        """
        data = self._get("/api/v1/funding-rates")

        if not isinstance(data, list):
            if isinstance(data, dict):
                data = data.get("funding_rates", data.get("fundingRates", []))
            if not isinstance(data, list):
                return []

        result: list[LighterFundingRate] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            market_id = _parse_int(item.get("market_id", item.get("marketId")))
            funding_rate = _parse_float(
                item.get("funding_rate", item.get("fundingRate", item.get("rate")))
            )
            next_funding_time = _parse_int(
                item.get("next_funding_time", item.get("nextFundingTime", item.get("next_funding_ts")))
            )

            result.append(LighterFundingRate(
                market_id=market_id,
                funding_rate=funding_rate,
                next_funding_time=next_funding_time,
            ))
        return result

    def candles(
        self,
        market_id: int,
        *,
        resolution: str = "60",
        limit: int = 96,
    ) -> list[dict[str, Any]]:
        """
        GET /api/v1/candles — свечи для рынка.

        Args:
            market_id: ID рынка
            resolution: Интервал в минутах ("1", "5", "15", "60", "240", "1440")
            limit: Количество свечей

        Returns:
            Список словарей с OHLCV данными в формате lightweight-charts.
        """
        params: dict[str, Any] = {
            "market_id": market_id,
            "resolution": resolution,
            "limit": limit,
        }
        data = self._get("/api/v1/candles", params=params)

        if not isinstance(data, dict):
            return []

        candles_raw = data.get("candles", [])
        if not isinstance(candles_raw, list):
            return []

        result: list[dict[str, Any]] = []
        for item in candles_raw:
            if not isinstance(item, dict):
                continue
            try:
                t = _parse_int(item.get("time", item.get("timestamp", item.get("t", 0))))
                # If time is in milliseconds, convert to seconds
                if t > 1_000_000_000_000:
                    t = t // 1000
                result.append({
                    "time": t,
                    "open": _parse_float(item.get("open", item.get("o", 0))),
                    "high": _parse_float(item.get("high", item.get("h", 0))),
                    "low": _parse_float(item.get("low", item.get("l", 0))),
                    "close": _parse_float(item.get("close", item.get("c", 0))),
                    "volume": _parse_float(item.get("volume", item.get("v", 0))),
                })
            except (TypeError, ValueError):
                continue

        # Sort by time ascending
        result.sort(key=lambda x: x["time"])
        return result


# ---------------------------------------------------------------------------
# Normalization: Lighter → BookTickerRow
# ---------------------------------------------------------------------------

_PERP_SUFFIX_RE = re.compile(r"[-_]PERP$", re.IGNORECASE)


def _normalize_symbol(symbol: str) -> str:
    """
    Normalize Lighter symbol to unified format.

    Examples:
        "ETH-PERP" → "ETHUSDT"
        "BTC_PERP" → "BTCUSDT"
        "SOL-PERP" → "SOLUSDT"
        "ETHUSDT"  → "ETHUSDT" (already normalized)
    """
    # Remove -PERP / _PERP suffix
    s = _PERP_SUFFIX_RE.sub("", symbol.strip())
    # Remove remaining dashes/underscores
    s = s.replace("-", "").replace("_", "")
    # Append USDT if not already present
    if not s.upper().endswith("USDT"):
        s = s + "USDT"
    return s.upper()


def _round_price(price: float, decimals: int) -> float:
    """Round price to the supported number of decimals."""
    if decimals <= 0:
        return price
    return round(price, decimals)


def lighter_snapshot_rows(
    client: LighterPublicClient | None = None,
    *,
    min_volume_quote: float = 1000.0,
    max_markets: int = 50,
    max_workers: int = 10,
) -> list[BookTickerRow]:
    """
    Fetch Lighter orderbook data and normalize into BookTickerRow list.

    Strategy:
    1. Fetch orderBookDetails to get market metadata (symbol, decimals, volume, last_price)
    2. Filter to active markets with sufficient volume
    3. Fetch orderBookOrders (limit=1) for each market concurrently to get best bid/ask
    4. Normalize into BookTickerRow

    Args:
        client: LighterPublicClient instance. If None, creates a new one.
        min_volume_quote: Minimum 24h quote volume to include a market (USD).
        max_markets: Maximum number of markets to fetch orderbooks for.
        max_workers: Number of concurrent threads for orderbook fetching.

    Returns:
        List of BookTickerRow compatible with the existing pipeline.
    """
    import concurrent.futures

    if client is None:
        client = LighterPublicClient()

    details = client.orderbook_details(filter="perp")

    # Filter active markets with sufficient volume
    active_details = [
        d for d in details
        if d.volume_24h >= min_volume_quote
    ]
    # Sort by volume descending, take top N
    active_details.sort(key=lambda d: d.volume_24h, reverse=True)
    active_details = active_details[:max_markets]

    if not active_details:
        return []

    # Fetch funding rates (best-effort)
    funding_map: dict[int, float] = {}
    try:
        funding_rates = client.funding_rates()
        funding_map = {fr.market_id: fr.funding_rate for fr in funding_rates}
    except LighterApiError:
        logger.warning("Failed to fetch Lighter funding rates, continuing without them")

    # Fetch orderbook orders concurrently for best bid/ask
    def _fetch_orderbook(market_id: int) -> tuple[int, dict[str, Any]]:
        try:
            data = client.orderbook_orders(market_id=market_id, limit=1)
            return market_id, data
        except LighterApiError as e:
            logger.debug("Failed to fetch orderbook for market_id=%d: %s", market_id, e)
            return market_id, {"bids": [], "asks": []}

    orderbook_data: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_orderbook, d.market_id): d
            for d in active_details
        }
        for future in concurrent.futures.as_completed(futures):
            market_id, data = future.result()
            orderbook_data[market_id] = data

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows: list[BookTickerRow] = []
    for info in active_details:
        ob_data = orderbook_data.get(info.market_id, {})
        bids = ob_data.get("bids", [])
        asks = ob_data.get("asks", [])

        if not bids or not asks:
            continue

        # Extract best bid/ask from orderbook orders
        best_bid_order = bids[0] if isinstance(bids[0], dict) else {}
        best_ask_order = asks[0] if isinstance(asks[0], dict) else {}

        bid = _parse_float(best_bid_order.get("price", 0))
        ask = _parse_float(best_ask_order.get("price", 0))
        bid_qty = _parse_float(best_bid_order.get("remaining_base_amount", 0))
        ask_qty = _parse_float(best_ask_order.get("remaining_base_amount", 0))

        # Skip invalid entries
        if bid <= 0 or ask <= 0:
            continue

        # Normalize symbol
        symbol = _normalize_symbol(info.symbol)

        # Round prices
        price_decimals = info.price_decimals
        bid = _round_price(bid, price_decimals)
        ask = _round_price(ask, price_decimals)

        if bid <= 0 or ask <= 0:
            continue

        # Compute spread metrics
        mid = (bid + ask) / 2.0
        spread_abs = ask - bid
        spread_bps: float | None = None
        if mid > 0:
            spread_bps = 10000.0 * spread_abs / mid

        # Volume from orderBookDetails
        volume_24h_quote = info.volume_24h
        volume_24h_base = volume_24h_quote / mid if mid > 0 else 0.0

        # Funding rate (if available)
        funding_rate = funding_map.get(info.market_id)

        rows.append(BookTickerRow(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            mid=mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            volume_24h_base=volume_24h_base,
            volume_24h_quote=volume_24h_quote,
            funding_rate=funding_rate,
            observed_at=now_iso,
        ))

    return rows
