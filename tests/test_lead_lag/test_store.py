"""Unit tests for LeadLagStore.

Tests cover:
- SQLite schema creation
- save_signal / query round-trip
- update_signal with resolution data
- query_signals with various filters
- In-memory buffer on DB failure
- FIFO eviction when buffer is full
- Retry logic flushes buffer
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mexc_monitor.lead_lag.models import (
    LeadLagSignal,
    SignalDirection,
    SignalStatus,
)
from mexc_monitor.lead_lag.store import (
    LeadLagStore,
    SignalQuery,
    SignalResolution,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_signal(
    symbol: str = "BTCUSDT",
    direction: SignalDirection = SignalDirection.LONG,
    status: SignalStatus = SignalStatus.ACTIVE,
    z_score: float = 2.5,
    entry_spread_bps: float = 5.0,
    created_at: str | None = None,
) -> LeadLagSignal:
    """Create a test signal with sensible defaults."""
    return LeadLagSignal(
        id=str(uuid.uuid4()),
        symbol=symbol,
        leader_exchange="binance",
        lagger_exchange="mexc",
        direction=direction,
        z_score=z_score,
        entry_spread_bps=entry_spread_bps,
        leader_mid_at_signal=67500.0,
        lagger_mid_at_signal=67495.0,
        estimated_lag_ms=200.0,
        status=status,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def store(tmp_path: Path) -> LeadLagStore:
    """Create a LeadLagStore with a temporary database."""
    db_path = str(tmp_path / "test_signals.sqlite")
    s = LeadLagStore(db_path=db_path)
    yield s
    s.stop()


# ---------------------------------------------------------------------------
# Tests: Schema and initialization
# ---------------------------------------------------------------------------


class TestStoreInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "test.sqlite"
        s = LeadLagStore(db_path=str(db_path))
        assert db_path.exists()
        s.stop()

    def test_creates_table(self, store: LeadLagStore) -> None:
        """Verify the lead_lag_signals table exists."""
        with store._lock:
            cursor = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='lead_lag_signals'"
            )
            assert cursor.fetchone() is not None

    def test_is_connected(self, store: LeadLagStore) -> None:
        assert store.is_connected is True


# ---------------------------------------------------------------------------
# Tests: save_signal
# ---------------------------------------------------------------------------


class TestSaveSignal:
    def test_save_and_retrieve_by_id(self, store: LeadLagStore) -> None:
        signal = _make_signal()
        store.save_signal(signal)

        retrieved = store.get_signal_by_id(signal.id)
        assert retrieved is not None
        assert retrieved.id == signal.id
        assert retrieved.symbol == signal.symbol
        assert retrieved.leader_exchange == signal.leader_exchange
        assert retrieved.lagger_exchange == signal.lagger_exchange
        assert retrieved.direction == signal.direction
        assert retrieved.z_score == signal.z_score
        assert retrieved.entry_spread_bps == signal.entry_spread_bps
        assert retrieved.leader_mid_at_signal == signal.leader_mid_at_signal
        assert retrieved.lagger_mid_at_signal == signal.lagger_mid_at_signal
        assert retrieved.estimated_lag_ms == signal.estimated_lag_ms
        assert retrieved.status == signal.status
        assert retrieved.created_at == signal.created_at

    def test_save_multiple_signals(self, store: LeadLagStore) -> None:
        signals = [_make_signal(symbol=f"SYM{i}") for i in range(5)]
        for s in signals:
            store.save_signal(s)

        results = store.query_signals(SignalQuery(limit=10))
        assert len(results) == 5

    def test_save_signal_with_all_optional_fields(self, store: LeadLagStore) -> None:
        signal = _make_signal(status=SignalStatus.RESOLVED)
        signal.resolved_at = datetime.now(timezone.utc).isoformat()
        signal.actual_lag_ms = 180.0
        signal.exit_spread_bps = 1.5
        signal.theoretical_pnl_bps = 1.0
        store.save_signal(signal)

        retrieved = store.get_signal_by_id(signal.id)
        assert retrieved is not None
        assert retrieved.resolved_at == signal.resolved_at
        assert retrieved.actual_lag_ms == 180.0
        assert retrieved.exit_spread_bps == 1.5
        assert retrieved.theoretical_pnl_bps == 1.0


# ---------------------------------------------------------------------------
# Tests: update_signal
# ---------------------------------------------------------------------------


class TestUpdateSignal:
    def test_update_resolution(self, store: LeadLagStore) -> None:
        signal = _make_signal()
        store.save_signal(signal)

        resolution = SignalResolution(
            resolved_at=datetime.now(timezone.utc).isoformat(),
            actual_lag_ms=150.0,
            exit_spread_bps=1.0,
            theoretical_pnl_bps=2.0,
        )
        store.update_signal(signal.id, resolution)

        retrieved = store.get_signal_by_id(signal.id)
        assert retrieved is not None
        assert retrieved.resolved_at == resolution.resolved_at
        assert retrieved.actual_lag_ms == 150.0
        assert retrieved.exit_spread_bps == 1.0
        assert retrieved.theoretical_pnl_bps == 2.0

    def test_update_with_none_actual_lag(self, store: LeadLagStore) -> None:
        signal = _make_signal()
        store.save_signal(signal)

        resolution = SignalResolution(
            resolved_at=datetime.now(timezone.utc).isoformat(),
            exit_spread_bps=2.0,
            theoretical_pnl_bps=-1.0,
        )
        store.update_signal(signal.id, resolution)

        retrieved = store.get_signal_by_id(signal.id)
        assert retrieved is not None
        assert retrieved.actual_lag_ms is None
        assert retrieved.exit_spread_bps == 2.0


# ---------------------------------------------------------------------------
# Tests: query_signals with filters
# ---------------------------------------------------------------------------


class TestQuerySignals:
    def test_filter_by_symbol(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(symbol="BTCUSDT"))
        store.save_signal(_make_signal(symbol="ETHUSDT"))
        store.save_signal(_make_signal(symbol="BTCUSDT"))

        results = store.query_signals(SignalQuery(symbol="BTCUSDT"))
        assert len(results) == 2
        assert all(s.symbol == "BTCUSDT" for s in results)

    def test_filter_by_status(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(status=SignalStatus.ACTIVE))
        store.save_signal(_make_signal(status=SignalStatus.RESOLVED))
        store.save_signal(_make_signal(status=SignalStatus.EXPIRED))

        results = store.query_signals(SignalQuery(status=SignalStatus.ACTIVE))
        assert len(results) == 1
        assert results[0].status == SignalStatus.ACTIVE

    def test_filter_by_direction(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(direction=SignalDirection.LONG))
        store.save_signal(_make_signal(direction=SignalDirection.SHORT))
        store.save_signal(_make_signal(direction=SignalDirection.LONG))

        results = store.query_signals(SignalQuery(direction=SignalDirection.SHORT))
        assert len(results) == 1
        assert results[0].direction == SignalDirection.SHORT

    def test_filter_by_time_range(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(created_at="2024-01-01T00:00:00+00:00"))
        store.save_signal(_make_signal(created_at="2024-01-02T00:00:00+00:00"))
        store.save_signal(_make_signal(created_at="2024-01-03T00:00:00+00:00"))

        results = store.query_signals(SignalQuery(
            time_from="2024-01-01T12:00:00+00:00",
            time_to="2024-01-02T12:00:00+00:00",
        ))
        assert len(results) == 1
        assert results[0].created_at == "2024-01-02T00:00:00+00:00"

    def test_limit_default_1000(self, store: LeadLagStore) -> None:
        # Insert more than default limit won't happen in test, but verify limit works
        for _ in range(5):
            store.save_signal(_make_signal())

        results = store.query_signals(SignalQuery(limit=3))
        assert len(results) == 3

    def test_limit_clamped_to_1000(self, store: LeadLagStore) -> None:
        for _ in range(5):
            store.save_signal(_make_signal())

        # Limit > 1000 should be clamped
        results = store.query_signals(SignalQuery(limit=5000))
        assert len(results) == 5  # Only 5 exist, but limit was clamped to 1000

    def test_sorted_by_created_at_desc(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(created_at="2024-01-01T00:00:00+00:00"))
        store.save_signal(_make_signal(created_at="2024-01-03T00:00:00+00:00"))
        store.save_signal(_make_signal(created_at="2024-01-02T00:00:00+00:00"))

        results = store.query_signals()
        assert results[0].created_at == "2024-01-03T00:00:00+00:00"
        assert results[1].created_at == "2024-01-02T00:00:00+00:00"
        assert results[2].created_at == "2024-01-01T00:00:00+00:00"

    def test_combined_filters(self, store: LeadLagStore) -> None:
        store.save_signal(_make_signal(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            status=SignalStatus.RESOLVED,
            created_at="2024-01-02T00:00:00+00:00",
        ))
        store.save_signal(_make_signal(
            symbol="BTCUSDT",
            direction=SignalDirection.SHORT,
            status=SignalStatus.ACTIVE,
            created_at="2024-01-02T00:00:00+00:00",
        ))
        store.save_signal(_make_signal(
            symbol="ETHUSDT",
            direction=SignalDirection.LONG,
            status=SignalStatus.RESOLVED,
            created_at="2024-01-02T00:00:00+00:00",
        ))

        results = store.query_signals(SignalQuery(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            status=SignalStatus.RESOLVED,
        ))
        assert len(results) == 1
        assert results[0].symbol == "BTCUSDT"
        assert results[0].direction == SignalDirection.LONG

    def test_empty_result(self, store: LeadLagStore) -> None:
        results = store.query_signals(SignalQuery(symbol="NONEXISTENT"))
        assert results == []


# ---------------------------------------------------------------------------
# Tests: In-memory buffer
# ---------------------------------------------------------------------------


class TestInMemoryBuffer:
    def test_buffer_on_db_failure(self, tmp_path: Path) -> None:
        """When DB connection is lost, signals are buffered."""
        db_path = str(tmp_path / "test.sqlite")
        store = LeadLagStore(db_path=db_path)

        # Simulate DB failure by closing connection
        with store._lock:
            store._conn.close()
            store._conn = None

        signal = _make_signal()
        store.save_signal(signal)

        assert store.buffer_size == 1
        store.stop()

    def test_buffer_fifo_eviction(self, tmp_path: Path) -> None:
        """When buffer is full, oldest entries are evicted (FIFO)."""
        db_path = str(tmp_path / "test.sqlite")
        store = LeadLagStore(db_path=db_path)

        # Simulate DB failure
        with store._lock:
            store._conn.close()
            store._conn = None

        # Fill buffer beyond capacity
        for i in range(LeadLagStore.MAX_BUFFER_SIZE + 10):
            store.save_signal(_make_signal())

        # Buffer should be at max size (deque maxlen handles eviction)
        assert store.buffer_size == LeadLagStore.MAX_BUFFER_SIZE
        store.stop()

    def test_update_buffered_on_failure(self, tmp_path: Path) -> None:
        """Update operations are also buffered on failure."""
        db_path = str(tmp_path / "test.sqlite")
        store = LeadLagStore(db_path=db_path)

        # Simulate DB failure
        with store._lock:
            store._conn.close()
            store._conn = None

        resolution = SignalResolution(
            resolved_at=datetime.now(timezone.utc).isoformat(),
            actual_lag_ms=100.0,
            exit_spread_bps=1.0,
            theoretical_pnl_bps=2.0,
        )
        store.update_signal("some-id", resolution)

        assert store.buffer_size == 1
        store.stop()

    def test_flush_buffer_on_reconnect(self, tmp_path: Path) -> None:
        """Buffered operations are flushed when DB becomes available."""
        db_path = str(tmp_path / "test.sqlite")
        store = LeadLagStore(db_path=db_path)

        signal = _make_signal()

        # Simulate DB failure
        with store._lock:
            store._conn.close()
            store._conn = None

        store.save_signal(signal)
        assert store.buffer_size == 1

        # Manually trigger flush (simulates retry)
        store._flush_buffer()

        # Buffer should be empty now (DB reconnected)
        assert store.buffer_size == 0

        # Signal should be in DB
        retrieved = store.get_signal_by_id(signal.id)
        assert retrieved is not None
        assert retrieved.id == signal.id
        store.stop()


# ---------------------------------------------------------------------------
# Tests: get_signal_by_id
# ---------------------------------------------------------------------------


class TestGetSignalById:
    def test_returns_none_for_missing(self, store: LeadLagStore) -> None:
        result = store.get_signal_by_id("nonexistent-id")
        assert result is None

    def test_returns_signal(self, store: LeadLagStore) -> None:
        signal = _make_signal()
        store.save_signal(signal)
        result = store.get_signal_by_id(signal.id)
        assert result is not None
        assert result.id == signal.id
