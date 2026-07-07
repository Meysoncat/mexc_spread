"""
Unit tests for BasisCalculator.

Tests cover:
- Pure basis computation formulas (compute_basis_snapshot)
- Spread buffer key resolution
- Stale status detection
- BasisCalculator lifecycle (start/stop/subscribe)
- All three exchange combos
"""

from __future__ import annotations

import time
import threading
from unittest.mock import patch, MagicMock

import pytest

from mexc_monitor.futures_arb.basis_calculator import (
    BasisCalculator,
    compute_basis_snapshot,
    _spread_buffer_key,
    _LegData,
)
from mexc_monitor.futures_arb.models import BasisSnapshot, FuturesArbSettings


class TestSpreadBufferKey:
    """Tests for _spread_buffer_key resolution."""

    def test_mexc_spot_key(self):
        assert _spread_buffer_key("mexc_spot", "BTCUSDT") == "BTCUSDT"
        assert _spread_buffer_key("mexc_spot", "ethusdt") == "ETHUSDT"

    def test_mexc_futures_key(self):
        assert _spread_buffer_key("mexc_futures", "BTCUSDT") == "BTC_USDT"
        assert _spread_buffer_key("mexc_futures", "ETHUSDT") == "ETH_USDT"
        assert _spread_buffer_key("mexc_futures", "SOLUSDT") == "SOL_USDT"

    def test_asterdex_perp_key(self):
        assert _spread_buffer_key("asterdex_perp", "BTCUSDT") == "ASTER:BTCUSDT"
        assert _spread_buffer_key("asterdex_perp", "ethusdt") == "ASTER:ETHUSDT"

    def test_unknown_exchange_raises(self):
        with pytest.raises(ValueError, match="Unknown exchange"):
            _spread_buffer_key("binance", "BTCUSDT")


