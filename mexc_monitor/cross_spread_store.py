"""
Cross-Spread History Store — периодическая запись межбиржевого спреда в SQLite.

Записывает снимки MEXC ↔ AsterDEX basis для всех символов с данными на обеих биржах.
Поддерживает retention (удаление старых записей) и downsampling для длинных периодов.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mexc_monitor.orm import CrossSpreadSnapshot, create_schema, get_engine
from mexc_monitor.spread_buffer import get_latest, get_tracked_symbols

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/cross_spread_history.sqlite"
_DEFAULT_INTERVAL_SEC = 60.0
_DEFAULT_RETENTION_DAYS = 30


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_db_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return _repo_root() / p


class CrossSpreadWorker:
    """Фоновый поток записи кросс-спреда в SQLite."""

    def __init__(
        self,
        db_path: str = _DEFAULT_DB_PATH,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ):
        self._db_path = _resolve_db_path(db_path)
        self._interval_sec = max(5.0, interval_sec)
        self._retention_days = max(1, retention_days)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        # Init DB
        create_schema(self._db_path)
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="cross-spread-store",
        )
        self._thread.start()
        logger.info("CrossSpreadWorker started: interval=%ss, retention=%sd", self._interval_sec, self._retention_days)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._snapshot_tick()
            except Exception:
                logger.exception("CrossSpreadWorker tick failed")
            self._stop_event.wait(timeout=self._interval_sec)

    def _snapshot_tick(self) -> None:
        """Один цикл: собрать данные, записать, почистить."""
        now_iso = datetime.now(timezone.utc).isoformat()
        tracked = get_tracked_symbols()

        # Найти символы с данными на обеих биржах
        # MEXC: BTCUSDT или BTC_USDT
        # AsterDEX: ASTER:BTCUSDT
        aster_symbols = [s.replace("ASTER:", "") for s in tracked if s.startswith("ASTER:")]
        mexc_symbols = [s for s in tracked if not s.startswith("ASTER:") and not s.startswith("CROSS:")]

        # Маппинг: для каждого aster символа найти соответствующий MEXC
        batch: list[CrossSpreadSnapshot] = []
        for aster_sym in aster_symbols:
            aster_tick = get_latest(f"ASTER:{aster_sym}")
            if aster_tick is None:
                continue

            # Try MEXC spot (BTCUSDT) or futures (BTC_USDT)
            mexc_tick = get_latest(aster_sym)
            if mexc_tick is None:
                fut_sym = aster_sym.replace("USDT", "_USDT") if "USDT" in aster_sym and "_" not in aster_sym else None
                if fut_sym:
                    mexc_tick = get_latest(fut_sym)
            if mexc_tick is None:
                continue

            # Compute basis
            aster_mid = (aster_tick.bid + aster_tick.ask) / 2
            mexc_mid = mexc_tick.mid
            if mexc_mid <= 0 or aster_mid <= 0:
                continue

            basis_abs = aster_mid - mexc_mid
            basis_bps = 10_000 * basis_abs / mexc_mid

            batch.append(CrossSpreadSnapshot(
                symbol=aster_sym,
                mexc_bid=mexc_tick.bid,
                mexc_ask=mexc_tick.ask,
                mexc_mid=mexc_mid,
                aster_bid=aster_tick.bid,
                aster_ask=aster_tick.ask,
                aster_mid=aster_mid,
                basis_abs=basis_abs,
                basis_bps=basis_bps,
                funding_rate=None,  # Could be enriched later
                observed_at=now_iso,
            ))

        if batch:
            engine = get_engine(self._db_path)
            with Session(engine) as session:
                session.add_all(batch)
                session.commit()
            logger.debug("CrossSpreadWorker: stored %d rows", len(batch))

        # Retention cleanup
        self._cleanup_old_records()

    def _cleanup_old_records(self) -> None:
        """Удалить записи старше retention_days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        engine = get_engine(self._db_path)
        try:
            with Session(engine) as session:
                stmt = delete(CrossSpreadSnapshot).where(CrossSpreadSnapshot.observed_at < cutoff)
                result = session.execute(stmt)
                if result.rowcount > 0:
                    logger.debug("CrossSpreadWorker: cleaned %d old rows", result.rowcount)
                session.commit()
        except Exception:
            logger.exception("CrossSpreadWorker cleanup failed")


def query_cross_spread_history(
    db_path: str = _DEFAULT_DB_PATH,
    *,
    symbol: str | None = None,
    since_iso: str | None = None,
    until_iso: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Запрос истории кросс-спреда с downsampling."""
    path = _resolve_db_path(db_path)
    if not path.is_file():
        return []

    engine = get_engine(path)
    with Session(engine) as session:
        stmt = select(CrossSpreadSnapshot)
        if symbol:
            stmt = stmt.where(CrossSpreadSnapshot.symbol == symbol.strip().upper())
        if since_iso:
            stmt = stmt.where(CrossSpreadSnapshot.observed_at >= since_iso.strip())
        if until_iso:
            stmt = stmt.where(CrossSpreadSnapshot.observed_at <= until_iso.strip())
        stmt = stmt.order_by(CrossSpreadSnapshot.observed_at.asc())

        rows = session.scalars(stmt).all()

    result = [
        {
            "symbol": r.symbol,
            "mexc_bid": r.mexc_bid,
            "mexc_ask": r.mexc_ask,
            "mexc_mid": r.mexc_mid,
            "aster_bid": r.aster_bid,
            "aster_ask": r.aster_ask,
            "aster_mid": r.aster_mid,
            "basis_abs": r.basis_abs,
            "basis_bps": r.basis_bps,
            "funding_rate": r.funding_rate,
            "observed_at": r.observed_at,
        }
        for r in rows
    ]

    # Downsampling if too many points
    if len(result) > limit:
        step = len(result) / limit
        sampled = []
        idx = 0.0
        while int(idx) < len(result):
            sampled.append(result[int(idx)])
            idx += step
        result = sampled

    return result


# ─── Module-level singleton ──────────────────────────────────────────────────

_worker: CrossSpreadWorker | None = None


def start_cross_spread_worker(
    db_path: str = _DEFAULT_DB_PATH,
    interval_sec: float = _DEFAULT_INTERVAL_SEC,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
) -> None:
    global _worker
    if _worker is not None:
        return
    _worker = CrossSpreadWorker(
        db_path=db_path,
        interval_sec=interval_sec,
        retention_days=retention_days,
    )
    _worker.start()


def stop_cross_spread_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
