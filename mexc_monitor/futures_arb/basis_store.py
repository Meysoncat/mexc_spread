"""
Basis History Store — периодическая запись базиса в SQLite.

Записывает значения базиса с настраиваемым интервалом (по умолчанию 60 секунд).
Поддерживает retention policy: удаление записей старше retention_days.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from mexc_monitor.futures_arb.basis_calculator import BasisCalculator
from mexc_monitor.futures_arb.models import BasisSnapshot, FuturesArbSettings

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS basis_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange_combo TEXT NOT NULL,
    spot_mid REAL NOT NULL,
    futures_mid REAL NOT NULL,
    basis_abs REAL NOT NULL,
    basis_bps REAL NOT NULL,
    funding_rate REAL,
    spot_spread_bps REAL,
    futures_spread_bps REAL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_basis_symbol_combo_ts
ON basis_snapshots (symbol, exchange_combo, timestamp);
"""

_INSERT_SQL = """
INSERT INTO basis_snapshots
    (timestamp, symbol, exchange_combo, spot_mid, futures_mid, basis_abs, basis_bps,
     funding_rate, spot_spread_bps, futures_spread_bps)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_QUERY_SQL = """
SELECT timestamp, symbol, exchange_combo, spot_mid, futures_mid,
       basis_abs, basis_bps, funding_rate, spot_spread_bps, futures_spread_bps
FROM basis_snapshots
WHERE symbol = ? AND exchange_combo = ?
  AND timestamp >= ? AND timestamp <= ?
ORDER BY timestamp DESC
LIMIT ?;
"""

_DELETE_OLD_SQL = """
DELETE FROM basis_snapshots WHERE timestamp < ?;
"""

_COUNT_SQL = """
SELECT COUNT(*) FROM basis_snapshots;
"""


class BasisHistoryStore:
    """
    Периодическая запись базиса в SQLite.

    Записывает текущие значения базиса для всех мониторируемых пар
    с настраиваемым интервалом. Поддерживает retention policy.
    """

    def __init__(
        self,
        db_path: str | Path = "data/basis_history.db",
        interval_sec: float = 60.0,
        retention_days: int = 90,
        basis_calculator: BasisCalculator | None = None,
    ):
        self._db_path = Path(db_path)
        self._interval_sec = interval_sec
        self._retention_days = retention_days
        self._basis_calculator = basis_calculator

        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._conn: sqlite3.Connection | None = None

        # Ensure DB directory exists and create table
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database and create table if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)
        conn.commit()
        self._conn = conn

    def start(self) -> None:
        """Start the periodic recording loop."""
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._record_loop,
            daemon=True,
            name="basis-history-store",
        )
        self._thread.start()
        logger.info(
            "BasisHistoryStore started: interval=%.0fs, retention=%dd, db=%s",
            self._interval_sec, self._retention_days, self._db_path,
        )

    def stop(self) -> None:
        """Stop the periodic recording loop."""
        if not self._running:
            return

        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval_sec + 5)
            self._thread = None
        logger.info("BasisHistoryStore stopped")

    def close(self) -> None:
        """Close the database connection."""
        self.stop()
        if self._conn:
            self._conn.close()
            self._conn = None

    def query_history(
        self,
        symbol: str,
        exchange_combo: str,
        since: str | None = None,
        until: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Query basis history for a specific symbol and exchange_combo.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            exchange_combo: Exchange combination (e.g., "mexc_spot+mexc_futures")
            since: ISO8601 timestamp (inclusive). Defaults to 24h ago.
            until: ISO8601 timestamp (inclusive). Defaults to now.
            limit: Maximum number of records to return.

        Returns:
            List of dicts with basis history data, ordered by timestamp DESC.
        """
        if since is None:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        if until is None:
            until = datetime.now(timezone.utc).isoformat()

        if not self._conn:
            return []

        try:
            cursor = self._conn.execute(
                _QUERY_SQL, (symbol, exchange_combo, since, until, limit)
            )
            rows = cursor.fetchall()
        except sqlite3.Error as e:
            logger.error("Query error: %s", e)
            return []

        results = []
        for row in rows:
            results.append({
                "timestamp": row[0],
                "symbol": row[1],
                "exchange_combo": row[2],
                "spot_mid": row[3],
                "futures_mid": row[4],
                "basis_abs": row[5],
                "basis_bps": row[6],
                "funding_rate": row[7],
                "spot_spread_bps": row[8],
                "futures_spread_bps": row[9],
            })

        return results

    def get_record_count(self) -> int:
        """Get total number of records in the database."""
        if not self._conn:
            return 0
        try:
            cursor = self._conn.execute(_COUNT_SQL)
            return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    def record_snapshot(self, snapshot: BasisSnapshot) -> None:
        """Record a single basis snapshot to the database."""
        if not self._conn:
            return

        timestamp = datetime.fromtimestamp(
            snapshot.timestamp_ms / 1000, tz=timezone.utc
        ).isoformat()

        try:
            self._conn.execute(_INSERT_SQL, (
                timestamp,
                snapshot.symbol,
                snapshot.exchange_combo,
                snapshot.spot_mid,
                snapshot.futures_mid,
                snapshot.basis_abs,
                snapshot.basis_bps,
                snapshot.funding_rate,
                None,  # spot_spread_bps — not available in BasisSnapshot
                None,  # futures_spread_bps — not available in BasisSnapshot
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to record basis snapshot: %s", e)

    def cleanup_old_records(self) -> int:
        """
        Delete records older than retention_days.

        Returns the number of deleted records.
        """
        if not self._conn:
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()

        try:
            cursor = self._conn.execute(_DELETE_OLD_SQL, (cutoff,))
            self._conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info("Cleaned up %d old basis records (before %s)", deleted, cutoff)
            return deleted
        except sqlite3.Error as e:
            logger.error("Cleanup error: %s", e)
            return 0

    # --- Internal ---

    def _record_loop(self) -> None:
        """Background loop: record snapshots at interval."""
        cleanup_counter = 0

        while not self._stop_event.is_set():
            try:
                self._record_all_snapshots()
            except Exception:
                logger.exception("BasisHistoryStore record error")

            # Periodic cleanup (every 100 iterations ≈ every ~100 minutes at 60s interval)
            cleanup_counter += 1
            if cleanup_counter >= 100:
                cleanup_counter = 0
                try:
                    self.cleanup_old_records()
                except Exception:
                    logger.exception("BasisHistoryStore cleanup error")

            self._stop_event.wait(timeout=self._interval_sec)

    def _record_all_snapshots(self) -> None:
        """Record current basis snapshots for all monitored pairs."""
        if not self._basis_calculator:
            return

        snapshots = self._basis_calculator.get_all_basis()
        for snapshot in snapshots:
            if snapshot.status == "active":
                self.record_snapshot(snapshot)