class TestComputeBasisSnapshot:
    """Tests for the pure compute_basis_snapshot function."""

    def test_basic_computation(self):
        """Test basic basis computation with known values."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=50000.0,
            spot_ask=50010.0,
            futures_bid=50100.0,
            futures_ask=50110.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            timestamp_ms=1000000,
        )

        # spot_mid = (50000 + 50010) / 2 = 50005
        assert snap.spot_mid == pytest.approx(50005.0)
        # futures_mid = (50100 + 50110) / 2 = 50105
        assert snap.futures_mid == pytest.approx(50105.0)
        # basis_abs = 50105 - 50005 = 100
        assert snap.basis_abs == pytest.approx(100.0)
        # basis_bps = 10000 * 100 / 50005 ≈ 19.998
        assert snap.basis_bps == pytest.approx(10000.0 * 100.0 / 50005.0)
        # executable_cc_bps = (50100 - 50010) / 50005 * 10000 - 3 = 17.998...
        expected_cc = (50100.0 - 50010.0) / 50005.0 * 10000.0 - 3.0
        assert snap.executable_basis_cc_bps == pytest.approx(expected_cc)
        # executable_rcc_bps = (50000 - 50110) / 50005 * 10000 - 3 = -24.998...
        expected_rcc = (50000.0 - 50110.0) / 50005.0 * 10000.0 - 3.0
        assert snap.executable_basis_rcc_bps == pytest.approx(expected_rcc)
        # estimated_apy uses executable basis (after fees) minus exit fees:
        # realistic_pnl = max(executable_cc, executable_rcc) - exit_fees
        # apy = (realistic_pnl / 10000) * (365 * 24 / hold_hours) * 100
        best_exec = max(snap.executable_basis_cc_bps, snap.executable_basis_rcc_bps)
        exit_fees = 1.0 + 2.0
        realistic_pnl = best_exec - exit_fees
        expected_apy = (realistic_pnl / 10000.0) * (365.0 * 24.0 / 24.0) * 100.0
        assert snap.estimated_apy == pytest.approx(expected_apy)

    def test_negative_basis(self):
        """Test when futures trades below spot (backwardation)."""
        snap = compute_basis_snapshot(
            symbol="ETHUSDT",
            exchange_combo="mexc_spot+asterdex_perp",
            spot_bid=3000.0,
            spot_ask=3001.0,
            futures_bid=2990.0,
            futures_ask=2991.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            timestamp_ms=2000000,
        )

        assert snap.basis_abs < 0
        assert snap.basis_bps < 0
        # With the corrected APY formula, negative basis (backwardation) can
        # still yield positive APY via reverse cash-and-carry (RCC).
        # The RCC executable basis is positive here, so APY should be positive.
        assert snap.executable_basis_rcc_bps > 0
        assert snap.estimated_apy > 0

    def test_zero_spot_mid_protection(self):
        """When spot_mid is 0, should not divide by zero."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=0.0,
            spot_ask=0.0,
            futures_bid=100.0,
            futures_ask=101.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            timestamp_ms=3000000,
        )

        assert snap.basis_bps == 0.0
        assert snap.executable_basis_cc_bps == 0.0
        assert snap.executable_basis_rcc_bps == 0.0

    def test_zero_expected_hold_hours(self):
        """When expected_hold_hours is 0, APY should be 0 (no division by zero)."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=50000.0,
            spot_ask=50010.0,
            futures_bid=50100.0,
            futures_ask=50110.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=0.0,
            timestamp_ms=4000000,
        )

        assert snap.estimated_apy == 0.0

    def test_status_preserved(self):
        """Status should be set as provided."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=50000.0,
            spot_ask=50010.0,
            futures_bid=50100.0,
            futures_ask=50110.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            status="stale",
            timestamp_ms=5000000,
        )

        assert snap.status == "stale"

    def test_funding_rate_passed_through(self):
        """Funding rate should be stored in snapshot."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=50000.0,
            spot_ask=50010.0,
            futures_bid=50100.0,
            futures_ask=50110.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            funding_rate=0.0005,
            timestamp_ms=6000000,
        )

        assert snap.funding_rate == 0.0005

    def test_equal_spot_and_futures(self):
        """When spot == futures, basis should be 0."""
        snap = compute_basis_snapshot(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            spot_bid=50000.0,
            spot_ask=50000.0,
            futures_bid=50000.0,
            futures_ask=50000.0,
            spot_fee_bps=1.0,
            futures_fee_bps=2.0,
            expected_hold_hours=24.0,
            timestamp_ms=7000000,
        )

        assert snap.basis_abs == 0.0
        assert snap.basis_bps == 0.0
        # executable_cc_bps = (50000 - 50000) / 50000 * 10000 - 3 = -3
        assert snap.executable_basis_cc_bps == pytest.approx(-3.0)
        # executable_rcc_bps = (50000 - 50000) / 50000 * 10000 - 3 = -3
        assert snap.executable_basis_rcc_bps == pytest.approx(-3.0)


class TestBasisCalculatorLifecycle:
    """Tests for BasisCalculator start/stop and subscription management."""

    def _make_settings(self, **kwargs) -> FuturesArbSettings:
        defaults = {
            "symbols": ["BTCUSDT"],
            "exchange_combos": ["mexc_spot+mexc_futures"],
            "spot_taker_fee_bps": 1.0,
            "futures_taker_fee_bps": 2.0,
            "expected_hold_hours": 24.0,
        }
        defaults.update(kwargs)
        return FuturesArbSettings(**defaults)

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_start_subscribes_to_both_legs(self, mock_unsub, mock_sub):
        """start() should subscribe to spread buffer for both spot and futures keys."""
        settings = self._make_settings()
        calc = BasisCalculator(settings)
        calc.start()

        # Should subscribe to BTCUSDT (spot) and BTC_USDT (futures)
        assert mock_sub.call_count == 2
        keys_subscribed = {call.args[0] for call in mock_sub.call_args_list}
        assert "BTCUSDT" in keys_subscribed
        assert "BTC_USDT" in keys_subscribed

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_stop_unsubscribes(self, mock_unsub, mock_sub):
        """stop() should unsubscribe from all spread buffer keys."""
        settings = self._make_settings()
        calc = BasisCalculator(settings)
        calc.start()
        calc.stop()

        assert mock_unsub.call_count == 2

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_multiple_combos(self, mock_unsub, mock_sub):
        """Should subscribe to all configured exchange combos."""
        settings = self._make_settings(
            exchange_combos=["mexc_spot+mexc_futures", "mexc_spot+asterdex_perp"]
        )
        calc = BasisCalculator(settings)
        calc.start()

        # 2 combos × 2 legs = 4 subscriptions
        # But mexc_spot BTCUSDT appears in both combos, still 4 subscriptions
        assert mock_sub.call_count == 4
        keys_subscribed = {call.args[0] for call in mock_sub.call_args_list}
        assert "BTCUSDT" in keys_subscribed
        assert "BTC_USDT" in keys_subscribed
        assert "ASTER:BTCUSDT" in keys_subscribed

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_multiple_symbols(self, mock_unsub, mock_sub):
        """Should subscribe for each symbol × combo combination."""
        settings = self._make_settings(symbols=["BTCUSDT", "ETHUSDT"])
        calc = BasisCalculator(settings)
        calc.start()

        # 2 symbols × 1 combo × 2 legs = 4 subscriptions
        assert mock_sub.call_count == 4

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_start_idempotent(self, mock_unsub, mock_sub):
        """Calling start() twice should not double-subscribe."""
        settings = self._make_settings()
        calc = BasisCalculator(settings)
        calc.start()
        calc.start()

        assert mock_sub.call_count == 2  # Only first start subscribes


class TestBasisCalculatorComputation:
    """Tests for BasisCalculator basis computation via tick callbacks."""

    def _make_calculator(self, **kwargs) -> BasisCalculator:
        settings = FuturesArbSettings(
            symbols=["BTCUSDT"],
            exchange_combos=["mexc_spot+mexc_futures"],
            spot_taker_fee_bps=1.0,
            futures_taker_fee_bps=2.0,
            expected_hold_hours=24.0,
            **kwargs,
        )
        return BasisCalculator(settings, stale_after_sec=30.0)

    def _make_tick(self, bid: float, ask: float, ts_ms: int | None = None):
        """Create a mock tick object."""
        tick = MagicMock()
        tick.bid = bid
        tick.ask = ask
        tick.mid = (bid + ask) / 2.0
        tick.timestamp_ms = ts_ms or int(time.time() * 1000)
        return tick

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_basis_computed_after_both_legs_update(self, mock_unsub, mock_sub):
        """Basis should be computed once both legs have data."""
        calc = self._make_calculator()
        calc.start()

        now_ms = int(time.time() * 1000)

        # Simulate spot tick
        spot_tick = self._make_tick(50000.0, 50010.0, now_ms)
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot", spot_tick)

        # Only one leg — no snapshot yet (futures has no data)
        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+mexc_futures")
        assert snap is None

        # Simulate futures tick
        futures_tick = self._make_tick(50100.0, 50110.0, now_ms)
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "futures", futures_tick)

        # Now both legs have data — snapshot should exist
        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+mexc_futures")
        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert snap.exchange_combo == "mexc_spot+mexc_futures"
        assert snap.spot_mid == pytest.approx(50005.0)
        assert snap.futures_mid == pytest.approx(50105.0)
        assert snap.basis_abs == pytest.approx(100.0)
        assert snap.status == "active"

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_stale_when_leg_too_old(self, mock_unsub, mock_sub):
        """Pair should be marked stale when one leg is older than stale_after_sec."""
        calc = self._make_calculator()
        calc.start()

        now_ms = int(time.time() * 1000)
        old_ms = now_ms - 60_000  # 60 seconds ago (> 30s stale threshold)

        # Spot is fresh, futures is old
        spot_tick = self._make_tick(50000.0, 50010.0, now_ms)
        futures_tick = self._make_tick(50100.0, 50110.0, old_ms)

        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot", spot_tick)
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "futures", futures_tick)

        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+mexc_futures")
        assert snap is not None
        assert snap.status == "stale"

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_active_when_both_legs_fresh(self, mock_unsub, mock_sub):
        """Pair should be active when both legs are fresh."""
        calc = self._make_calculator()
        calc.start()

        now_ms = int(time.time() * 1000)

        spot_tick = self._make_tick(50000.0, 50010.0, now_ms)
        futures_tick = self._make_tick(50100.0, 50110.0, now_ms)

        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot", spot_tick)
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "futures", futures_tick)

        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+mexc_futures")
        assert snap is not None
        assert snap.status == "active"

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_get_all_basis(self, mock_unsub, mock_sub):
        """get_all_basis() should return snapshots for all configured pairs."""
        settings = FuturesArbSettings(
            symbols=["BTCUSDT", "ETHUSDT"],
            exchange_combos=["mexc_spot+mexc_futures"],
            spot_taker_fee_bps=1.0,
            futures_taker_fee_bps=2.0,
            expected_hold_hours=24.0,
        )
        calc = BasisCalculator(settings, stale_after_sec=30.0)
        calc.start()

        now_ms = int(time.time() * 1000)

        # Feed data for BTCUSDT
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot",
                           self._make_tick(50000.0, 50010.0, now_ms))
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "futures",
                           self._make_tick(50100.0, 50110.0, now_ms))

        # Feed data for ETHUSDT
        calc._on_leg_update("ETHUSDT", "mexc_spot+mexc_futures", "spot",
                           self._make_tick(3000.0, 3001.0, now_ms))
        calc._on_leg_update("ETHUSDT", "mexc_spot+mexc_futures", "futures",
                           self._make_tick(3010.0, 3011.0, now_ms))

        all_basis = calc.get_all_basis()
        assert len(all_basis) == 2
        symbols = {s.symbol for s in all_basis}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_asterdex_perp_combo(self, mock_unsub, mock_sub):
        """Test mexc_spot+asterdex_perp combo works correctly."""
        settings = FuturesArbSettings(
            symbols=["BTCUSDT"],
            exchange_combos=["mexc_spot+asterdex_perp"],
            spot_taker_fee_bps=1.0,
            futures_taker_fee_bps=2.0,
            expected_hold_hours=24.0,
        )
        calc = BasisCalculator(settings, stale_after_sec=30.0)
        calc.start()

        now_ms = int(time.time() * 1000)

        calc._on_leg_update("BTCUSDT", "mexc_spot+asterdex_perp", "spot",
                           self._make_tick(50000.0, 50010.0, now_ms))
        calc._on_leg_update("BTCUSDT", "mexc_spot+asterdex_perp", "futures",
                           self._make_tick(50200.0, 50210.0, now_ms))

        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+asterdex_perp")
        assert snap is not None
        assert snap.exchange_combo == "mexc_spot+asterdex_perp"
        assert snap.basis_abs > 0

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_asterdex_mexc_futures_combo(self, mock_unsub, mock_sub):
        """Test asterdex_perp+mexc_futures combo works correctly."""
        settings = FuturesArbSettings(
            symbols=["BTCUSDT"],
            exchange_combos=["asterdex_perp+mexc_futures"],
            spot_taker_fee_bps=1.0,
            futures_taker_fee_bps=2.0,
            expected_hold_hours=24.0,
        )
        calc = BasisCalculator(settings, stale_after_sec=30.0)
        calc.start()

        now_ms = int(time.time() * 1000)

        calc._on_leg_update("BTCUSDT", "asterdex_perp+mexc_futures", "spot",
                           self._make_tick(50000.0, 50010.0, now_ms))
        calc._on_leg_update("BTCUSDT", "asterdex_perp+mexc_futures", "futures",
                           self._make_tick(50300.0, 50310.0, now_ms))

        snap = calc.get_current_basis("BTCUSDT", "asterdex_perp+mexc_futures")
        assert snap is not None
        assert snap.exchange_combo == "asterdex_perp+mexc_futures"
        assert snap.basis_abs > 0

    @patch("mexc_monitor.spread_buffer.subscribe")
    @patch("mexc_monitor.spread_buffer.unsubscribe")
    def test_callbacks_ignored_after_stop(self, mock_unsub, mock_sub):
        """Tick callbacks should be ignored after stop()."""
        calc = self._make_calculator()
        calc.start()

        now_ms = int(time.time() * 1000)

        # Feed initial data
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot",
                           self._make_tick(50000.0, 50010.0, now_ms))
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "futures",
                           self._make_tick(50100.0, 50110.0, now_ms))

        calc.stop()

        # Feed new data after stop — should be ignored
        calc._on_leg_update("BTCUSDT", "mexc_spot+mexc_futures", "spot",
                           self._make_tick(60000.0, 60010.0, now_ms))

        # Snapshot should still have old data
        snap = calc.get_current_basis("BTCUSDT", "mexc_spot+mexc_futures")
        assert snap is not None
        assert snap.spot_mid == pytest.approx(50005.0)
