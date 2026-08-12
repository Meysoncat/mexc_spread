"""SQLite-хранилище событий «монета найдена скринером».

Запись — в момент входа символа в шорт-лист (переход нет→да); при выходе
обновляется ``exited_at``/``duration_sec``. ``score_breakdown`` (JSON) хранит
причину попадания (EV, liquidity, …). Таблица создаётся автоматически через
ORM ``create_schema``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from mexc_monitor.orm import ScreenerOpportunityEvent, create_schema, get_engine


def _ensure(path: Path) -> None:
    create_schema(path)


def record_enter(
    path: Path,
    *,
    symbol: str,
    found_at_iso: str,
    found_at_ms: int,
    opp: dict,
) -> int | None:
    """Insert a discovery event; return its row id (or None if disabled/no path)."""
    if path is None:
        return None
    _ensure(path)
    bd = opp.get("score_breakdown")
    breakdown_json = json.dumps(bd) if isinstance(bd, dict) else None
    ev = ScreenerOpportunityEvent(
        symbol=symbol.upper(),
        found_at=found_at_iso,
        found_at_ms=found_at_ms,
        net_spread_bps=_f(opp.get("net_spread_bps")),
        spread_bps=_f(opp.get("spread_bps")),
        l1_notional=_f(opp.get("l1_notional")),
        volume_24h_quote=_f(opp.get("volume_24h_quote")),
        lifetime_sec=_f(opp.get("lifetime_sec")),
        spread_zscore=_f(opp.get("spread_zscore")),
        book_update_rate_per_min=_f(opp.get("book_update_rate_per_min")),
        score=_f(opp.get("score")),
        score_breakdown=breakdown_json,
    )
    engine = get_engine(path)
    with Session(engine) as session:
        session.add(ev)
        session.commit()
        return ev.id


def record_exit(
    path: Path, *, symbol: str, exited_at_iso: str, exited_at_ms: int
) -> bool:
    """Close the latest open event for ``symbol``. Returns True if updated."""
    if path is None:
        return False
    _ensure(path)
    engine = get_engine(path)
    with Session(engine) as session:
        stmt = (
            select(ScreenerOpportunityEvent)
            .where(
                ScreenerOpportunityEvent.symbol == symbol.upper(),
                ScreenerOpportunityEvent.exited_at.is_(None),
            )
            .order_by(desc(ScreenerOpportunityEvent.found_at_ms))
            .limit(1)
        )
        ev = session.scalars(stmt).first()
        if ev is None:
            return False
        ev.exited_at = exited_at_iso
        ev.duration_sec = max(0.0, (exited_at_ms - ev.found_at_ms) / 1000.0)
        session.commit()
        return True


def close_all_open(path: Path, *, now_iso: str, now_ms: int) -> int:
    """Close every still-open event (startup reset after a restart). Returns count."""
    if path is None:
        return 0
    _ensure(path)
    engine = get_engine(path)
    with Session(engine) as session:
        stmt = (
            update(ScreenerOpportunityEvent)
            .where(ScreenerOpportunityEvent.exited_at.is_(None))
            .values(
                exited_at=now_iso,
                duration_sec=(
                    (now_ms - ScreenerOpportunityEvent.found_at_ms) / 1000.0
                ),
            )
        )
        result = session.execute(stmt)
        session.commit()
        return int(result.rowcount or 0)


def query_events(
    path: Path,
    *,
    limit: int = 100,
    symbol: str | None = None,
    only_open: bool = False,
) -> list[dict]:
    if path is None or not path.is_file():
        return []
    _ensure(path)
    engine = get_engine(path)
    with Session(engine) as session:
        stmt = select(ScreenerOpportunityEvent)
        if symbol:
            stmt = stmt.where(ScreenerOpportunityEvent.symbol == symbol.strip().upper())
        if only_open:
            stmt = stmt.where(ScreenerOpportunityEvent.exited_at.is_(None))
        stmt = stmt.order_by(desc(ScreenerOpportunityEvent.found_at_ms)).limit(
            max(1, min(int(limit), 1000))
        )
        rows = session.scalars(stmt).all()
        return [_event_to_dict(r) for r in rows]


def _event_to_dict(r: ScreenerOpportunityEvent) -> dict:
    breakdown = None
    if r.score_breakdown:
        try:
            breakdown = json.loads(r.score_breakdown)
        except (TypeError, ValueError):
            breakdown = None
    return {
        "id": r.id,
        "symbol": r.symbol,
        "found_at": r.found_at,
        "found_at_ms": r.found_at_ms,
        "exited_at": r.exited_at,
        "duration_sec": round(r.duration_sec, 1) if r.duration_sec is not None else None,
        "net_spread_bps": r.net_spread_bps,
        "spread_bps": r.spread_bps,
        "l1_notional": r.l1_notional,
        "volume_24h_quote": r.volume_24h_quote,
        "lifetime_sec": r.lifetime_sec,
        "spread_zscore": r.spread_zscore,
        "book_update_rate_per_min": r.book_update_rate_per_min,
        "score": r.score,
        "score_breakdown": breakdown,
        "open": r.exited_at is None,
    }


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math

    if math.isnan(f) or math.isinf(f):
        return None
    return f
