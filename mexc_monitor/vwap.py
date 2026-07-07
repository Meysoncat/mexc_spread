"""Volume-Weighted Average Price (VWAP) execution model.

Computes realistic execution prices for market orders by walking through
L2 orderbook depth levels. Replaces the L1-only ``min(bid_qty, ask_qty)``
estimate with actual depth-weighted pricing and slippage.

Math summary
------------
Given an order of size :math:`Q` on the ask side (market buy), walk levels
:math:`i = 1 \\dots n` (ascending price) until cumulative fill :math:`\\geq Q`::

    q_i = min(level_qty_i, Q_remaining)
    VWAP = Σ(P_i · q_i) / Σ(q_i)
    slippage_bps = (VWAP - P_best) / mid × 10⁴

For a market sell, walk bids descending (highest price first).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

Side = Literal["buy", "sell"]

_EMPTY: VwapResult | None = None


@dataclass(frozen=True)
class VwapResult:
    """Result of a VWAP execution simulation over L2 depth."""

    vwap_price: float
    filled_qty: float
    unfilled_qty: float
    best_price: float
    mid: float
    slippage_bps: float | None
    total_notional: float
    levels_consumed: int
    fully_filled: bool


def _extract(levels: Sequence[dict[str, float]] | None) -> list[tuple[float, float]]:
    """Normalize a side's levels to ``[(price, qty), ...]`` with valid positive prices."""
    if not levels:
        return []
    out: list[tuple[float, float]] = []
    for lv in levels:
        if not isinstance(lv, dict):
            continue
        p = lv.get("price")
        q = lv.get("qty")
        if p is None or q is None:
            continue
        try:
            pf = float(p)
            qf = float(q)
        except (TypeError, ValueError):
            continue
        if pf > 0 and qf > 0:
            out.append((pf, qf))
    return out


def compute_vwap(
    bids: Sequence[dict[str, float]] | None,
    asks: Sequence[dict[str, float]] | None,
    side: Side,
    order_qty: float,
    *,
    mid: float | None = None,
) -> VwapResult | None:
    """Compute VWAP for a market order of *order_qty* base units.

    Parameters
    ----------
    bids, asks
        L2 depth levels as ``[{"price": p, "qty": q, ...}, ...]``.
        Bids should be sorted descending, asks ascending (exchange convention).
    side
        ``"buy"`` walks asks; ``"sell"`` walks bids.
    order_qty
        Order size in **base** currency (e.g. BTC).
    mid
        Midpoint for slippage calculation. If ``None``, derived from best bid/ask.

    Returns
    -------
    VwapResult or None
        ``None`` when no usable depth exists.
    """
    if order_qty <= 0:
        return None

    if side == "buy":
        levels = _extract(asks)
    else:
        levels = _extract(bids)

    if not levels:
        return None

    best_price = levels[0][0]

    remaining = order_qty
    total_notional = 0.0
    total_qty = 0.0
    consumed = 0

    for price, qty in levels:
        if remaining <= 0:
            break
        fill = qty if qty < remaining else remaining
        total_notional += price * fill
        total_qty += fill
        remaining -= fill
        consumed += 1

    if total_qty <= 0:
        return None

    vwap = total_notional / total_qty

    # Derive mid if not provided
    if mid is None or mid <= 0:
        bid_levels = _extract(bids)
        ask_levels = _extract(asks)
        bb = bid_levels[0][0] if bid_levels else 0.0
        ba = ask_levels[0][0] if ask_levels else 0.0
        if bb > 0 and ba > 0:
            mid = (bb + ba) / 2.0
        else:
            mid = vwap

    # Slippage: how much worse than the best price, in bps.
    # For buy: VWAP >= best_ask → slippage >= 0 (cost increases)
    # For sell: VWAP <= best_bid → slippage >= 0 (revenue decreases)
    if mid > 0:
        if side == "buy":
            slippage = (vwap - best_price) / mid * 10_000.0
        else:
            slippage = (best_price - vwap) / mid * 10_000.0
    else:
        slippage = None

    return VwapResult(
        vwap_price=vwap,
        filled_qty=total_qty,
        unfilled_qty=remaining,
        best_price=best_price,
        mid=mid,
        slippage_bps=slippage,
        total_notional=total_notional,
        levels_consumed=consumed,
        fully_filled=remaining <= 0,
    )


def compute_executable_notional(
    bids: Sequence[dict[str, float]] | None,
    asks: Sequence[dict[str, float]] | None,
    side: Side,
    *,
    max_levels: int = 20,
) -> float:
    """Total notional (quote currency) available on one side up to *max_levels*.

    Useful for a quick liquidity check without specifying an order size.
    """
    levels = _extract(asks if side == "buy" else bids)
    total = 0.0
    for price, qty in levels[:max_levels]:
        total += price * qty
    return total


def compute_depth_summary(
    bids: Sequence[dict[str, float]] | None,
    asks: Sequence[dict[str, float]] | None,
    reference_notional: float = 0.0,
    *,
    max_levels: int = 20,
) -> dict[str, float | None]:
    """Compute a summary of L2 depth for snapshot enrichment.

    Returns a dict with keys suitable for extending ``BookTickerRow``::

        {
            "vwap_buy_price": float | None,      # VWAP for buying ref notional
            "vwap_sell_price": float | None,
            "slippage_buy_bps": float | None,
            "slippage_sell_bps": float | None,
            "executable_buy_notional": float,     # total depth on ask side
            "executable_sell_notional": float,    # total depth on bid side
            "depth_levels": int,                  # levels available (min of both sides)
        }
    """
    bid_levels = _extract(bids)
    ask_levels = _extract(asks)

    result: dict[str, float | None] = {
        "vwap_buy_price": None,
        "vwap_sell_price": None,
        "slippage_buy_bps": None,
        "slippage_sell_bps": None,
        "executable_buy_notional": 0.0,
        "executable_sell_notional": 0.0,
        "depth_levels": min(len(bid_levels), len(ask_levels)),
    }

    if not bid_levels or not ask_levels:
        return result

    bb = bid_levels[0][0]
    ba = ask_levels[0][0]
    mid = (bb + ba) / 2.0 if bb > 0 and ba > 0 else None

    # Total available depth
    result["executable_buy_notional"] = sum(
        p * q for p, q in ask_levels[:max_levels]
    )
    result["executable_sell_notional"] = sum(
        p * q for p, q in bid_levels[:max_levels]
    )

    # VWAP for reference notional order
    if reference_notional > 0 and mid is not None and mid > 0:
        order_qty = reference_notional / mid

        buy_vwap = compute_vwap(bids, asks, "buy", order_qty, mid=mid)
        if buy_vwap is not None:
            result["vwap_buy_price"] = buy_vwap.vwap_price
            result["slippage_buy_bps"] = buy_vwap.slippage_bps

        sell_vwap = compute_vwap(bids, asks, "sell", order_qty, mid=mid)
        if sell_vwap is not None:
            result["vwap_sell_price"] = sell_vwap.vwap_price
            result["slippage_sell_bps"] = sell_vwap.slippage_bps

    return result
