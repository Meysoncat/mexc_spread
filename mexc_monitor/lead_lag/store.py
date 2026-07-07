"""Signal Store for the Lead-Lag Arbitrage module.

Persists LeadLagSignal objects to SQLite for historical analysis and backtesting.
Implements in-memory buffering with retry logic when SQLite is unavailable.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 10.4, 10.5
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mexc_monitor.lead_lag.models import (
    LeadLagSignal,
    SignalDirection,
    SignalStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper dataclass for signal resolution updates
# ---------------------------------------------------------------------------


@dataclass
class SignalResolution:
    """Data for updating a resolved/expired signal."""

    resolved_at: str                    # ISO8601
    actual_lag_ms: Optional[float] = None
    exit_spread_bps: Optional[float] = None
    theoretical_pnl_bps: Optional[float] = None


# ---------------------------------------------------------------------------
# Query filter dataclass
# ---------------------------------------------------------------------------


@dataclass
class SignalQuery:
    """Filters for querying signals from the store."""

    symbol: Optional[str] = None
    status: Optional[SignalStatus] = None
    direction: Optional[SignalDirection] = None
    time_from: Optional[str] = None     # ISO8601 inclusive
    time_to: Optional[str] = None       # ISO8601 inclusive
    limit: int = 1000


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lead_lag_signals (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    leader_exchange TEXT NOT NULL,
    lagger_exchange TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    z_score REAL NOT NULL,
    entry_spread_bps REAL NOT NULL,
    leader_mid_at_signal REAL NOT NULL,
    lagger_mid_at_signal REAL NOT NULL,
    estimated_lag_ms REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'expired')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    actual_lag_ms REAL,
    exit_spread_bps REAL,
    theoretical_pnl_bps REAL
);
"""

_CREATE_INDEX_STATUS_SQL = """
CREATE INDEX IF NOT EXISTS idx_signals_status ON lead_lag_signals (status);
"""

_CREATE_INDEX_SYMBOL_SQL = """
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON lead_lag_signals (symbol);
"""

_CREATE_INDEX_CREATED_SQL = """
CREATE INDEX IF NOT EXISTS idx_signals_created ON lead_lag_signals (created_at DESC);
"""

_INSERT_SQL = """
INSERT OR REPLACE INTO lead_lag_signals
    (id, symbol, leader_exchange, lagger_exchange, direction, z_score,
     entry_spread_bps, leader_mid_at_signal, lagger_mid_at_signal,
     estimated_lag_ms, status, created_at, resolved_at, actual_lag_ms,
     exit_spread_bps, theoretical_pnl_bps)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_UPDATE_SQL = """
UPDATE lead_lag_signals
SET resolved_at = ?,
    actual_lag_ms = ?,
    exit_spread_bps = ?,
    theoretical_pnl_bps = ?,
    status = CASE
        WHEN ? IS NOT NULL THEN ?
        ELSE status
    END
WHERE id = ?;
"""

_UPDATE_RESOLUTION_SQL = """
UPDATE lead_lag_signals
SET resolved_at = ?,
    actual_lag_ms = ?,
    exit_spread_bps = ?,
    theoretical_pnl_bps = ?
