"""
Unit tests for FundingTracker class.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from unittest.mock import patch, MagicMock

import pytest

from mexc_monitor.futures_arb.funding_tracker import (
    FundingTracker,
    FundingRateEntry,
    MEXC_FUNDING_INTERVAL_HOURS,
    ASTERDEX_FUNDING_INTERVAL_HOURS,
    _MAX_HISTORY_ENTRIES,
)
from mexc_monitor.futures_arb.models import FundingInfo, FuturesArbSettings


@pytest.fixture
def default_settings() -> FuturesArbSettings:
    """Default settings for testing."""
    return FuturesArbSettings(
        symbols=["BTCUSDT", "ETHUSDT"],
        exchange_combos=["mexc_spot+mexc_futures", "mexc_spot+asterdex_perp"],
    )


@pytest.fixture
def tracker(default_settings: FuturesArbSettings) -> FundingTracker:
    """Create a FundingTracker instance for testing."""
    return FundingTracker(default_settings, poll_interval_sec=60.0)


class TestFundingTrackerInit:
    """Tests for FundingTracker initialization."""

    def test_initial_state(self, tracker: FundingTracker):
        """Tracker should start with empty state."""
        assert tracker.get_all_funding() == []
        assert tracker.get_funding("BTCUSDT", "mexc_futures") is None

    def test_not_running_initially(self, tracker: FundingTracker):
        """Tracker should not be running initially."""
        assert tracker._running is False


class TestFundingTrackerProcessEntry:
    """Tests for _process_entry logic (core computation)."""

    def test_single_entry_stored(self, tracker: FundingTracker):
        """A single entry should be stored and retrievable."""
        entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=1000000000000,
            next_funding_time_ms=1000000028800000,
        )
        tracker._process_entry(entry)

        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        assert info.symbol == "BTCUSDT"
        assert info.exchange == "mexc_futures"
        assert info.current_rate == 0.0001

    def test_annualized_yield_computation(self, tracker: FundingTracker):
        """Annualized yield should be rate * (365*24/interval_hours) * 100."""
        entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=1000000000000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry)

        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        # 0.0001 * (365*24/8) * 100 = 0.0001 * 1095 * 100 = 10.95
        expected = 0.0001 * (365 * 24 / MEXC_FUNDING_INTERVAL_HOURS) * 100
        assert abs(info.annualized_yield - expected) < 1e-10

    def test_annualized_yield_asterdex(self, tracker: FundingTracker):
        """Annualized yield for AsterDEX should use its interval."""
        entry = FundingRateEntry(
            symbol="ETHUSDT",
            exchange="asterdex_perp",
            rate=0.0005,
            timestamp_ms=1000000000000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry)

        info = tracker.get_funding("ETHUSDT", "asterdex_perp")
        assert info is not None
        expected = 0.0005 * (365 * 24 / ASTERDEX_FUNDING_INTERVAL_HOURS) * 100
        assert abs(info.annualized_yield - expected) < 1e-10

    def test_avg_7d_single_entry(self, tracker: FundingTracker):
        """With a single entry, avg_7d should equal that entry's rate."""
        entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0003,
            timestamp_ms=1000000000000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry)

        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        assert info.avg_7d == 0.0003
        assert info.avg_30d == 0.0003

    def test_avg_7d_multiple_entries(self, tracker: FundingTracker):
        """avg_7d should be the mean of entries within last 7 days."""
        now_ms = int(time.time() * 1000)
        rates = [0.0001, 0.0002, 0.0003, 0.0004, 0.0005]

        for i, rate in enumerate(rates):
            entry = FundingRateEntry(
                symbol="BTCUSDT",
                exchange="mexc_futures",
                rate=rate,
                timestamp_ms=now_ms + i * 60000,  # 1 minute apart
                next_funding_time_ms=0,
            )
            tracker._process_entry(entry)

        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        expected_avg = sum(rates) / len(rates)
        assert abs(info.avg_7d - expected_avg) < 1e-10

    def test_avg_7d_excludes_old_entries(self, tracker: FundingTracker):
        """avg_7d should exclude entries older than 7 days."""
        now_ms = int(time.time() * 1000)
        eight_days_ago_ms = now_ms - (8 * 24 * 3600 * 1000)

        # Old entry (8 days ago)
        old_entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.001,
            timestamp_ms=eight_days_ago_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(old_entry)

        # Recent entry (now)
        new_entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0002,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(new_entry)

        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        # avg_7d should only include the recent entry
        assert info.avg_7d == 0.0002
        # avg_30d should include both
        expected_30d = (0.001 + 0.0002) / 2
        assert abs(info.avg_30d - expected_30d) < 1e-10

    def test_direction_changed_positive_to_negative(self, tracker: FundingTracker):
        """Direction change should be detected when rate goes from positive to negative.

        With debounce (confirmation threshold = 3), need 3 consecutive negative
        readings after the sign flip to confirm.
        """
        now_ms = int(time.time() * 1000)

        # First entry: positive
        entry1 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)
        info1 = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info1 is not None
        assert info1.direction_changed is False

        # Sign flip to negative — starts confirmation counter
        entry2 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0002,
            timestamp_ms=now_ms + 60000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        info2 = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info2.direction_changed is False  # Not confirmed yet

        # Second consecutive negative
        entry3 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0003,
            timestamp_ms=now_ms + 120000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry3)

        # Third consecutive negative — confirmation threshold met
        entry4 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0004,
            timestamp_ms=now_ms + 180000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry4)
        info4 = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info4 is not None
        assert info4.direction_changed is True

    def test_direction_changed_negative_to_positive(self, tracker: FundingTracker):
        """Direction change should be detected when rate goes from negative to positive.

        With debounce (confirmation threshold = 3), need 3 consecutive positive
        readings after the sign flip to confirm.
        """
        now_ms = int(time.time() * 1000)

        entry1 = FundingRateEntry(
            symbol="ETHUSDT",
            exchange="asterdex_perp",
            rate=-0.0003,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)

        # Sign flip to positive
        entry2 = FundingRateEntry(
            symbol="ETHUSDT",
            exchange="asterdex_perp",
            rate=0.0001,
            timestamp_ms=now_ms + 60000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        info2 = tracker.get_funding("ETHUSDT", "asterdex_perp")
        assert info2.direction_changed is False  # Not confirmed yet

        # Second consecutive positive
        entry3 = FundingRateEntry(
            symbol="ETHUSDT",
            exchange="asterdex_perp",
            rate=0.0002,
            timestamp_ms=now_ms + 120000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry3)

        # Third consecutive positive — confirmed
        entry4 = FundingRateEntry(
            symbol="ETHUSDT",
            exchange="asterdex_perp",
            rate=0.0003,
            timestamp_ms=now_ms + 180000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry4)
        info4 = tracker.get_funding("ETHUSDT", "asterdex_perp")
        assert info4 is not None
        assert info4.direction_changed is True
        info = tracker.get_funding("ETHUSDT", "asterdex_perp")
        assert info is not None
        assert info.direction_changed is True

    def test_no_direction_change_same_sign(self, tracker: FundingTracker):
        """No direction change when rate stays positive."""
        now_ms = int(time.time() * 1000)

        entry1 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)

        entry2 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0005,
            timestamp_ms=now_ms + 60000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        assert info.direction_changed is False

    def test_no_direction_change_from_zero(self, tracker: FundingTracker):
        """No direction change when previous rate was zero."""
        now_ms = int(time.time() * 1000)

        entry1 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)

        entry2 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=now_ms + 60000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        info = tracker.get_funding("BTCUSDT", "mexc_futures")
        assert info is not None
        assert info.direction_changed is False

    def test_direction_changed_callback_fired(self, default_settings: FuturesArbSettings):
        """Callback should be fired when direction changes (after confirmation)."""
        callback_calls: list[FundingInfo] = []

        tracker = FundingTracker(
            default_settings,
            on_direction_changed=lambda info: callback_calls.append(info),
        )

        now_ms = int(time.time() * 1000)

        # Positive entry
        entry1 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0001,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry1)
        assert len(callback_calls) == 0

        # Sign flip — starts confirmation, not yet confirmed
        entry2 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0001,
            timestamp_ms=now_ms + 60000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry2)
        assert len(callback_calls) == 0  # Not confirmed yet

        # Second consecutive negative
        entry3 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0002,
            timestamp_ms=now_ms + 120000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry3)

        # Third consecutive negative — confirmed, callback fires
        entry4 = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=-0.0003,
            timestamp_ms=now_ms + 180000,
            next_funding_time_ms=0,
        )
        tracker._process_entry(entry4)
        assert len(callback_calls) == 1
        assert callback_calls[0].direction_changed is True

    def test_history_pruned_after_30_days(self, tracker: FundingTracker):
        """Entries older than 30 days should be pruned from history."""
        now_ms = int(time.time() * 1000)
        thirty_one_days_ago_ms = now_ms - (31 * 24 * 3600 * 1000)

        # Add old entry
        old_entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.001,
            timestamp_ms=thirty_one_days_ago_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(old_entry)

        # Add new entry (triggers pruning)
        new_entry = FundingRateEntry(
            symbol="BTCUSDT",
            exchange="mexc_futures",
            rate=0.0002,
            timestamp_ms=now_ms,
            next_funding_time_ms=0,
        )
        tracker._process_entry(new_entry)

        # History should only contain the new entry
        key = ("BTCUSDT", "mexc_futures")
        assert len(tracker._history[key]) == 1
        assert tracker._history[key][0].rate == 0.0002


