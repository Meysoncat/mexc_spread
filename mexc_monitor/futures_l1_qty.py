from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import TYPE_CHECKING

from mexc_monitor.orderbook import fetch_futures_orderbook

if TYPE_CHECKING:
    import httpx

    from mexc_monitor.config import Settings
    from mexc_monitor.models import BookTickerRow


# symbol -> (monotonic, (bid_qty, ask_qty)). Объёмы L1 меняются быстро, но для
# скринера точность «на 30 секунд» достаточна, а полный обход стаканов на
# каждой пересборке снимка (~раз в 3 с) упирался в лимиты MEXC.
_cache: dict[str, tuple[float, tuple[float, float]]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SEC = 30.0
_REFRESH_PER_CALL = 15


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
    # Обновляем только самые устаревшие символы из топа по объёму, остальное — из кэша
    top = sorted(rows, key=lambda r: -(r.volume_24h_quote or 0.0))[:cap]
    now = time.monotonic()
    with _cache_lock:
        ages = {r.symbol: now - _cache[r.symbol][0] if r.symbol in _cache else float("inf") for r in top}
    stale = sorted((s for s, age in ages.items() if age > _CACHE_TTL_SEC), key=lambda s: -ages[s])
    uniq = stale[:_REFRESH_PER_CALL]

    if not uniq:
        return _apply_cached(rows)

    workers = max(
        1,
        min(int(settings.futures_rest_l1_qty_max_workers), len(uniq)),
    )

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
            if pair is not None:
                with _cache_lock:
                    _cache[sym] = (time.monotonic(), pair)
    return _apply_cached(rows)


def _apply_cached(rows: list[BookTickerRow]) -> list[BookTickerRow]:
    now = time.monotonic()
    out: list[BookTickerRow] = []
    with _cache_lock:
        for r in rows:
            hit = _cache.get(r.symbol)
            if hit is None or now - hit[0] > 3 * _CACHE_TTL_SEC:
                out.append(r)
            else:
                bq, aq = hit[1]
                out.append(replace(r, bid_qty=bq, ask_qty=aq))
    return out
