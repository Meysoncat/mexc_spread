"""Tests for P3 polish items: LTTB downsampling, sample variance, funding debounce."""

from __future__ import annotations

import math
import pytest

from mexc_monitor.spread_buffer import (
    SpreadTick,
    _lttb_downsample,
    get_stats,
    push_tick,
    clear,
)


# ─── P3.1: LTTB Downsampling ──────────────────────────────────────────────────


class TestLTTBDownsample:
    def _make_ticks(self, n: int) -> list[SpreadTick]:
        """Create n ticks with a sine-wave spread pattern."""
        ticks = []
        for i in range(n):
            spread_val = 10.0 + 5.0 * math.sin(2 * math.pi * i / 50)
            ticks.append(SpreadTick(
                timestamp_ms=1000 + i * 100,
                bid=100.0, ask=100.0 + spread_val / 10000,
                bid_qty=1.0, ask_qty=1.0,
                mid=100.0 + spread_val / 20000,
                spread_abs=spread_val / 10000,
                spread_bps=spread_val,
            ))
        return ticks

    def test_no_downsample_when_below_threshold(self):
        ticks = self._make_ticks(10)
        result = _lttb_downsample(ticks, 100)
        assert len(result) == 10

    def test_downsample_to_target(self):
        ticks = self._make_ticks(500)
        result = _lttb_downsample(ticks, 50)
        assert len(result) <= 51  # threshold + 1 (first + last + threshold-2)
        assert len(result) >= 49

    def test_preserves_first_and_last(self):
        ticks = self._make_ticks(100)
        result = _lttb_downsample(ticks, 10)
        assert result[0].timestamp_ms == ticks[0].timestamp_ms
        assert result[-1].timestamp_ms == ticks[-1].timestamp_ms

    def test_preserves_peaks_better_than_stride(self):
        """LTTB should keep points near the sine wave peaks."""
        ticks = self._make_ticks(200)
        lttb = _lttb_downsample(ticks, 20)
        # Check that the max spread value is reasonably preserved
        max_original = max(t.spread_bps for t in ticks)
        max_lttb = max(t.spread_bps for t in lttb)
        # LTTB should capture at least 90% of the peak
        assert max_lttb >= max_original * 0.9

    def test_small_threshold_returns_input(self):
        ticks = self._make_ticks(100)
        result = _lttb_downsample(ticks, 2)
        assert result == ticks  # threshold < 3 → no downsample


# ─── P3.3: Sample Variance (÷(n-1)) ──────────────────────────────────────────


class TestSampleVariance:
    def test_std_uses_sample_variance(self):
        """Verify std uses ÷(n-1) not ÷n."""
        from mexc_monitor.spread_buffer import get_history
        clear("TESTSV")
        # Push 3 ticks with different spreads
        push_tick("TESTSV", 100.0, 100.1, 1.0, 1.0)
        push_tick("TESTSV", 100.0, 100.2, 1.0, 1.0)
        push_tick("TESTSV", 100.0, 100.3, 1.0, 1.0)

        stats = get_stats("TESTSV", period_sec=300)
        assert stats is not None

        # Extract actual spread_bps from ticks
        ticks = get_history("TESTSV", max_points=10)
        spreads = [t.spread_bps for t in ticks if t.spread_bps is not None]
        n = len(spreads)
        assert n >= 3
        avg = sum(spreads) / n
        # Sample variance = sum((s-avg)^2) / (n-1)
        expected_var = sum((s - avg) ** 2 for s in spreads) / (n - 1)
        expected_std = math.sqrt(expected_var)
        assert stats.std_spread_bps == pytest.approx(expected_std)
        # Verify it's NOT population std (÷n)
        pop_var = sum((s - avg) ** 2 for s in spreads) / n
        pop_std = math.sqrt(pop_var)
        assert stats.std_spread_bps != pytest.approx(pop_std)

    def test_single_tick_zero_variance(self):
        """With 1 tick, variance = 0 (no division by zero)."""
        clear("TESTST")
        push_tick("TESTST", 100.0, 101.0, 1.0, 1.0)
        stats = get_stats("TESTST", period_sec=300)
        assert stats is not None
        assert stats.std_spread_bps == pytest.approx(0.0)


# ─── P3.4: Funding Direction Change Debounce ─────────────────────────────────


class TestFundingDebounce:
    def test_direction_change_requires_confirmation(self):
        """Direction change should not fire on a single sign flip."""
        from mexc_monitor.futures_arb.funding_tracker import FundingTracker
        from mexc_monitor.futures_arb.models import FuturesArbSettings

        # Use confirmation threshold of 2
        settings = FuturesArbSettings(funding_consecutive_periods_exit=2)
        tracker = FundingTracker(settings)

        key = ("BTC", "mexc_futures")
        # Start positive
        tracker._last_rate[key] = 0.001
        tracker._dir_confirm_count[key] = 0

        # First negative reading — sign flip detected, but not confirmed
        from mexc_monitor.futures_arb.funding_tracker import FundingRateEntry
        entry1 = FundingRateEntry(
            symbol="BTC", exchange="mexc_futures", rate=-0.001,
            timestamp_ms=1000000, next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)
        fi1 = tracker.get_funding("BTC", "mexc_futures")
        assert fi1 is not None
        assert fi1.direction_changed is False  # Not confirmed yet

        # Second negative reading — confirmation threshold met
        entry2 = FundingRateEntry(
            symbol="BTC", exchange="mexc_futures", rate=-0.002,
            timestamp_ms=1000060, next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        fi2 = tracker.get_funding("BTC", "mexc_futures")
        assert fi2 is not None
        assert fi2.direction_changed is True  # Confirmed

    def test_single_sign_flip_not_reported(self):
        """A single sign flip followed by a reversal should not trigger."""
        from mexc_monitor.futures_arb.funding_tracker import FundingTracker
        from mexc_monitor.futures_arb.models import FuturesArbSettings
        from mexc_monitor.futures_arb.funding_tracker import FundingRateEntry

        settings = FuturesArbSettings(funding_consecutive_periods_exit=3)
        tracker = FundingTracker(settings)

        key = ("ETH", "mexc_futures")
        tracker._last_rate[key] = 0.001
        tracker._dir_confirm_count[key] = 0

        # Sign flip to negative
        entry1 = FundingRateEntry(
            symbol="ETH", exchange="mexc_futures", rate=-0.001,
            timestamp_ms=1000000, next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)
        fi1 = tracker.get_funding("ETH", "mexc_futures")
        assert fi1.direction_changed is False

        # Back to positive (reversal before confirmation)
        entry2 = FundingRateEntry(
            symbol="ETH", exchange="mexc_futures", rate=0.002,
            timestamp_ms=1000060, next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        fi2 = tracker.get_funding("ETH", "mexc_futures")
        # Direction change should NOT be reported (wasn't confirmed)
        assert fi2.direction_changed is False
