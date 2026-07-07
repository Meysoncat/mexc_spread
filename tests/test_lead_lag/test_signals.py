"""Unit tests for the SignalGenerator class.

Tests signal generation, resolution, expiry, z-score computation,
and the no-duplicate-active-signal guarantee.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from mexc_monitor.lead_lag.config import LeadLagConfig
from mexc_monitor.lead_lag.detector import LagDetector
from mexc_monitor.lead_lag.models import (
    LagEstimate,
    SignalDirection,
    SignalStatus,
)
from mexc_monitor.lead_lag.price_buffer import PriceBuffer
from mexc_monitor.lead_lag.signals import SignalGenerator


def _make_config(**overrides) -> LeadLagConfig:
    """Create a LeadLagConfig with test-friendly defaults."""
    defaults = dict(
        enabled=True,
        leader_exchange="binance",
        lagger_exchanges=["mexc"],
        symbols=["BTCUSDT"],
        market="futures",
        z_score_entry_threshold=2.0,
        z_score_exit_threshold=0.5,
        signal_timeout_sec=10.0,
        rolling_window_sec=300.0,
        min_spread_bps=3.0,
        lag_estimation_interval_sec=30.0,
        price_buffer_history_sec=60.0,
        assumed_taker_fee_bps=2.0,
    )
    defaults.update(overrides)
    return LeadLagConfig(**defaults)


def _setup_generator(config: LeadLagConfig | None = None) -> tuple[SignalGenerator, PriceBuffer, LagDetector]:
    """Create a SignalGenerator with its dependencies."""
    if config is None:
        config = _make_config()
    price_buffer = PriceBuffer(max_history_sec=config.price_buffer_history_sec)
    lag_detector = LagDetector(config)
    generator = SignalGenerator(config, price_buffer, lag_detector)
    return generator, price_buffer, lag_detector


def _populate_spread_history(
    price_buffer: PriceBuffer,
    generator: SignalGenerator,
    symbol: str = "BTCUSDT",
    leader: str = "binance",
    lagger: str = "mexc",
    base_price: float = 50000.0,
    spread_bps: float = 0.0,
    n_ticks: int = 100,
    start_ms: int | None = None,
    interval_ms: int = 100,
):
    """Populate spread history with consistent spread values.

    This builds up enough history for z-score computation.
    """
    if start_ms is None:
        start_ms = int(time.time() * 1000) - n_ticks * interval_ms

    for i in range(n_ticks):
        ts = start_ms + i * interval_ms
        leader_mid = base_price
        # spread_bps = 10000 * (leader - lagger) / leader
        # lagger = leader * (1 - spread_bps / 10000)
        lagger_mid = leader_mid * (1 - spread_bps / 10000)

        price_buffer.update(leader, symbol, leader_mid, ts)
        price_buffer.update(lagger, symbol, lagger_mid, ts)

    # Run ticks to build spread history
    for _ in range(n_ticks):
        generator.tick()


class TestSignalGeneration:
    """Tests for signal creation conditions."""

    def test_no_signal_when_z_score_below_threshold(self):
        """No signal generated when z-score is below entry threshold."""
        generator, price_buffer, _ = _setup_generator()

        # Populate with consistent spread (z-score will be ~0)
        now_ms = int(time.time() * 1000)
        for i in range(100):
            ts = now_ms - (100 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49990.0, ts)  # ~2 bps spread

        # Tick many times to build history
        for _ in range(100):
            generator.tick()

        # With constant spread, z-score should be 0 → no signal
        assert generator.get_active_signals() == []

    def test_signal_generated_when_conditions_met(self):
        """Signal generated when z-score > threshold AND spread > min_spread_bps."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with small spread (near zero)
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)  # 0 bps spread
            generator.tick()

        # Now introduce a large spread (should trigger signal)
        # Need spread > 3 bps and z-score > 2
        # With history of 0 spread, any non-zero spread will have high z-score
        # if std is small enough
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            # 10 bps spread: lagger = 50000 * (1 - 10/10000) = 49950
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
            result = generator.tick()

        active = generator.get_active_signals()
        assert len(active) == 1
        signal = active[0]
        assert signal.symbol == "BTCUSDT"
        assert signal.leader_exchange == "binance"
        assert signal.lagger_exchange == "mexc"
        assert signal.status == SignalStatus.ACTIVE
        assert signal.direction == SignalDirection.LONG  # leader > lagger
        assert signal.entry_spread_bps > 0

    def test_no_signal_when_spread_below_min(self):
        """No signal when spread is below min_spread_bps even if z-score is high."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            min_spread_bps=50.0,  # Very high minimum
            rolling_window_sec=60.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Introduce 10 bps spread (high z-score but below min_spread_bps=50)
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)  # 10 bps
            generator.tick()

        assert generator.get_active_signals() == []


class TestNoDuplicateActiveSignals:
    """Tests for the no-duplicate guarantee (Requirement 4.5)."""

    def test_no_duplicate_active_signal_for_same_pair(self):
        """Only one ACTIVE signal per (symbol, lagger_exchange)."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
            signal_timeout_sec=60.0,  # Long timeout so signal stays active
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Trigger a signal with large spread
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
            generator.tick()

        assert len(generator.get_active_signals()) == 1

        # Continue with large spread — should NOT create another signal
        for i in range(10):
            ts = now_ms + i * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49940.0, ts)  # Even larger spread
            generator.tick()

        # Still only one active signal
        assert len(generator.get_active_signals()) == 1


