from __future__ import annotations

from typing import Any

from mexc_monitor.metrics import compute_mid_spread
from mexc_monitor.models import BookTickerRow


def parse_float(raw: str | float | int | None) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def futures_ticker_item_to_row(item: dict[str, Any]) -> BookTickerRow | None:
    """
    REST contract/ticker element or futures WS push.tickers element.

    REST: обычно есть bid1/ask1 (лучшие цены).

    WS push.tickers: полей bid1/ask1 часто нет; maxBidPrice/minAskPrice — это **не** лучший
    спред (см. сырые данные: minAskPrice может быть меньше maxBidPrice). В таком случае
    берём fairPrice или lastPrice для bid и ask одинаково: mid ≈ рыночной цене, спред в
    снимке — 0 (реальный L1 только из REST или sub.depth).
    """
    symbol = item.get("symbol")
    if not symbol or not isinstance(symbol, str):
        return None
    bid = parse_float(item.get("bid1"))
    ask = parse_float(item.get("ask1"))
    if (
        bid is not None
        and ask is not None
        and bid > 0
        and ask > 0
        and ask >= bid
    ):
        pass
    else:
        ref = parse_float(item.get("fairPrice"))
        if ref is None or ref <= 0:
            ref = parse_float(item.get("lastPrice"))
        if ref is None or ref <= 0:
            return None
        bid = ref
        ask = ref

    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None

    vol_base = parse_float(item.get("volume24")) or 0.0
    vol_quote = parse_float(item.get("amount24")) or 0.0
    fr = parse_float(item.get("fundingRate"))

    bid_qty = parse_float(item.get("bidQty")) or parse_float(item.get("bidQuantity")) or 0.0
    ask_qty = parse_float(item.get("askQty")) or parse_float(item.get("askQuantity")) or 0.0

    mid, spread_abs, spread_bps = compute_mid_spread(bid, ask)
    return BookTickerRow(
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        mid=mid,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
        volume_24h_base=vol_base,
        volume_24h_quote=vol_quote,
        funding_rate=fr,
        observed_at=None,
    )
