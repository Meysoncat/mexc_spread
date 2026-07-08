"""Unit tests for the LeadLagEngine orchestrator.

Tests engine lifecycle, status transitions (running/degraded/no_leader),
signal generation pausing/resuming, and background thread coordination.

Requirements: 10.1, 10.2, 10.3
"""

from __future__ import annotations

import time
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from mexc_monitor.lead_lag.config import LeadLagConfig
from mexc_monitor.lead_lag.engine import (
    EngineStatus,
    LeadLagEngine,
    _RECOVERY_DATA_SEC,
)
from mexc_monitor.lead_lag.ws_manager import ConnectionStatus


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
        db_path=":memory:",
        assumed_taker_fee_bps=2.0,
        ws_urls={
            "binance_futures": "wss://fstream.binance.com/ws",
            "mexc_futures": "wss://contract.mexc.com/edge",
        },
    )
    defaults.update(overrides)
    return LeadLagConfig(**defaults)


class TestEngineInit:
    """Test engine initialization."""

    def test_initial_status_is_stopped(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        assert engine.status == EngineStatus.STOPPED

    def test_uptime_zero_when_stopped(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        assert engine.uptime_sec == 0.0

    def test_components_accessible(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        assert engine.price_buffer is not None
        assert engine.lag_detector is not None
        assert engine.signal_generator is not None
        assert engine.store is not None
        assert engine.ws_manager is not None
        assert engine.config is config


class TestEngineStartStop:
    """Test engine start/stop lifecycle."""

    def test_start_with_valid_config(self):
        config = _make_config()
        engine = LeadLagEngine(config)

        with patch.object(engine._ws_manager, 'start'):
            with patch.object(engine._store, 'start_retry_loop'):
                result = engine.start()

        assert result is None
        assert engine.status != EngineStatus.STOPPED
        time.sleep(0.01)  # clock resolution: ensure measurable uptime
        assert engine.uptime_sec > 0

        # Clean up
        with patch.object(engine._ws_manager, 'stop'):
            with patch.object(engine._store, 'stop'):
                engine.stop()

    def test_start_idempotent(self):
        config = _make_config()
        engine = LeadLagEngine(config)

        with patch.object(engine._ws_manager, 'start'):
            with patch.object(engine._store, 'start_retry_loop'):
                engine.start()
                # Second start should be no-op
                result = engine.start()

        assert result is None

        with patch.object(engine._ws_manager, 'stop'):
            with patch.object(engine._store, 'stop'):
                engine.stop()

    def test_start_with_invalid_config(self):
        # Invalid: entry threshold <= exit threshold
        config = _make_config(
            z_score_entry_threshold=0.5,
            z_score_exit_threshold=2.0,
        )
        engine = LeadLagEngine(config)
        result = engine.start()

        assert result is not None
        assert "validation failed" in result.lower() or "must be" in result.lower()
        assert engine.status == EngineStatus.STOPPED

    def test_stop_idempotent(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        # Stop when already stopped should be no-op
        engine.stop()
        assert engine.status == EngineStatus.STOPPED

    def test_stop_sets_status_to_stopped(self):
        config = _make_config()
        engine = LeadLagEngine(config)

        with patch.object(engine._ws_manager, 'start'):
            with patch.object(engine._store, 'start_retry_loop'):
                engine.start()

        assert engine.status != EngineStatus.STOPPED

        with patch.object(engine._ws_manager, 'stop'):
            with patch.object(engine._store, 'stop'):
                engine.stop()

        assert engine.status == EngineStatus.STOPPED
        assert engine.uptime_sec == 0.0


class TestEngineStatusTransitions:
    """Test engine status transitions based on connection health."""

    def _make_engine(self) -> LeadLagEngine:
        config = _make_config()
        engine = LeadLagEngine(config)
        # Set to running manually for testing status transitions
        engine._status = EngineStatus.RUNNING
        engine._started_at = time.time()
        return engine

    def test_no_leader_when_binance_disconnected(self):
        """Requirement 10.2: Leader disconnected → no_leader status."""
        engine = self._make_engine()

        # Mock connection status: leader disconnected, lagger connected
        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.NO_LEADER
        assert engine._signals_paused is True

    def test_no_leader_when_binance_stale(self):
        """Requirement 10.2: Leader stale → no_leader status."""
        engine = self._make_engine()

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "stale", "last_message_ms": 0, "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.NO_LEADER
        assert engine._signals_paused is True

    def test_degraded_when_all_laggers_disconnected(self):
        """Requirement 10.1: All laggers disconnected → degraded status."""
        engine = self._make_engine()

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.DEGRADED
        assert engine._signals_paused is True

    def test_degraded_when_all_laggers_stale(self):
        """Requirement 10.1: All laggers stale → degraded status."""
        engine = self._make_engine()

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "stale", "last_message_ms": 0, "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.DEGRADED
        assert engine._signals_paused is True

    def test_running_when_all_healthy(self):
        """All exchanges healthy → running status."""
        engine = self._make_engine()

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.RUNNING
        assert engine._signals_paused is False

    def test_no_leader_takes_priority_over_degraded(self):
        """When both leader and laggers are down, no_leader takes priority."""
        engine = self._make_engine()

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
            "mexc": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.NO_LEADER

    def test_multiple_laggers_one_healthy_is_not_degraded(self):
        """With multiple laggers, if at least one is healthy, not degraded."""
        config = _make_config(lagger_exchanges=["mexc", "bybit"])
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.RUNNING
        engine._started_at = time.time()

        # Rebuild states for the extra exchange
        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
            "bybit": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.RUNNING


class TestEngineRecovery:
    """Test recovery from degraded/no_leader states (Requirement 10.3)."""

    def test_recovery_requires_5_seconds(self):
        """Requirement 10.3: Recovery needs 5 seconds of continuous data."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.DEGRADED
        engine._started_at = time.time()
        engine._signals_paused = True

        # First check: connections restored but not enough time passed
        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        # Should still be degraded (not enough time)
        assert engine.status == EngineStatus.DEGRADED
        assert engine._signals_paused is True

    def test_recovery_after_5_seconds(self):
        """Requirement 10.3: After 5 seconds of continuous data, resume."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.NO_LEADER
        engine._started_at = time.time()
        engine._signals_paused = True

        # Simulate recovery start was 6 seconds ago
        engine._recovery_start = {
            "binance": time.time() - 6.0,
            "mexc": time.time() - 6.0,
        }

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
        }):
            engine._check_and_update_status()

        assert engine.status == EngineStatus.RUNNING
        assert engine._signals_paused is False

    def test_recovery_resets_on_disconnect(self):
        """If exchange disconnects during recovery, reset recovery timer."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.DEGRADED
        engine._started_at = time.time()
        engine._signals_paused = True

        # Start recovery
        engine._recovery_start = {
            "binance": time.time() - 3.0,
            "mexc": time.time() - 3.0,
        }

        # Now mexc disconnects again
        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": int(time.time() * 1000), "discarded_count": 0},
            "mexc": {"status": "disconnected", "last_message_ms": 0, "discarded_count": 0},
        }):
            engine._check_and_update_status()

        # Should remain degraded
        assert engine.status == EngineStatus.DEGRADED
        assert engine._signals_paused is True


class TestEngineSignalPausing:
    """Test that signal generation is properly paused/resumed."""

    def test_signal_tick_skipped_when_paused(self):
        """Signal tick should not generate signals when paused."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.DEGRADED
        engine._started_at = time.time()
        engine._signals_paused = True

        # Mock signal generator tick
        with patch.object(engine._signal_generator, 'tick') as mock_tick:
            engine._run_signal_tick()
            mock_tick.assert_not_called()

    def test_signal_tick_skipped_when_degraded(self):
        """Signal tick should not generate signals in degraded status."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.DEGRADED
        engine._started_at = time.time()
        engine._signals_paused = False  # Even if not explicitly paused

        with patch.object(engine._signal_generator, 'tick') as mock_tick:
            engine._run_signal_tick()
            mock_tick.assert_not_called()

    def test_signal_tick_skipped_when_no_leader(self):
        """Signal tick should not generate signals in no_leader status."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.NO_LEADER
        engine._started_at = time.time()
        engine._signals_paused = False

        with patch.object(engine._signal_generator, 'tick') as mock_tick:
            engine._run_signal_tick()
            mock_tick.assert_not_called()

    def test_signal_tick_runs_when_running(self):
        """Signal tick should generate signals when running."""
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.RUNNING
        engine._started_at = time.time()
        engine._signals_paused = False

        with patch.object(engine._signal_generator, 'tick', return_value=[]) as mock_tick:
            engine._run_signal_tick()
            mock_tick.assert_called_once()


class TestEngineStatusInfo:
    """Test get_status_info method."""

    def test_status_info_when_stopped(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        info = engine.get_status_info()

        assert info["running"] is False
        assert info["status"] == "stopped"
        assert info["connections"] == {}
        assert info["symbols_monitored"] == []
        assert info["active_signals_count"] == 0
        assert info["uptime_sec"] == 0.0

    def test_status_info_when_running(self):
        config = _make_config()
        engine = LeadLagEngine(config)
        engine._status = EngineStatus.RUNNING
        engine._started_at = time.time() - 10.0

        with patch.object(engine._ws_manager, 'connection_status', return_value={
            "binance": {"status": "connected", "last_message_ms": 1234567890, "discarded_count": 0},
            "mexc": {"status": "connected", "last_message_ms": 1234567891, "discarded_count": 0},
        }):
            info = engine.get_status_info()

        assert info["running"] is True
        assert info["status"] == "running"
        assert "binance" in info["connections"]
        assert info["connections"]["binance"]["connected"] is True
        assert info["symbols_monitored"] == ["BTCUSDT"]
        assert info["uptime_sec"] >= 10.0


class TestEngineLagEstimation:
    """Test lag estimation background loop."""

    def test_lag_estimation_runs_for_all_symbols(self):
        config = _make_config(symbols=["BTCUSDT", "ETHUSDT"])
        engine = LeadLagEngine(config)

        with patch.object(engine._lag_detector, 'update_estimate') as mock_update:
            engine._run_lag_estimation()

        assert mock_update.call_count == 2
        calls = [call.args[0] for call in mock_update.call_args_list]
        assert "BTCUSDT" in calls
        assert "ETHUSDT" in calls
