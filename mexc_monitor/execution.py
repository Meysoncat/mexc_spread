from __future__ import annotations

from dataclasses import replace
from typing import Literal, Sequence

from mexc_monitor.config import Settings
from mexc_monitor.metrics import net_spread_after_fees_bps, round_trip_taker_fee_bps
from mexc_monitor.models import BookTickerRow
from mexc_monitor.vwap import compute_depth_summary

MarketId = Literal["spot", "futures"]


def enrich_row_execution(row: BookTickerRow, market: MarketId, settings: Settings) -> BookTickerRow:
    """
    Дополняет строку снимка оценками исполнения (консервативно, только L1).

    Комиссии: предполагается taker на входе и выходе → round-trip = 2 × one-way (bps).
    Объём: на лучшем уровне одновременно «купить по ask и продать по bid» в базе
    ограничен min(bid_qty, ask_qty); без стакана глубже — это верхняя граница, не факт исполнения.
    """
    fee_ow = (
        settings.exec_spot_taker_fee_bps
        if market == "spot"
        else settings.exec_futures_taker_fee_bps
    )
    fee_ow = max(0.0, float(fee_ow))
    fee_rt = round_trip_taker_fee_bps(fee_ow)
    net_bps = net_spread_after_fees_bps(row.spread_bps, fee_rt)

    l1_base = 0.0
    if row.bid_qty > 0 and row.ask_qty > 0:
        l1_base = min(row.bid_qty, row.ask_qty)
    l1_notional = l1_base * row.mid if row.mid > 0 else 0.0

    ref = max(0.0, float(settings.exec_reference_quote_notional))
    covers: bool | None = None
    ref_out: float | None = None
    if ref > 0:
        ref_out = ref
        covers = l1_notional >= ref

    return replace(
        row,
        fee_round_trip_bps=fee_rt,
        net_spread_bps=net_bps,
        l1_max_executable_base=l1_base,
        l1_max_notional_quote=l1_notional,
        reference_quote_notional=ref_out,
        l1_covers_reference_notional=covers,
    )


def enrich_row_vwap(
    row: BookTickerRow,
    bids: Sequence[dict[str, float]] | None,
    asks: Sequence[dict[str, float]] | None,
    settings: Settings,
) -> BookTickerRow:
    """Enrich a row with L2 depth-based VWAP execution estimates.

    Called **after** :func:`enrich_row_execution` when full orderbook depth is
    available (e.g. from ``fetch_orderbook_depth``). Fills the ``vwap_*`` and
    ``slippage_*`` fields on :class:`BookTickerRow`.

    If *bids* or *asks* are empty/None, returns the row unchanged.
    """
    if not bids and not asks:
        return row

    ref = max(0.0, float(settings.exec_reference_quote_notional))
    summary = compute_depth_summary(
        bids, asks, reference_notional=ref, max_levels=20,
    )

    return replace(
        row,
        vwap_buy_price=summary["vwap_buy_price"],
        vwap_sell_price=summary["vwap_sell_price"],
        slippage_buy_bps=summary["slippage_buy_bps"],
        slippage_sell_bps=summary["slippage_sell_bps"],
        executable_buy_notional=summary["executable_buy_notional"],
        executable_sell_notional=summary["executable_sell_notional"],
        depth_levels=int(summary["depth_levels"]),
    )
