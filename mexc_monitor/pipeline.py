from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from mexc_monitor.client import (
    MexcApiError,
    fetch_futures_snapshot_rows,
    fetch_merged_snapshot_rows,
)
from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.cross_market import load_cross_snapshot
from mexc_monitor.execution import enrich_row_execution
from mexc_monitor.models import BookTickerRow
from mexc_monitor.symbol_filter import filter_rows_by_universe

MarketId = Literal["spot", "futures", "cross"]

_EMPTY_COLS = [
    "symbol",
    "bid",
    "ask",
    "spread_abs",
    "spread_bps",
    "mid",
    "bid_qty",
    "ask_qty",
    "volume_24h_base",
    "volume_24h_quote",
    "funding_rate",
    "observed_at",
    "fee_round_trip_bps",
    "net_spread_bps",
    "l1_max_executable_base",
    "l1_max_notional_quote",
    "reference_quote_notional",
    "l1_covers_reference_notional",
]


def rows_to_dataframe(rows: list[BookTickerRow]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_EMPTY_COLS)
    records = [r.__dict__ for r in rows]
    df = pd.DataFrame.from_records(records)
    return df


def load_snapshot(
    market: MarketId = "spot",
    settings: Settings | None = None,
) -> pd.DataFrame:
    cfg = settings if settings is not None else DEFAULT_SETTINGS
    if market == "cross":
        return load_cross_snapshot(cfg)
    if market == "futures":
        rows = fetch_futures_snapshot_rows(cfg)
    else:
        rows = fetch_merged_snapshot_rows(cfg)
    rows = filter_rows_by_universe(rows, market, cfg)
    ts = datetime.now(timezone.utc).isoformat()
    rows = [
        enrich_row_execution(replace(r, observed_at=ts), market, cfg) for r in rows
    ]
    return rows_to_dataframe(rows)


def safe_load_snapshot(
    market: MarketId = "spot",
    settings: Settings | None = None,
) -> tuple[pd.DataFrame | None, str | None]:
    try:
        return load_snapshot(market, settings), None
    except MexcApiError as e:
        return None, str(e)
    except Exception as e:  # httpx errors, etc.
        return None, f"{type(e).__name__}: {e}"