class TestSignalResolution:
    """Tests for signal resolution (Requirement 4.3)."""

    def test_signal_resolved_when_z_score_drops(self):
        """Signal transitions to RESOLVED when z-score < exit threshold."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
            signal_timeout_sec=60.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Trigger a signal
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
            generator.tick()

        assert len(generator.get_active_signals()) == 1

        # Now bring spread back to zero (z-score should drop below exit threshold)
        for i in range(20):
            ts = now_ms + i * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Signal should be resolved
        active = generator.get_active_signals()
        assert len(active) == 0

        # Check it's in recent signals as RESOLVED
        recent = generator.get_recent_signals(limit=10)
        resolved = [s for s in recent if s.status == SignalStatus.RESOLVED]
        assert len(resolved) >= 1
        assert resolved[0].resolved_at is not None
        assert resolved[0].exit_spread_bps is not None
        assert resolved[0].actual_lag_ms is not None


class TestSignalExpiry:
    """Tests for signal expiry (Requirement 4.4)."""

    def test_signal_expires_after_timeout(self):
        """Signal transitions to EXPIRED when lifetime > signal_timeout_sec."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
            signal_timeout_sec=1.0,  # 1 second timeout for testing
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread to establish baseline
        for i in range(30):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Trigger a signal with large spread
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)  # 10 bps
            generator.tick()

        active = generator.get_active_signals()
        assert len(active) == 1
        signal_id = active[0].id

        # Wait for timeout without ticking (so no resolution can happen)
        time.sleep(1.2)

        # Now tick once — the signal should expire
        # (a new signal may be created in the same tick since conditions still hold)
        ts = int(time.time() * 1000)
        price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
        price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
        generator.tick()

        # The original signal should be EXPIRED in recent signals
        recent = generator.get_recent_signals(limit=50)
        expired = [s for s in recent if s.status == SignalStatus.EXPIRED and s.id == signal_id]
        assert len(expired) == 1
        assert expired[0].resolved_at is not None
        assert expired[0].exit_spread_bps is not None
        assert expired[0].theoretical_pnl_bps is not None

    def test_resolved_signal_not_expired(self):
        """Already RESOLVED signals are not subject to expiry."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
            signal_timeout_sec=1.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Trigger a signal
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
            generator.tick()

        assert len(generator.get_active_signals()) == 1

        # Resolve it by bringing spread back
        for i in range(20):
            ts = now_ms + i * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        assert len(generator.get_active_signals()) == 0

        # Wait past timeout
        time.sleep(1.1)
        generator.tick()

        # The signal should still be RESOLVED, not EXPIRED
        recent = generator.get_recent_signals(limit=10)
        signal = recent[0]
        assert signal.status == SignalStatus.RESOLVED


class TestTheoreticalPnl:
    """Tests for theoretical PnL computation (Requirement 4.6)."""

    def test_pnl_formula_on_resolved_signal(self):
        """theoretical_pnl_bps = entry_spread - exit_spread - 2 * fee."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            z_score_exit_threshold=0.5,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
            signal_timeout_sec=60.0,
            assumed_taker_fee_bps=2.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Trigger a signal with known spread
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)  # 10 bps
            generator.tick()

        active = generator.get_active_signals()
        assert len(active) == 1
        entry_spread = active[0].entry_spread_bps

        # Resolve by bringing spread to zero
        for i in range(20):
            ts = now_ms + i * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        recent = generator.get_recent_signals(limit=10)
        resolved = [s for s in recent if s.status == SignalStatus.RESOLVED]
        assert len(resolved) >= 1

        signal = resolved[0]
        expected_pnl = signal.entry_spread_bps - signal.exit_spread_bps - 2 * 2.0
        assert signal.theoretical_pnl_bps == pytest.approx(expected_pnl, abs=0.01)


