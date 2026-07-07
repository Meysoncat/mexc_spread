from __future__ import annotations


def round_trip_taker_fee_bps(one_way_taker_bps: float) -> float:
    """Два исхода по taker (купля по ask + продажа по bid) в bps."""
    return max(0.0, 2.0 * max(0.0, float(one_way_taker_bps)))


def net_spread_after_fees_bps(
    gross_spread_bps: float | None,
    fee_round_trip_bps: float,
) -> float | None:
    if gross_spread_bps is None:
        return None
    return gross_spread_bps - fee_round_trip_bps


def compute_mid_spread(bid: float, ask: float) -> tuple[float, float, float | None]:
    """
    Returns (mid, spread_abs, spread_bps).

    spread_bps = 10_000 × (ask − bid) / mid — **full** bid-ask spread in bps
    (NOT half-spread; half-spread = spread_bps / 2).
    """
    spread_abs = ask - bid
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return mid, spread_abs, None
    spread_bps = 10_000.0 * spread_abs / mid
    return mid, spread_abs, spread_bps


def effective_spread_bps(trade_price: float, mid: float) -> float | None:
    """Effective spread for a single executed trade.

    .. math::
        S_{\\text{eff}} = \\frac{2 |P_{\\text{trade}} - M|}{M} \\times 10^4

    For a market buy at ask: S_eff = quoted spread.
    For a buy with slippage: S_eff > quoted spread.
    """
    if mid <= 0:
        return None
    return 10_000.0 * 2.0 * abs(trade_price - mid) / mid


def realized_spread_bps(
    trade_price: float,
    mid_at_k: float,
    mid: float,
) -> float | None:
    """Realized spread — profit of a market maker k seconds after the trade.

    .. math::
        S_{\\text{realized}} = \\frac{2 (P_{\\text{trade}} - M_{t+k})}{M} \\times 10^4

    If S_realized < S_effective, the market maker lost money to adverse
    selection (the price moved against them after the fill).
    """
    if mid <= 0:
        return None
    return 10_000.0 * 2.0 * (trade_price - mid_at_k) / mid


def adverse_selection_bps(
    trade_price: float,
    mid_at_trade: float,
    mid_at_k: float,
) -> float | None:
    """Adverse selection component = effective spread − realized spread.

    .. math::
        \\alpha = S_{\\text{eff}} - S_{\\text{realized}} =
        \\frac{2 (M_{t+k} - M_t)}{M_t} \\times 10^4

    Positive α means the price moved against the filled order (adverse
    selection). Negative α means favorable selection.
    """
    if mid_at_trade <= 0:
        return None
    return 10_000.0 * 2.0 * (mid_at_k - mid_at_trade) / mid_at_trade
