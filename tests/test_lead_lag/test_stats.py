"""Unit tests for the StatsEngine class.

Tests cover:
- summary() with various signal combinations
- per_symbol_stats() breakdown
- lag_distribution() histogram generation
- Edge cases: no signals, no resolved signals, null values
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from mexc_monitor.lead_lag.models import (
    LeadLagSignal,
    SignalDirection,
    SignalStatus,
)
from mexc_monitor.lead_lag.stats import LagDistribution, StatsEngine, SymbolStats
from mexc_monitor.lead_lag.store import LeadLagStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    symbol: str = "BTCUSDT",
    status: SignalStatus = SignalStatus.RESOLVED,
    direction: SignalDirection = SignalDirection.LONG,
    actual_lag_ms: float | None = 150.0,
    theoretical_pnl_bps: float | None = 2.5,
    created_at: str | None = None,
) -> LeadLagSignal:
    """Create a test signal with sensible defaults."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    resolved_at = None
    if status in (SignalStatus.RESOLVED, SignalStatus.EXPIRED):
        resolved_at = (
            datetime.now(timezone.utc) + timedelta(milliseconds=actual_lag_ms or 100)
        ).isoformat()

    return LeadLagSignal(
        id=str(uuid.uuid4()),
        symbol=symbol,
        leader_exchange="binance",
        lagger_exchange="mexc",
        direction=direction,
        z_score=2.5,
        entry_spread_bps=5.0,
        leader_mid_at_signal=67500.0,
        lagger_mid_at_signal=67495.0,
        estimated_lag_ms=200.0,
        status=status,
        created_at=created_at,
        resolved_at=resolved_at,
        actual_lag_ms=actual_lag_ms,
        exit_spread_bps=1.0 if status != SignalStatus.ACTIVE else None,
        theoretical_pnl_bps=theoretical_pnl_bps,
    )


@pytest.fixture
def store(tmp_path) -> LeadLagStore:
    """Create a temporary LeadLagStore for testing."""
    db_path = str(tmp_path / "test_stats.sqlite")
    s = LeadLagStore(db_path=db_path)
    yield s
    s.stop()


@pytest.fixture
def engine(store: LeadLagStore) -> StatsEngine:
    """Create a StatsEngine with the test store."""
    return StatsEngine(store)


# ---------------------------------------------------------------------------
# Tests: summary()
# ---------------------------------------------------------------------------


