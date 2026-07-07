from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import TYPE_CHECKING

from mexc_monitor.orderbook import fetch_futures_orderbook

if TYPE_CHECKING:
    import httpx

    from mexc_monitor.config import Settings
    from mexc_monitor.models import BookTickerRow


def enrich_futures_bid_ask_qty_from_rest_depth(
    rows: list[BookTickerRow],
    settings: Settings,
    client: httpx.Client,
) -> list[BookTickerRow]:
    """
    Дополняет bid_qty / ask_qty с лучшего уровня contract/depth (тикер часто без L1 qty).
    """
    if not settings.futures_rest_l1_qty_enrich or not rows:
        return rows

    cap = int(settings.futures_rest_l1_qty_max_symbols)
    if cap <= 0:
        return rows

    limit = max(5, int(settings.futures_rest_l1_qty_depth_limit))
    slice_rows = rows[:cap]

    uniq: list[str] = []
    seen: set[str] = set()
    for r in slice_rows:
        if r.symbol not in seen:
            seen.add(r.symbol)
            uniq.append(r.symbol)

    if not uniq:
        return rows

    workers = max(
        1,
        min(int(settings.futures_rest_l1_qty_max_workers), len(uniq)),
    )
    qty_by_symbol: dict[str, tuple[float, float] | None] = {}

    def _one(sym: str) -> tuple[str, tuple[float, float] | None]:
        try:
            ob = fetch_futures_orderbook(
                sym,
                limit=limit,
                settings=settings,
                client=client,
            )
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            bq = float(bids[0]["qty"]) if bids else 0.0
            aq = float(asks[0]["qty"]) if asks else 0.0
            return sym, (bq, aq)
        except Exception:
            return sym, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, s) for s in uniq]
        for fut in as_completed(futs):
            sym, pair = fut.result()
            qty_by_symbol[sym] = pair

    out: list[BookTickerRow] = []
    for r in rows:
        pair = qty_by_symbol.get(r.symbol)
        if pair is None:
            out.append(r)
        else:
            bq, aq = pair
            out.append(replace(r, bid_qty=bq, ask_qty=aq))
    return out