class TestGetRecentSignals:
    """Tests for get_recent_signals method."""

    def test_returns_empty_when_no_signals(self):
        """Returns empty list when no signals have been generated."""
        generator, _, _ = _setup_generator()
        assert generator.get_recent_signals() == []

    def test_respects_limit(self):
        """Returns at most `limit` signals."""
        generator, _, _ = _setup_generator()
        # Manually add signals for testing
        from mexc_monitor.lead_lag.models import LeadLagSignal
        for i in range(10):
            signal = LeadLagSignal(
                id=f"test-{i}",
                symbol="BTCUSDT",
                leader_exchange="binance",
                lagger_exchange="mexc",
                direction=SignalDirection.LONG,
                z_score=2.5,
                entry_spread_bps=5.0,
                leader_mid_at_signal=50000.0,
                lagger_mid_at_signal=49975.0,
                estimated_lag_ms=100.0,
                status=SignalStatus.RESOLVED,
                created_at="2024-01-01T00:00:00+00:00",
            )
            generator._all_signals.append(signal)

        result = generator.get_recent_signals(limit=5)
        assert len(result) == 5


class TestSignalDirection:
    """Tests for signal direction determination."""

    def test_long_when_leader_above_lagger(self):
        """Direction is LONG when leader_mid > lagger_mid."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Leader above lagger
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 49950.0, ts)
            generator.tick()

        active = generator.get_active_signals()
        assert len(active) == 1
        assert active[0].direction == SignalDirection.LONG

    def test_short_when_leader_below_lagger(self):
        """Direction is SHORT when leader_mid < lagger_mid."""
        config = _make_config(
            z_score_entry_threshold=2.0,
            min_spread_bps=3.0,
            rolling_window_sec=60.0,
        )
        generator, price_buffer, _ = _setup_generator(config)

        now_ms = int(time.time() * 1000)

        # Build history with zero spread
        for i in range(50):
            ts = now_ms - (60 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 50000.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        # Leader below lagger (negative spread)
        for i in range(5):
            ts = now_ms - (10 - i) * 100
            price_buffer.update("binance", "BTCUSDT", 49950.0, ts)
            price_buffer.update("mexc", "BTCUSDT", 50000.0, ts)
            generator.tick()

        active = generator.get_active_signals()
        assert len(active) == 1
        assert active[0].direction == SignalDirection.SHORT
