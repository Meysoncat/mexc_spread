from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

from mexc_monitor.client import fetch_futures_snapshot_rows, fetch_merged_snapshot_rows
from mexc_monitor.config import Settings
from mexc_monitor.execution import enrich_row_execution
from mexc_monitor.models import BookTickerRow, CrossSpreadRow
from mexc_monitor.symbol_filter import filter_rows_by_universe

_CROSS_COLS = [
    "symbol_spot",
    "symbol_futures",
    "spot_bid",
    "spot_ask",
    "spot_mid",
    "spot_spread_bps",
    "fut_bid",
    "fut_ask",
    "fut_mid",
    "fut_spread_bps",
    "basis_mid_abs",
    "basis_mid_bps",
    "funding_rate",
    "volume_24h_base_spot",
    "volume_24h_quote_spot",
    "volume_24h_base_fut",
    "volume_24h_quote_fut",
    "observed_at",
]


def spot_to_futures_symbol(spot_symbol: str) -> str | None:
    """BTCUSDT → BTC_USDT. Только *USDT спот-пары (как на MEXC)."""
    s = spot_symbol.strip().upper()
    if not s.endswith("USDT"):
        return None
    base = s[:-4]
    if not base:
        return None
    return f"{base}_USDT"


def merge_cross_rows(
    spot_rows: list[BookTickerRow],
    fut_rows: list[BookTickerRow],
) -> list[CrossSpreadRow]:
    fut_map: dict[str, BookTickerRow] = {r.symbol: r for r in fut_rows}
    out: list[CrossSpreadRow] = []
    for s in spot_rows:
        fsym = spot_to_futures_symbol(s.symbol)
        if fsym is None or fsym not in fut_map:
            continue
        f = fut_map[fsym]
        basis_abs = f.mid - s.mid
        basis_bps = (10_000.0 * basis_abs / s.mid) if s.mid > 0 else None
        oa = s.observed_at if s.observed_at == f.observed_at else s.observed_at
        out.append(
            CrossSpreadRow(
                symbol_spot=s.symbol,
                symbol_futures=f.symbol,
                spot_bid=s.bid,
                spot_ask=s.ask,
                spot_mid=s.mid,
                spot_spread_bps=s.spread_bps,
                fut_bid=f.bid,
                fut_ask=f.ask,
                fut_mid=f.mid,
                fut_spread_bps=f.spread_bps,
                basis_mid_abs=basis_abs,
                basis_mid_bps=basis_bps,
                funding_rate=f.funding_rate,
                volume_24h_base_spot=s.volume_24h_base,
                volume_24h_quote_spot=s.volume_24h_quote,
                volume_24h_base_fut=f.volume_24h_base,
                volume_24h_quote_fut=f.volume_24h_quote,
                observed_at=oa,
            ),
        )
    return out


def cross_rows_to_dataframe(rows: list[CrossSpreadRow]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_CROSS_COLS)
    records = [r.__dict__ for r in rows]
    return pd.DataFrame.from_records(records)


def load_cross_snapshot(settings: Settings) -> pd.DataFrame:
    """Два снимка (спот + фьючерсы), сопоставление по BTCUSDT ↔ BTC_USDT.

    Спот и фьючерсы — независимые сетевые запросы, поэтому забираем их параллельно.
    """

    def _load_spot() -> list[BookTickerRow]:
        return filter_rows_by_universe(
            fetch_merged_snapshot_rows(settings), "spot", settings
        )

    def _load_fut() -> list[BookTickerRow]:
        return filter_rows_by_universe(
            fetch_futures_snapshot_rows(settings), "futures", settings
        )

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        spot_future = pool.submit(_load_spot)
        fut_future = pool.submit(_load_fut)
        spot_rows = spot_future.result()
        fut_rows = fut_future.result()
    ts = datetime.now(timezone.utc).isoformat()
    spot_rows = [
        enrich_row_execution(replace(r, observed_at=ts), "spot", settings) for r in spot_rows
    ]
    fut_rows = [
        enrich_row_execution(replace(r, observed_at=ts), "futures", settings)
        for r in fut_rows
    ]
    merged = merge_cross_rows(spot_rows, fut_rows)
    logger.info(
        "cross snapshot: spot=%s fut=%s merged_pairs=%s",
        len(spot_rows),
        len(fut_rows),
        len(merged),
    )
    return cross_rows_to_dataframe(merged)