class TestFundingTrackerGetAllFunding:
    """Tests for get_all_funding()."""

    def test_returns_all_tracked(self, tracker: FundingTracker):
        """get_all_funding should return info for all tracked symbols."""
        now_ms = int(time.time() * 1000)

        entries = [
            FundingRateEntry("BTCUSDT", "mexc_futures", 0.0001, now_ms, 0),
            FundingRateEntry("ETHUSDT", "mexc_futures", 0.0002, now_ms, 0),
            FundingRateEntry("BTCUSDT", "asterdex_perp", 0.0003, now_ms, 0),
        ]
        for entry in entries:
            tracker._process_entry(entry)

        all_funding = tracker.get_all_funding()
        assert len(all_funding) == 3
        symbols_exchanges = {(f.symbol, f.exchange) for f in all_funding}
        assert ("BTCUSDT", "mexc_futures") in symbols_exchanges
        assert ("ETHUSDT", "mexc_futures") in symbols_exchanges
        assert ("BTCUSDT", "asterdex_perp") in symbols_exchanges


class TestFundingTrackerStartStop:
    """Tests for start/stop lifecycle."""

    def test_start_and_stop(self, tracker: FundingTracker):
        """Tracker should start and stop cleanly."""
        with patch.object(tracker, "_poll_once"):
            tracker.start()
            assert tracker._running is True
            assert tracker._thread is not None
            assert tracker._thread.is_alive()

            tracker.stop()
            assert tracker._running is False

    def test_double_start_is_safe(self, tracker: FundingTracker):
        """Calling start() twice should not create duplicate threads."""
        with patch.object(tracker, "_poll_once"):
            tracker.start()
            thread1 = tracker._thread
            tracker.start()  # Should warn and not create new thread
            assert tracker._thread is thread1
            tracker.stop()

    def test_stop_without_start_is_safe(self, tracker: FundingTracker):
        """Calling stop() without start() should not raise."""
        tracker.stop()  # Should be a no-op