WHERE id = ?;
"""


# ---------------------------------------------------------------------------
# Buffered operation types
# ---------------------------------------------------------------------------

_OP_SAVE = "save"
_OP_UPDATE = "update"


# ---------------------------------------------------------------------------
# LeadLagStore
# ---------------------------------------------------------------------------


class LeadLagStore:
    """Persists lead-lag signals to SQLite with in-memory fallback buffer.

    When SQLite writes fail, signals are buffered in memory (up to 1000)
    and retried every 30 seconds. FIFO eviction when buffer is full.
    """

    MAX_BUFFER_SIZE = 1000
    RETRY_INTERVAL_SEC = 30.0

    def __init__(self, db_path: str = "data/lead_lag_signals.sqlite") -> None:
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

        # In-memory buffer for failed writes: deque of (op_type, data)
        self._buffer: deque[tuple[str, LeadLagSignal | tuple[str, SignalResolution]]] = deque(
            maxlen=self.MAX_BUFFER_SIZE
        )
        self._buffer_lock = threading.Lock()

        # Retry thread
        self._retry_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._retry_running = False

        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database and create table/indexes."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_STATUS_SQL)
            conn.execute(_CREATE_INDEX_SYMBOL_SQL)
            conn.execute(_CREATE_INDEX_CREATED_SQL)
            conn.commit()
            self._conn = conn
            logger.info("LeadLagStore initialized: db=%s", self._db_path)
        except (sqlite3.Error, OSError) as exc:
            logger.error("Failed to initialize LeadLagStore DB: %s", exc)
            self._conn = None

    def start_retry_loop(self) -> None:
        """Start the background retry thread for buffered writes."""
        if self._retry_running:
            return
        self._stop_event.clear()
        self._retry_running = True
        self._retry_thread = threading.Thread(
            target=self._retry_loop,
            daemon=True,
            name="lead-lag-store-retry",
        )
        self._retry_thread.start()

    def stop(self) -> None:
        """Stop the retry loop and close the database connection."""
        self._stop_event.set()
        self._retry_running = False
        if self._retry_thread:
            self._retry_thread.join(timeout=5.0)
            self._retry_thread = None
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_signal(self, signal: LeadLagSignal) -> None:
        """Save a new signal to SQLite. Buffers in memory on failure.

        Requirement 5.1: Insert new signal with all fields.
        Requirement 5.4, 10.4: Buffer on failure, retry every 30s.
        Requirement 5.5, 10.5: FIFO eviction when buffer full.
        """
        with self._lock:
            if self._conn is not None:
                try:
                    self._execute_save(self._conn, signal)
                    self._conn.commit()
                    return
                except sqlite3.Error as exc:
                    logger.error("Failed to save signal %s: %s", signal.id, exc)

        # SQLite write failed — buffer in memory
        self._buffer_operation(_OP_SAVE, signal)

    def update_signal(self, signal_id: str, resolution: SignalResolution) -> None:
        """Update a signal with resolution data.

        Requirement 5.2: Update resolved_at, actual_lag_ms, exit_spread_bps,
        theoretical_pnl_bps.
        """
        with self._lock:
            if self._conn is not None:
                try:
                    self._execute_update(self._conn, signal_id, resolution)
                    self._conn.commit()
                    return
                except sqlite3.Error as exc:
                    logger.error("Failed to update signal %s: %s", signal_id, exc)

        # SQLite write failed — buffer in memory
        self._buffer_operation(_OP_UPDATE, (signal_id, resolution))

    def query_signals(self, filters: Optional[SignalQuery] = None) -> list[LeadLagSignal]:
        """Query signals with optional filters.

        Requirement 5.3: Filter by symbol, time range, status, direction.
        Limit 1000, sorted by created_at DESC.
        """
        if filters is None:
            filters = SignalQuery()

        # Clamp limit to 1000
        limit = min(max(filters.limit, 1), 1000)

        conditions: list[str] = []
        params: list[str | int | float] = []

        if filters.symbol is not None:
            conditions.append("symbol = ?")
            params.append(filters.symbol)

        if filters.status is not None:
            conditions.append("status = ?")
            params.append(filters.status.value)

        if filters.direction is not None:
            conditions.append("direction = ?")
            params.append(filters.direction.value)

        if filters.time_from is not None:
            conditions.append("created_at >= ?")
            params.append(filters.time_from)

        if filters.time_to is not None:
            conditions.append("created_at <= ?")
            params.append(filters.time_to)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT id, symbol, leader_exchange, lagger_exchange, direction,
                   z_score, entry_spread_bps, leader_mid_at_signal,
                   lagger_mid_at_signal, estimated_lag_ms, status, created_at,
                   resolved_at, actual_lag_ms, exit_spread_bps, theoretical_pnl_bps
            FROM lead_lag_signals
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?;
        """
        params.append(limit)

        with self._lock:
            if self._conn is None:
                return []
            try:
                cursor = self._conn.execute(query, params)
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                logger.error("Failed to query signals: %s", exc)
                return []

        return [self._row_to_signal(row) for row in rows]

    def get_signal_by_id(self, signal_id: str) -> Optional[LeadLagSignal]:
        """Retrieve a single signal by ID."""
        query = """
            SELECT id, symbol, leader_exchange, lagger_exchange, direction,
                   z_score, entry_spread_bps, leader_mid_at_signal,
                   lagger_mid_at_signal, estimated_lag_ms, status, created_at,
                   resolved_at, actual_lag_ms, exit_spread_bps, theoretical_pnl_bps
            FROM lead_lag_signals
            WHERE id = ?;
        """
        with self._lock:
            if self._conn is None:
                return None
            try:
                cursor = self._conn.execute(query, (signal_id,))
                row = cursor.fetchone()
            except sqlite3.Error as exc:
                logger.error("Failed to get signal %s: %s", signal_id, exc)
                return None

        if row is None:
            return None
        return self._row_to_signal(row)

    @property
    def buffer_size(self) -> int:
        """Return the current number of buffered operations."""
        with self._buffer_lock:
            return len(self._buffer)

    @property
    def is_connected(self) -> bool:
        """Return whether the SQLite connection is available."""
        with self._lock:
            return self._conn is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_save(conn: sqlite3.Connection, signal: LeadLagSignal) -> None:
        """Execute INSERT for a signal."""
        conn.execute(_INSERT_SQL, (
            signal.id,
            signal.symbol,
            signal.leader_exchange,
            signal.lagger_exchange,
            signal.direction.value if isinstance(signal.direction, SignalDirection) else signal.direction,
            signal.z_score,
            signal.entry_spread_bps,
            signal.leader_mid_at_signal,
            signal.lagger_mid_at_signal,
            signal.estimated_lag_ms,
            signal.status.value if isinstance(signal.status, SignalStatus) else signal.status,
            signal.created_at,
            signal.resolved_at,
            signal.actual_lag_ms,
            signal.exit_spread_bps,
            signal.theoretical_pnl_bps,
        ))

    @staticmethod
    def _execute_update(
        conn: sqlite3.Connection, signal_id: str, resolution: SignalResolution
    ) -> None:
        """Execute UPDATE for a signal resolution."""
        conn.execute(_UPDATE_RESOLUTION_SQL, (
            resolution.resolved_at,
            resolution.actual_lag_ms,
            resolution.exit_spread_bps,
            resolution.theoretical_pnl_bps,
            signal_id,
        ))

    def _buffer_operation(
        self, op_type: str, data: LeadLagSignal | tuple[str, SignalResolution]
    ) -> None:
        """Add an operation to the in-memory buffer.

        FIFO eviction when buffer is full (deque maxlen handles this).
        Logs a warning when buffer reaches capacity.
        """
        with self._buffer_lock:
            was_full = len(self._buffer) >= self.MAX_BUFFER_SIZE
            self._buffer.append((op_type, data))
            if was_full:
                logger.warning(
                    "LeadLagStore buffer full (%d). Oldest entry evicted (FIFO).",
                    self.MAX_BUFFER_SIZE,
                )
            else:
                logger.debug(
                    "Buffered %s operation. Buffer size: %d",
                    op_type, len(self._buffer),
                )

        # Start retry loop if not already running
        if not self._retry_running:
            self.start_retry_loop()

    def _retry_loop(self) -> None:
        """Background thread: retry buffered writes every 30 seconds."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.RETRY_INTERVAL_SEC)
            if self._stop_event.is_set():
                break
            self._flush_buffer()

    def _flush_buffer(self) -> None:
        """Attempt to write all buffered operations to SQLite."""
        with self._buffer_lock:
            if not self._buffer:
                return
            # Take a snapshot of current buffer
            pending = list(self._buffer)

        # Try to reconnect if needed
        with self._lock:
            if self._conn is None:
                try:
                    self._db_path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute(_CREATE_TABLE_SQL)
                    conn.execute(_CREATE_INDEX_STATUS_SQL)
                    conn.execute(_CREATE_INDEX_SYMBOL_SQL)
                    conn.execute(_CREATE_INDEX_CREATED_SQL)
                    conn.commit()
                    self._conn = conn
                    logger.info("LeadLagStore reconnected to DB: %s", self._db_path)
                except (sqlite3.Error, OSError) as exc:
                    logger.error("LeadLagStore reconnect failed: %s", exc)
                    return

        # Attempt to flush
        flushed_count = 0
        with self._lock:
            if self._conn is None:
                return
            try:
                for op_type, data in pending:
                    if op_type == _OP_SAVE:
                        self._execute_save(self._conn, data)  # type: ignore[arg-type]
                    elif op_type == _OP_UPDATE:
                        signal_id, resolution = data  # type: ignore[misc]
                        self._execute_update(self._conn, signal_id, resolution)
                    flushed_count += 1
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.error(
                    "LeadLagStore flush failed after %d ops: %s", flushed_count, exc
                )
                # Partial flush: remove only successfully written items
                with self._buffer_lock:
                    for _ in range(flushed_count):
                        if self._buffer:
                            self._buffer.popleft()
                return

        # Full flush successful — clear buffer
        with self._buffer_lock:
            # Remove only the items we flushed (new items may have been added)
            for _ in range(min(flushed_count, len(self._buffer))):
                self._buffer.popleft()

        if flushed_count > 0:
            logger.info("LeadLagStore flushed %d buffered operations", flushed_count)

    @staticmethod
    def _row_to_signal(row: tuple) -> LeadLagSignal:
        """Convert a database row tuple to a LeadLagSignal object."""
        return LeadLagSignal(
            id=row[0],
            symbol=row[1],
            leader_exchange=row[2],
            lagger_exchange=row[3],
            direction=SignalDirection(row[4]),
            z_score=row[5],
            entry_spread_bps=row[6],
            leader_mid_at_signal=row[7],
            lagger_mid_at_signal=row[8],
            estimated_lag_ms=row[9],
            status=SignalStatus(row[10]),
            created_at=row[11],
            resolved_at=row[12],
            actual_lag_ms=row[13],
            exit_spread_bps=row[14],
            theoretical_pnl_bps=row[15],
        )