class TestSummary:
    def test_empty_signals_returns_zeros(self, engine: StatsEngine) -> None:
        """Requirement 6.5: Zero counters and null for avg/median when no signals."""
        stats = engine.summary(window_hours=24)

        assert stats.window_hours == 24
        assert stats.total_signals == 0
        assert stats.resolved_signals == 0
        assert stats.expired_signals == 0
        assert stats.win_rate is None
        assert stats.avg_lag_ms is None
        assert stats.median_lag_ms is None
        assert stats.avg_theoretical_pnl_bps is None
        assert stats.total_theoretical_pnl_bps == 0.0
        assert stats.signals_per_hour == 0.0
        assert stats.top_symbols == []

    def test_counts_resolved_and_expired(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.1: Correct counting of resolved and expired signals."""
        store.save_signal(_make_signal(status=SignalStatus.RESOLVED))
        store.save_signal(_make_signal(status=SignalStatus.RESOLVED))
        store.save_signal(_make_signal(status=SignalStatus.EXPIRED, theoretical_pnl_bps=-1.0))

        stats = engine.summary(window_hours=24)

        assert stats.total_signals == 3
        assert stats.resolved_signals == 2
        assert stats.expired_signals == 1

    def test_win_rate_calculation(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.1: win_rate = resolved with pnl > 0 / total resolved."""
        # 2 wins (pnl > 0), 1 loss (pnl < 0)
        store.save_signal(_make_signal(theoretical_pnl_bps=3.0))
        store.save_signal(_make_signal(theoretical_pnl_bps=1.5))
        store.save_signal(_make_signal(theoretical_pnl_bps=-2.0))

        stats = engine.summary(window_hours=24)

        assert stats.win_rate == pytest.approx(2.0 / 3.0)

    def test_win_rate_none_when_no_resolved(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Win rate is None when there are no resolved signals with PnL."""
        store.save_signal(_make_signal(
            status=SignalStatus.ACTIVE,
            actual_lag_ms=None,
            theoretical_pnl_bps=None,
        ))

        stats = engine.summary(window_hours=24)

        assert stats.win_rate is None

    def test_avg_and_median_lag(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.1: avg_lag_ms and median_lag_ms computation."""
        store.save_signal(_make_signal(actual_lag_ms=100.0))
        store.save_signal(_make_signal(actual_lag_ms=200.0))
        store.save_signal(_make_signal(actual_lag_ms=300.0))

        stats = engine.summary(window_hours=24)

        assert stats.avg_lag_ms == pytest.approx(200.0)
        assert stats.median_lag_ms == pytest.approx(200.0)

    def test_pnl_aggregation(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.1: avg and total theoretical PnL."""
        store.save_signal(_make_signal(theoretical_pnl_bps=4.0))
        store.save_signal(_make_signal(theoretical_pnl_bps=2.0))
        store.save_signal(_make_signal(theoretical_pnl_bps=-1.0))

        stats = engine.summary(window_hours=24)

        assert stats.avg_theoretical_pnl_bps == pytest.approx(5.0 / 3.0)
        assert stats.total_theoretical_pnl_bps == pytest.approx(5.0)

    def test_signals_per_hour(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.1: signals_per_hour = total / window_hours."""
        for _ in range(6):
            store.save_signal(_make_signal())

        stats = engine.summary(window_hours=24)

        assert stats.signals_per_hour == pytest.approx(6.0 / 24.0)

    def test_top_symbols(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Top symbols sorted by signal count."""
        for _ in range(5):
            store.save_signal(_make_signal(symbol="BTCUSDT"))
        for _ in range(3):
            store.save_signal(_make_signal(symbol="ETHUSDT"))
        store.save_signal(_make_signal(symbol="SOLUSDT"))

        stats = engine.summary(window_hours=24)

        assert stats.top_symbols[0] == "BTCUSDT"
        assert stats.top_symbols[1] == "ETHUSDT"
        assert "SOLUSDT" in stats.top_symbols

    def test_window_filters_old_signals(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Only signals within the window are counted."""
        # Signal from 2 hours ago (within 24h window)
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        store.save_signal(_make_signal(created_at=recent.isoformat()))

        # Signal from 48 hours ago (outside 24h window)
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        store.save_signal(_make_signal(created_at=old.isoformat()))

        stats = engine.summary(window_hours=24)

        assert stats.total_signals == 1


# ---------------------------------------------------------------------------
# Tests: per_symbol_stats()
# ---------------------------------------------------------------------------


class TestPerSymbolStats:
    def test_empty_returns_empty_list(self, engine: StatsEngine) -> None:
        """Requirement 6.5: Empty list when no signals."""
        result = engine.per_symbol_stats(window_hours=24)
        assert result == []

    def test_per_symbol_breakdown(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.2: Per-symbol signal count, avg lag, avg PnL."""
        store.save_signal(_make_signal(symbol="BTCUSDT", actual_lag_ms=100.0, theoretical_pnl_bps=3.0))
        store.save_signal(_make_signal(symbol="BTCUSDT", actual_lag_ms=200.0, theoretical_pnl_bps=1.0))
        store.save_signal(_make_signal(symbol="ETHUSDT", actual_lag_ms=300.0, theoretical_pnl_bps=5.0))

        result = engine.per_symbol_stats(window_hours=24)

        assert len(result) == 2

        btc = next(s for s in result if s.symbol == "BTCUSDT")
        assert btc.total_signals == 2
        assert btc.avg_lag_ms == pytest.approx(150.0)
        assert btc.avg_theoretical_pnl_bps == pytest.approx(2.0)

        eth = next(s for s in result if s.symbol == "ETHUSDT")
        assert eth.total_signals == 1
        assert eth.avg_lag_ms == pytest.approx(300.0)
        assert eth.avg_theoretical_pnl_bps == pytest.approx(5.0)

    def test_counts_by_status(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Per-symbol stats include resolved, expired, active counts."""
        store.save_signal(_make_signal(symbol="BTCUSDT", status=SignalStatus.RESOLVED))
        store.save_signal(_make_signal(symbol="BTCUSDT", status=SignalStatus.EXPIRED, theoretical_pnl_bps=-1.0))
        store.save_signal(_make_signal(
            symbol="BTCUSDT",
            status=SignalStatus.ACTIVE,
            actual_lag_ms=None,
            theoretical_pnl_bps=None,
        ))

        result = engine.per_symbol_stats(window_hours=24)

        btc = result[0]
        assert btc.total_signals == 3
        assert btc.resolved_signals == 1
        assert btc.expired_signals == 1
        assert btc.active_signals == 1

    def test_null_avg_when_no_lag_data(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """avg_lag_ms is None when no signals have actual_lag_ms."""
        store.save_signal(_make_signal(
            status=SignalStatus.ACTIVE,
            actual_lag_ms=None,
            theoretical_pnl_bps=None,
        ))

        result = engine.per_symbol_stats(window_hours=24)

        assert result[0].avg_lag_ms is None
        assert result[0].avg_theoretical_pnl_bps is None


# ---------------------------------------------------------------------------
# Tests: lag_distribution()
# ---------------------------------------------------------------------------


class TestLagDistribution:
    def test_empty_returns_empty_buckets(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.5: Empty buckets list when no signals with lag data."""
        result = engine.lag_distribution()

        assert result.symbol is None
        assert result.buckets == []

    def test_single_bucket(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Single lag value falls into one bucket."""
        store.save_signal(_make_signal(actual_lag_ms=25.0))

        result = engine.lag_distribution()

        assert len(result.buckets) == 1
        assert result.buckets[0] == (0, 50, 1)

    def test_multiple_buckets(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Requirement 6.4: 50ms bucket histogram."""
        store.save_signal(_make_signal(actual_lag_ms=25.0))   # bucket 0-50
        store.save_signal(_make_signal(actual_lag_ms=75.0))   # bucket 50-100
        store.save_signal(_make_signal(actual_lag_ms=80.0))   # bucket 50-100
        store.save_signal(_make_signal(actual_lag_ms=150.0))  # bucket 150-200

        result = engine.lag_distribution()

        # Should have 3 non-empty buckets
        assert len(result.buckets) == 3
        assert (0, 50, 1) in result.buckets
        assert (50, 100, 2) in result.buckets
        assert (150, 200, 1) in result.buckets

    def test_filter_by_symbol(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Distribution can be filtered by symbol."""
        store.save_signal(_make_signal(symbol="BTCUSDT", actual_lag_ms=100.0))
        store.save_signal(_make_signal(symbol="ETHUSDT", actual_lag_ms=200.0))

        result = engine.lag_distribution(symbol="BTCUSDT")

        assert result.symbol == "BTCUSDT"
        assert len(result.buckets) == 1
        assert result.buckets[0] == (100, 150, 1)

    def test_no_lag_data_returns_empty(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Signals without actual_lag_ms are excluded from distribution."""
        store.save_signal(_make_signal(
            status=SignalStatus.ACTIVE,
            actual_lag_ms=None,
            theoretical_pnl_bps=None,
        ))

        result = engine.lag_distribution()

        assert result.buckets == []

    def test_boundary_values(
        self, store: LeadLagStore, engine: StatsEngine
    ) -> None:
        """Lag exactly at bucket boundary goes into the correct bucket."""
        store.save_signal(_make_signal(actual_lag_ms=50.0))   # bucket 50-100
        store.save_signal(_make_signal(actual_lag_ms=100.0))  # bucket 100-150

        result = engine.lag_distribution()

        assert (50, 100, 1) in result.buckets
        assert (100, 150, 1) in result.buckets
