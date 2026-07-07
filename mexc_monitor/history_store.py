from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from mexc_monitor.config import Settings
from mexc_monitor.orm import SpreadSnapshot, create_schema, get_engine


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_history_db_path(settings: Settings) -> Path:
    p = Path(settings.history_db_path)
    if p.is_absolute():
        return p
    return repo_root() / p


def init_db(path: Path) -> None:
    create_schema(path)


def _float_or_none(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def append_snapshot(path: Path, market: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    init_db(path)
    cols = [
        "observed_at",
        "symbol",
        "bid",
        "ask",
        "bid_qty",
        "ask_qty",
        "mid",
        "spread_abs",
        "spread_bps",
        "volume_24h_base",
        "volume_24h_quote",
        "funding_rate",
        "fee_round_trip_bps",
        "net_spread_bps",
        "l1_max_executable_base",
        "l1_max_notional_quote",
    ]
    for c in cols:
        if c not in df.columns and c != "observed_at":
            df = df.copy()
            df[c] = None
    batch: list[SpreadSnapshot] = []
    for _, row in df.iterrows():
        oa = row.get("observed_at")
        if oa is None or (isinstance(oa, float) and pd.isna(oa)):
            continue
        spread_bps = row.get("spread_bps")
        if spread_bps is not None and isinstance(spread_bps, float) and pd.isna(spread_bps):
            spread_bps = None
        fr = row.get("funding_rate")
        if fr is not None and isinstance(fr, float) and pd.isna(fr):
            fr = None
        net_bps = row.get("net_spread_bps")
        if net_bps is not None and isinstance(net_bps, float) and pd.isna(net_bps):
            net_bps = None
        batch.append(
            SpreadSnapshot(
                observed_at=str(oa),
                market=market,
                symbol=str(row["symbol"]),
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                bid_qty=float(row["bid_qty"]),
                ask_qty=float(row["ask_qty"]),
                mid=float(row["mid"]),
                spread_abs=float(row["spread_abs"]),
                spread_bps=float(spread_bps) if spread_bps is not None else None,
                volume_24h_base=float(row["volume_24h_base"]),
                volume_24h_quote=float(row["volume_24h_quote"]),
                funding_rate=float(fr) if fr is not None else None,
                fee_round_trip_bps=_float_or_none(row.get("fee_round_trip_bps")),
                net_spread_bps=float(net_bps) if net_bps is not None else None,
                l1_max_executable_base=_float_or_none(row.get("l1_max_executable_base")),
                l1_max_notional_quote=_float_or_none(row.get("l1_max_notional_quote")),
            ),
        )
    if not batch:
        return 0
    engine = get_engine(path)
    with Session(engine) as session:
        session.add_all(batch)
        session.commit()
    return len(batch)


def _snapshot_to_dict(r: SpreadSnapshot) -> dict:
    return {
        "observed_at": r.observed_at,
        "market": r.market,
        "symbol": r.symbol,
        "bid": r.bid,
        "ask": r.ask,
        "bid_qty": r.bid_qty,
        "ask_qty": r.ask_qty,
        "mid": r.mid,
        "spread_abs": r.spread_abs,
        "spread_bps": r.spread_bps,
        "volume_24h_base": r.volume_24h_base,
        "volume_24h_quote": r.volume_24h_quote,
        "funding_rate": r.funding_rate,
        "fee_round_trip_bps": r.fee_round_trip_bps,
        "net_spread_bps": r.net_spread_bps,
        "l1_max_executable_base": r.l1_max_executable_base,
        "l1_max_notional_quote": r.l1_max_notional_quote,
    }


def query_recent(
    path: Path,
    *,
    market: str,
    symbol: str | None,
    since_iso: str | None,
    limit: int,
) -> list[dict]:
    if not path.is_file():
        return []
    init_db(path)
    sym_norm: str | None = None
    if symbol:
        sym_norm = symbol.strip().upper()
        if market == "futures" and "_" in sym_norm:
            sym_norm = "_".join(p for p in sym_norm.split("_") if p)

    engine = get_engine(path)
    with Session(engine) as session:
        stmt = select(SpreadSnapshot).where(SpreadSnapshot.market == market)
        if sym_norm is not None:
            stmt = stmt.where(SpreadSnapshot.symbol == sym_norm)
        if since_iso:
            stmt = stmt.where(SpreadSnapshot.observed_at >= since_iso.strip())
        stmt = stmt.order_by(SpreadSnapshot.observed_at.desc()).limit(limit)
        rows = session.scalars(stmt).all()
        return [_snapshot_to_dict(r) for r in rows]