class TestFundingTrackerPollOnce:
    """Tests for _poll_once logic."""

    def test_polls_correct_exchanges(self, default_settings: FuturesArbSettings):
        """_poll_once should poll exchanges from configured combos."""
        tracker = FundingTracker(default_settings)

        with patch.object(tracker, "_fetch_funding_rate") as mock_fetch:
            mock_fetch.return_value = None
            tracker._poll_once()

            # Should have been called for each symbol × exchange
            # Combos: mexc_spot+mexc_futures, mexc_spot+asterdex_perp
            # Exchanges to poll: mexc_futures, asterdex_perp
            # Symbols: BTCUSDT, ETHUSDT
            # Total: 2 symbols × 2 exchanges = 4 calls
            assert mock_fetch.call_count == 4

    def test_polls_only_futures_exchanges(self):
        """Should not poll spot exchanges."""
        settings = FuturesArbSettings(
            symbols=["BTCUSDT"],
            exchange_combos=["mexc_spot+mexc_futures"],
        )
        tracker = FundingTracker(settings)

        with patch.object(tracker, "_fetch_funding_rate") as mock_fetch:
            mock_fetch.return_value = None
            tracker._poll_once()

            # Only mexc_futures should be polled (not mexc_spot)
            assert mock_fetch.call_count == 1
            call_args = mock_fetch.call_args_list[0]
            assert call_args[0][1] == "mexc_futures"
